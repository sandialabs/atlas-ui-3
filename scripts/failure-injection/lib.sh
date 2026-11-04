#!/bin/bash
# Shared helpers for the Atlas failure-injection exercises (issue #1129).
# Sourced by delete-pod.sh, dependency-outage.sh and rollout.sh; not run directly.
#
# Every exercise:
#   1. snapshots /api/health (and /admin/coordination when an admin identity is
#      given) on every Atlas pod, so the "before" state is recorded per replica;
#   2. streams the [COORD] log lines of every Atlas pod into the output dir;
#   3. injects the failure, observes, restores, observes again;
#   4. prints where the evidence is. It never decides pass/fail for you: compare
#      what you see with docs/admin/multi-replica-observability.md.
#
# Environment (all optional):
#   KUBECTL             kubectl or oc                      (default: kubectl)
#   ATLAS_NAMESPACE     namespace                          (or --namespace)
#   ATLAS_SELECTOR      label selector for API pods        (default: app=atlas)
#   ATLAS_CONTAINER     container name inside the pod      (default: first container)
#   ATLAS_PORT          port the API listens on in the pod (default: 8000)
#   ATLAS_ADMIN_EMAIL   identity sent as X-User-Email for /admin/coordination
#   ATLAS_AUTH_HEADER   header that carries it             (default: X-User-Email)
#   ATLAS_PROXY_SECRET  proxy secret, when FEATURE_PROXY_SECRET_ENABLED (sent over stdin, never argv)
#   ATLAS_PROXY_SECRET_HEADER  its header name                (default: X-Proxy-Secret)
#   OUT_DIR             where evidence is written          (default: $TMPDIR/atlas-failure-<name>-<ts>)

set -euo pipefail

KUBECTL="${KUBECTL:-kubectl}"
ATLAS_SELECTOR="${ATLAS_SELECTOR:-app=atlas}"
ATLAS_PORT="${ATLAS_PORT:-8000}"
ATLAS_AUTH_HEADER="${ATLAS_AUTH_HEADER:-X-User-Email}"
NAMESPACE="${ATLAS_NAMESPACE:-}"
DRY_RUN=0
ASSUME_YES=0
LOG_PIDS=()

log() { printf '[%s] %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# Parse the flags every exercise accepts. Unrecognized flags are left in
# REMAINING_ARGS for the caller.
parse_common_flags() {
    REMAINING_ARGS=()
    while [ $# -gt 0 ]; do
        case "$1" in
            -n|--namespace) NAMESPACE="$2"; shift 2 ;;
            --selector) ATLAS_SELECTOR="$2"; shift 2 ;;
            --out) OUT_DIR="$2"; shift 2 ;;
            --dry-run) DRY_RUN=1; shift ;;
            -y|--yes) ASSUME_YES=1; shift ;;
            *) REMAINING_ARGS+=("$1"); shift ;;
        esac
    done
}

kc() {
    # kubectl/oc with the namespace applied.
    if [ -n "$NAMESPACE" ]; then
        "$KUBECTL" -n "$NAMESPACE" "$@"
    else
        "$KUBECTL" "$@"
    fi
}

# Run a mutating command, or print it under --dry-run.
act() {
    local shown="$*"
    if [ "${1:-}" = kc ]; then
        shown="$KUBECTL${NAMESPACE:+ -n $NAMESPACE} ${*:2}"
    fi
    if [ "$DRY_RUN" = 1 ]; then
        printf 'DRY-RUN: %s\n' "$shown"
        return 0
    fi
    log "+ $shown"
    "$@"
}

require_target() {
    [ -n "$NAMESPACE" ] || die "set --namespace (or ATLAS_NAMESPACE); exercises never guess a namespace"
    command -v "$KUBECTL" > /dev/null || die "$KUBECTL not found (set KUBECTL=oc for OpenShift)"
}

