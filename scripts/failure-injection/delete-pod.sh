#!/bin/bash
# Failure exercise: delete one Atlas API pod (issue #1129).
#
#   graceful (default)  kubectl delete pod      -> SIGTERM, terminationGracePeriod
#   --force             --grace-period=0 --force -> no shutdown hooks (crash-like)
#
# Expected (docs/admin/multi-replica-observability.md):
#   graceful: the pod logs "[COORD] event=run_interrupted ... reason=replica_shutdown"
#             for each live run and "event=replica_shutdown interrupted_runs=N";
#             other replicas serve those conversations as failed with
#             end_reason=replica_shutdown. Completed tool calls are not re-run.
#   --force:  no interruption is reported by anyone yet: detecting a lost owner
#             is #1128 (leases). The conversation keeps the steps saved so far.
#   Both:     a replacement pod becomes Ready and logs "event=replica_started".
#
# Usage: delete-pod.sh -n NAMESPACE [--pod NAME] [--force] [--settle 60] [--dry-run] [--yes]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { sed -n '2,17p' "$0"; exit "${1:-0}"; }
POD="" FORCE=0 SETTLE=60
parse_common_flags "$@"
set -- "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"
while [ $# -gt 0 ]; do
    case "$1" in
        --pod) POD="$2"; shift 2 ;;
        --force) FORCE=1; shift ;;
        --settle) SETTLE="$2"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) log "unknown argument: $1"; usage 2 ;;
    esac
done

require_target
init_out_dir delete-pod
if [ -z "$POD" ]; then
    if [ "$DRY_RUN" = 1 ]; then
        POD="<first pod matching $ATLAS_SELECTOR>"
    else
        POD="$(atlas_pods | head -n 1)"
        [ -n "$POD" ] || die "no pods match $ATLAS_SELECTOR in $NAMESPACE"
    fi
fi

confirm "About to delete pod $POD ($([ "$FORCE" = 1 ] && echo forced || echo graceful))."
snapshot before
start_log_capture
if [ "$FORCE" = 1 ]; then
    act kc delete pod "$POD" --grace-period=0 --force --wait=false
else
    act kc delete pod "$POD" --wait=true
fi
observe after "$SETTLE" 5
act kc wait --for=condition=Ready pod -l "$ATLAS_SELECTOR" --timeout=180s
snapshot recovered
summarize
