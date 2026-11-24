#!/bin/bash
# Validation for issue #1226: multi-replica documentation drift.
# Greps the docs for the corrected statements and for the removed wrong ones.
# Whitespace (including line wraps) is normalized before matching, so
# re-wrapping a paragraph does not change the result. Run manually; like the
# other scripts here it is not wired into a workflow.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SS="$ROOT/docs/admin/session-store.md"
KS="$ROOT/docs/admin/kubernetes-staging.md"
PCR="$ROOT/docs/admin/parallel-conversation-runs.md"
RC="$ROOT/docs/developer/run-coordination.md"
OBS="$ROOT/docs/admin/multi-replica-observability.md"
CTX="$ROOT/frontend/src/contexts/ChatContext.jsx"
FAILED=0

norm() { tr '\n\t' '  ' < "$1" | sed -E 's/\/\/ //g; s/  +/ /g'; }

present() {
    if norm "$1" | grep -qF -- "$2"; then echo "PASS present: $2"; else echo "FAIL missing in $(basename "$1"): $2"; FAILED=$((FAILED+1)); fi
}
absent() {
    if norm "$1" | grep -qF -- "$2"; then echo "FAIL still in $(basename "$1"): $2"; FAILED=$((FAILED+1)); else echo "PASS absent: $2"; fi
}

# Wrong statements removed
absent "$SS" "should keep sticky sessions until distributed run"
absent "$SS" "stop/steer routing and the per-user run cap are still process-local"
absent "$KS" "are process-local until distributed run coordination"
absent "$KS" "process-local run limits"
absent "$PCR" "or sticky sessions, until that routing lands"
absent "$PCR" "or named on"
absent "$PCR" "after a reconnect; \`list_runs\`"
absent "$PCR" "The run registry is process-local today"
absent "$OBS" "The run registry is process-local today"
absent "$OBS" "are **not** reported interrupted by anyone yet"
absent "$CTX" "reaches only the runs this connection's server process owns"
absent "$RC" "## Still open in #1128"

# Corrected statements present
present "$SS" "No sticky sessions are needed"
present "$SS" "\`atlas_get_subagent_result\`"
present "$SS" "Redis must be one logical primary"
present "$SS" "Redis Cluster is not supported"
present "$SS" "\`timeout_graceful_shutdown\`"
present "$SS" "Runs are not drained"
present "$KS" "MCP OAuth pending state"
present "$KS" "OIDC login sessions"
present "$KS" "Admin-panel writes"
present "$KS" "not Redis Cluster"
present "$KS" "approval responses"
present "$KS" "Runs are not drained"
present "$PCR" "\`list_runs\` reports the run's status but does not replay them"
present "$PCR" "does not replay the request"
present "$PCR" "sends an \`auto_approve\` command to the owners"
present "$PCR" "the \`memory\` session store"
present "$RC" "## Beyond #1128: what stays replica-local"
present "$RC" "\`atlas_get_subagent_result\`"
present "$RC" "MCP elicitation requests"
present "$OBS" "\`end_reason=owner_lost\`"
present "$OBS" "the orphan sweep on any surviving replica"
present "$OBS" "stop, steer, approval and auto-approve routing are shared through Redis"
present "$CTX" "routes it to the runs other replicas own"

if [ "$FAILED" -eq 0 ]; then echo "All checks passed"; else echo "$FAILED check(s) failed"; exit 1; fi