confirm() {
    # Interactive confirmation for anything destructive, unless --yes/--dry-run.
    # The operator types the target's name (default: the namespace).
    local token="${2:-$NAMESPACE}"
    [ "$DRY_RUN" = 1 ] && return 0
    [ "$ASSUME_YES" = 1 ] && return 0
    printf '%s\nType %s to continue: ' "$1" "$token" >&2
    local answer
    read -r answer
    [ "$answer" = "$token" ] || die "not confirmed"
}

init_out_dir() {
    local name="$1"
    OUT_DIR="${OUT_DIR:-${TMPDIR:-/tmp}/atlas-failure-${name}-$(date -u +%Y%m%dT%H%M%SZ)}"
    mkdir -p "$OUT_DIR"
    log "evidence directory: $OUT_DIR"
}

atlas_pods() {
    kc get pods -l "$ATLAS_SELECTOR" -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
}

_container_flag() {
    if [ -n "${ATLAS_CONTAINER:-}" ]; then
        printf -- '-c\n%s\n' "$ATLAS_CONTAINER"
    fi
}

# GET a path on one pod from inside the pod (the image has python, not curl).
# Prints the response body; never fails the exercise.
# Headers travel over stdin, built with json.dumps from the environment: the
# proxy secret never appears in argv (ps, API-server exec audit logs) and
# values with quotes or backslashes stay valid JSON.
_request_headers() {
    local with_admin="$1"
    WITH_ADMIN="$with_admin" python3 -c '
import json, os
headers = {}
if os.environ.get("WITH_ADMIN") == "1" and os.environ.get("ATLAS_ADMIN_EMAIL"):
    headers[os.environ.get("ATLAS_AUTH_HEADER") or "X-User-Email"] = os.environ["ATLAS_ADMIN_EMAIL"]
    if os.environ.get("ATLAS_PROXY_SECRET"):
        headers[os.environ.get("ATLAS_PROXY_SECRET_HEADER") or "X-Proxy-Secret"] = os.environ["ATLAS_PROXY_SECRET"]
print(json.dumps(headers))
'
}

pod_get() {
    local pod="$1" path="$2" with_admin="${3:-0}"
    local container_args=()
    mapfile -t container_args < <(_container_flag)
    _request_headers "$with_admin" | kc exec -i "$pod" "${container_args[@]}" -- python3 -c '
import json, sys, urllib.request
req = urllib.request.Request(sys.argv[1], headers=json.load(sys.stdin))
try:
    with urllib.request.urlopen(req, timeout=5) as resp:
        print(resp.read().decode())
except Exception as exc:
    print(json.dumps({"probe_error": type(exc).__name__, "detail": str(exc)[:200]}))
' "http://127.0.0.1:${ATLAS_PORT}${path}" 2>&1 || echo '{"probe_error": "exec_failed"}'
}

# Record health (+ coordination when an admin identity is set) for every pod.
snapshot() {
    local label="$1"
    local file="$OUT_DIR/snapshot-${label}.jsonl"
    if [ "$DRY_RUN" = 1 ]; then
        printf 'DRY-RUN: snapshot %s of pods matching %s\n' "$label" "$ATLAS_SELECTOR"
        return 0
    fi
    : > "$file"
    local pod
    for pod in $(atlas_pods); do
        local health coord="null"
        health="$(pod_get "$pod" /api/health | tail -n 1)"
        if [ -n "${ATLAS_ADMIN_EMAIL:-}" ]; then
            coord="$(pod_get "$pod" /admin/coordination 1 | tail -n 1)"
        fi
        printf '{"at": "%s", "label": "%s", "pod": "%s", "health": %s, "coordination": %s}\n' \
            "$(date -u +%FT%TZ)" "$label" "$pod" "${health:-null}" "${coord:-null}" >> "$file"
        log "snapshot $label $pod: $(printf '%s' "$health" | head -c 160)"
    done
}

# Stream every current Atlas pod's log into $OUT_DIR/pod-<pod>.log; the [COORD]
# lines are filtered out of those files afterwards (summarize). Pods created
# later (replacements) are picked up by follow_new_pods. The follower is a
# single background process, not a pipeline, so stop_log_capture can kill it
# without leaving children behind.
start_log_capture() {
    [ "$DRY_RUN" = 1 ] && { printf 'DRY-RUN: stream [COORD] logs of pods matching %s\n' "$ATLAS_SELECTOR"; return 0; }
    local pod
    for pod in $(atlas_pods); do
        _capture_pod "$pod"
    done
}

