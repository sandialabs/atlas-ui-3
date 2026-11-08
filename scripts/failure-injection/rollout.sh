#!/bin/bash
# Failure exercise: controlled rollout of the Atlas API (issue #1129).
#
# Restarts the Deployment (kubectl rollout restart), which replaces every pod
# one surge at a time, and records what each replica reported on the way out.
#
# Expected (docs/admin/multi-replica-observability.md):
#   - each old pod logs "event=replica_shutdown interrupted_runs=N" and one
#     "event=run_interrupted reason=replica_shutdown" per run it still owned;
#   - each new pod logs "event=replica_started";
#   - /api/health stays "healthy" on the pods that are serving;
#   - interrupted runs show as failed with end_reason=replica_shutdown on the
#     surviving replicas; nothing is replayed. Long runs that must survive a
#     rollout are the #1128 work (they do not migrate between replicas).
#
# Start a few long agent runs first (for example with the agent-runs API) to
# see interruptions; an idle rollout only shows replica_shutdown/started.
#
# Usage: rollout.sh -n NAMESPACE [--deployment deploy/atlas] [--timeout 600s] [--dry-run] [--yes]
set -euo pipefail
# shellcheck source=lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

usage() { sed -n '2,20p' "$0"; exit "${1:-0}"; }
DEPLOYMENT="${ATLAS_DEPLOYMENT:-deploy/atlas}" TIMEOUT=600s
parse_common_flags "$@"
set -- "${REMAINING_ARGS[@]+"${REMAINING_ARGS[@]}"}"
while [ $# -gt 0 ]; do
    case "$1" in
        --deployment) DEPLOYMENT="$2"; shift 2 ;;
        --timeout) TIMEOUT="$2"; shift 2 ;;
        -h|--help) usage 0 ;;
        *) log "unknown argument: $1"; usage 2 ;;
    esac
done

require_target
init_out_dir rollout
confirm "About to restart $DEPLOYMENT (rolling)."
snapshot before
start_log_capture
act kc rollout restart "$DEPLOYMENT"
if [ "$DRY_RUN" = 1 ]; then
    act kc rollout status "$DEPLOYMENT" --timeout="$TIMEOUT"
else
    # Pick up new pods' logs while the rollout progresses.
    kc rollout status "$DEPLOYMENT" --timeout="$TIMEOUT" &
    status_pid=$!
    while kill -0 "$status_pid" 2>/dev/null; do
        follow_new_pods
        snapshot "during-$(date -u +%H%M%S)"
        sleep 5
    done
    wait "$status_pid" || die "rollout did not complete within $TIMEOUT"
fi
snapshot after
summarize
