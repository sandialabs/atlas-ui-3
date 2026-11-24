#!/bin/bash
# Validation for issue #1226: multi-replica documentation drift.
# Greps the docs for the corrected statements and for the removed wrong ones.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SS="$ROOT/docs/admin/session-store.md"
KS="$ROOT/docs/admin/kubernetes-staging.md"
PCR="$ROOT/docs/admin/parallel-conversation-runs.md"
RC="$ROOT/docs/developer/run-coordination.md"
FAILED=0

present() {
    if grep -qF -- "$2" "$1"; then echo "PASS present: $2"; else echo "FAIL missing in $(basename "$1"): $2"; FAILED=$((FAILED+1)); fi
}
absent() {
    if grep -qF -- "$2" "$1"; then echo "FAIL still in $(basename "$1"): $2"; FAILED=$((FAILED+1)); else echo "PASS absent: $2"; fi
}

absent "$SS" "should keep sticky sessions until distributed run"
absent "$SS" "stop/steer routing and the per-user run cap are still process-local"
absent "$KS" "are process-local until distributed run coordination"
absent "$PCR" "sticky sessions, until that routing lands"
absent "$PCR" "or named on"

present "$SS" "No sticky sessions"
present "$SS" "Redis Cluster is"
present "$SS" "timeout_graceful_shutdown"
present "$SS" "Runs are not drained"
present "$KS" "MCP OAuth pending state"
present "$KS" "OIDC login sessions"
present "$KS" "Admin-panel writes"
present "$KS" "not Redis Cluster"
present "$KS" "Runs are not drained"
present "$PCR" "does not replay them"
present "$PCR" "routed to the"
present "$RC" "still replica-local"
present "$RC" "elicitation"

if [ "$FAILED" -eq 0 ]; then echo "All checks passed"; else echo "$FAILED check(s) failed"; exit 1; fi