_capture_pod() {
    local pod="$1"
    [ -e "$OUT_DIR/.capturing-$pod" ] && return 0
    touch "$OUT_DIR/.capturing-$pod"
    # The binary itself, not the kc function: a backgrounded function runs in
    # a subshell whose PID is not the follower's.
    "$KUBECTL" ${NAMESPACE:+-n "$NAMESPACE"} logs -f --since=1s "$pod" ${ATLAS_CONTAINER:+-c "$ATLAS_CONTAINER"} \
        > "$OUT_DIR/pod-$pod.log" 2>/dev/null &
    LOG_PIDS+=("$!")
}

follow_new_pods() {
    [ "$DRY_RUN" = 1 ] && return 0
    local pod
    for pod in $(atlas_pods); do
        _capture_pod "$pod"
    done
}

stop_log_capture() {
    local pid
    for pid in "${LOG_PIDS[@]:-}"; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    done
    for pid in "${LOG_PIDS[@]:-}"; do
        [ -n "$pid" ] && wait "$pid" 2>/dev/null || true
    done
    LOG_PIDS=()
    if [ -n "${OUT_DIR:-}" ]; then
        rm -f "$OUT_DIR"/.capturing-* 2>/dev/null || true
    fi
}

# Poll health of every pod every $interval seconds for $duration seconds.
observe() {
    local label="$1" duration="$2" interval="${3:-5}"
    if [ "$DRY_RUN" = 1 ]; then
        printf 'DRY-RUN: observe %s for %ss every %ss\n' "$label" "$duration" "$interval"
        return 0
    fi
    local end=$(( $(date +%s) + duration )) n=0
    while [ "$(date +%s)" -lt "$end" ]; do
        n=$((n + 1))
        follow_new_pods
        snapshot "${label}-${n}"
        sleep "$interval"
    done
}

# The console carries WARNING and above (interruptions, failures, dependency
# transitions). INFO events (run_admitted, run_finished, summary) are only in
# the JSON app log inside the pod; copy them from every pod still running.
collect_app_logs() {
    [ "$DRY_RUN" = 1 ] && { printf 'DRY-RUN: collect [COORD] lines from %s in each pod\n' "${ATLAS_APP_LOG:-/app/logs/app.jsonl}"; return 0; }
    local pod
    for pod in $(atlas_pods); do
        kc exec "$pod" ${ATLAS_CONTAINER:+-c "$ATLAS_CONTAINER"} -- \
            grep -h '\[COORD\]' "${ATLAS_APP_LOG:-/app/logs/app.jsonl}" > "$OUT_DIR/applog-$pod.jsonl" 2>/dev/null || true
    done
}

summarize() {
    [ "$DRY_RUN" = 1 ] && return 0
    stop_log_capture
    local f
    for f in "$OUT_DIR"/pod-*.log; do
        [ -e "$f" ] || continue
        grep -h '\[COORD\]' "$f" > "$OUT_DIR/coord-$(basename "$f" .log | sed 's/^pod-//').log" || true
    done
    collect_app_logs
    log "[COORD] events captured:"
    cat "$OUT_DIR"/coord-*.log "$OUT_DIR"/applog-*.jsonl 2>/dev/null \
        | grep -o 'event=[a-z_]*' | sort | uniq -c | sort -rn >&2 || true
    log "interrupted runs:"
    grep -h 'event=run_interrupted' "$OUT_DIR"/coord-*.log 2>/dev/null >&2 || log "  (none)"
    log "dependency transitions:"
    grep -hE 'event=dependency_(down|recovered)' "$OUT_DIR"/coord-*.log 2>/dev/null >&2 || log "  (none)"
    log "evidence: $OUT_DIR"
}

trap stop_log_capture EXIT
