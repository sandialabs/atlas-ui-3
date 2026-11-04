#!/bin/bash
# Failure exercise: take Redis or PostgreSQL away, then bring it back (issue #1129).
#
# Kubernetes/OpenShift: scales the dependency's workload to 0 replicas and
# restores the original count. Local/podman: freezes a container
# (podman pause/unpause), which is the harder black-hole case.
#
# Expected (docs/admin/multi-replica-observability.md), on every Atlas replica:
#   - /api/health turns "degraded" (still HTTP 200): session_store.status or
#     checks.chat_history_db becomes "unavailable" -- within one probe;
#   - one "[COORD] event=dependency_down dependency=<redis|postgres>" line per
#     replica (repeated as dependency_still_down at most every 30 s), then
#     "event=dependency_recovered" with down_for_s once it is back;
#   - Redis down: requests needing the shared session fail with
#     SESSION_STORE_UNAVAILABLE (no silent per-replica fallback); conversations
#     in PostgreSQL are untouched.
#   - PostgreSQL down: conversation saves fail and are counted
#     (atlas_conversation_persist_failures_total); live runs keep executing.
#
# Usage:
#   dependency-outage.sh -n NAMESPACE --dependency redis|postgres [--target statefulset/redis] [--duration 60]
#   dependency-outage.sh --podman CONTAINER --dependency redis|postgres --url http://127.0.0.1:8000 [--url ...]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { sed -n '2,24p' "$0"; exit "${1:-0}"; }
DEPENDENCY="" TARGET="" DURATION=60 PODMAN_CONTAINER="" URLS=()
parse_common_flags "$@"
set -- "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"
while [ $# -gt 0 ]; do
    case "$1" in
        --dependency) DEPENDENCY="$2"; shift 2 ;;
        --target) TARGET="$2"; shift 2 ;;
        --duration) DURATION="$2"; shift 2 ;;
        --podman) PODMAN_CONTAINER="$2"; shift 2 ;;
        --url) URLS+=("$2"); shift 2 ;;
        -h|--help) usage 0 ;;
        *) log "unknown argument: $1"; usage 2 ;;
    esac
done
case "$DEPENDENCY" in
    redis) TARGET="${TARGET:-${ATLAS_REDIS_TARGET:-statefulset/redis}}" ;;
    postgres) TARGET="${TARGET:-${ATLAS_POSTGRES_TARGET:-statefulset/postgres}}" ;;
    *) log "--dependency must be redis or postgres"; usage 2 ;;
esac

# --- local podman mode ----------------------------------------------------
if [ -n "$PODMAN_CONTAINER" ]; then
    [ "${#URLS[@]}" -gt 0 ] || die "--podman needs at least one --url of an Atlas replica"
    init_out_dir "outage-$DEPENDENCY"
    confirm "About to freeze container $PODMAN_CONTAINER for ${DURATION}s ($DEPENDENCY outage)." "$PODMAN_CONTAINER"
    poll_urls() {
        local label="$1" url
        for url in "${URLS[@]}"; do
            local body
            body="$(python3 -c 'import sys,urllib.request
try: print(urllib.request.urlopen(sys.argv[1] + "/api/health", timeout=5).read().decode())
except Exception as e: print("{\"probe_error\": \"%s\"}" % type(e).__name__)' "$url")"
            printf '{"label": "%s", "url": "%s", "health": %s}\n' "$label" "$url" "$body" >> "$OUT_DIR/snapshots.jsonl"
            log "$label $url: $(printf '%s' "$body" | head -c 200)"
        done
    }
    [ "$DRY_RUN" = 1 ] || poll_urls before
    # Always thaw, even on Ctrl-C.
    [ "$DRY_RUN" = 1 ] || trap 'podman unpause "$PODMAN_CONTAINER" > /dev/null 2>&1 || true' EXIT
    act podman pause "$PODMAN_CONTAINER"
    if [ "$DRY_RUN" = 0 ]; then
        end=$(( $(date +%s) + DURATION ))
        while [ "$(date +%s)" -lt "$end" ]; do poll_urls during; sleep 5; done
    fi
    act podman unpause "$PODMAN_CONTAINER"
    if [ "$DRY_RUN" = 0 ]; then sleep 5; poll_urls after; fi
    log "evidence: $OUT_DIR (replica logs carry the [COORD] dependency_down/recovered lines)"
    exit 0
fi

# --- Kubernetes / OpenShift mode -----------------------------------------------
require_target
init_out_dir "outage-$DEPENDENCY"
ORIGINAL=1
if [ "$DRY_RUN" = 0 ]; then
    ORIGINAL="$(kc get "$TARGET" -o jsonpath='{.spec.replicas}')"
    [ -n "$ORIGINAL" ] && [ "$ORIGINAL" != 0 ] || die "$TARGET has no running replicas to take away"
fi
confirm "About to scale $TARGET from $ORIGINAL to 0 for ${DURATION}s ($DEPENDENCY outage)."
snapshot before
start_log_capture
# Always restore, even if observation fails or is interrupted. The trap stays
# armed until a scale-up has actually succeeded, and a failed restore makes
# the script exit non-zero instead of reporting success.
RESTORED=0
restore() {
    if act kc scale "$TARGET" --replicas="$ORIGINAL"; then
        # Trust the API server's spec, not the command's exit code.
        local now
        now="$( [ "$DRY_RUN" = 1 ] && echo "$ORIGINAL" || kc get "$TARGET" -o jsonpath='{.spec.replicas}')"
        if [ "$now" = "$ORIGINAL" ]; then
            RESTORED=1
            return 0
        fi
        log "scale reported success but $TARGET has spec.replicas=${now:-?}"
    fi
    log "RESTORE FAILED: run: $KUBECTL -n $NAMESPACE scale $TARGET --replicas=$ORIGINAL"
    return 1
}
on_exit() {
    local rc=$?
    if [ "$RESTORED" != 1 ]; then
        restore || rc=1
    fi
    stop_log_capture
    exit "$rc"
}
trap on_exit EXIT
act kc scale "$TARGET" --replicas=0
observe during "$DURATION" 5
restore || die "$TARGET was not restored to $ORIGINAL replicas"
act kc rollout status "$TARGET" --timeout=300s
observe recovery 45 5
summarize
