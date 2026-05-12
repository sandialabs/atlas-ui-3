#!/usr/bin/env bash
# PR #596 - Agent Portal Remove (stop + drop) action
#
# Scope validated by this script (the second of two for PR #596; the
# first, test_pr596_agent_portal_origin_allowlist.sh, covers the
# Origin allowlist scope):
#   1. Route POST /api/agent-portal/processes/{id}/remove is declared
#      and writes a "remove" audit event with final_status.
#   2. ProcessManager.remove and ProcessManager.stop_and_remove exist.
#   3. End-to-end e2e tests for the new endpoint pass
#      (remove-finished, remove-still-running, remove-unknown-id).
#   4. Namespace-strip defense in depth covered by unit tests in
#      test_process_manager.py.
#   5. docs/agentportal/threat-model.md lists the new endpoint in the
#      deferred-items ownership-check bullet.
#   6. CHANGELOG entry mentions Remove action and namespace-strip.
#   7. Frontend AgentPortal.jsx and Pane.jsx import the Trash2 icon
#      (regression guard for the UI affordance).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

print_header "PR #596: Agent Portal Remove action"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# ==========================================
# 1. /remove route declared + writes audit event
# ==========================================
print_header "1. /remove route + audit event"
ROUTES="$ATLAS_DIR/routes/agent_portal_routes.py"

grep -q '@router.post("/processes/{process_id}/remove")' "$ROUTES"
print_result $? "POST /processes/{id}/remove route is declared"

grep -q 'record_audit_event' "$ROUTES" \
    && awk '/async def remove_process/{flag=1} flag; /^async def /&&!/remove_process/{flag=0}' "$ROUTES" \
        | grep -q '"remove"'
print_result $? "remove_process writes a 'remove' audit event"

grep -q "TODO(graduation): add per-user ownership check" "$ROUTES" \
    && [ "$(grep -c 'TODO(graduation): add per-user ownership check' "$ROUTES")" -ge 5 ]
print_result $? "agent_portal_routes.py has >=5 graduation TODO markers (one for /remove)"

# ==========================================
# 2. ProcessManager remove() and stop_and_remove()
# ==========================================
print_header "2. ProcessManager.remove / stop_and_remove"
MGR="$ATLAS_DIR/modules/process_manager/manager.py"
grep -q "def remove(self, process_id" "$MGR"
print_result $? "ProcessManager.remove() defined"
grep -q "async def stop_and_remove" "$MGR"
print_result $? "ProcessManager.stop_and_remove() defined"

# ==========================================
# 3. e2e tests pass
# ==========================================
print_header "3. e2e Remove tests"
python -m pytest \
    "$ATLAS_DIR/tests/test_agent_portal_e2e.py::test_remove_finished_process_drops_it_from_list" \
    "$ATLAS_DIR/tests/test_agent_portal_e2e.py::test_remove_running_process_stops_and_drops" \
    "$ATLAS_DIR/tests/test_agent_portal_e2e.py::test_remove_unknown_process_returns_404" \
    -q > /tmp/pr596_remove_e2e.log 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
    tail -30 /tmp/pr596_remove_e2e.log
fi
print_result "$RC" "Remove e2e tests pass"

# ==========================================
# 4. Namespace-strip unit tests
# ==========================================
print_header "4. Namespace-strip unit tests"
python -m pytest \
    "$ATLAS_DIR/tests/test_process_manager.py::test_launch_strips_namespaces_when_host_unsupported" \
    "$ATLAS_DIR/tests/test_process_manager.py::test_launch_skips_capability_probe_when_namespaces_false" \
    -q > /tmp/pr596_ns_strip.log 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
    tail -30 /tmp/pr596_ns_strip.log
fi
print_result "$RC" "Namespace-strip unit tests pass"

# ==========================================
# 5. Threat-model doc lists the new endpoint
# ==========================================
print_header "5. Threat-model deferred-items entry"
grep -q "POST /processes/{id}/remove" "$PROJECT_ROOT/docs/agentportal/threat-model.md"
print_result $? "threat-model.md lists POST /processes/{id}/remove in deferred items"

# ==========================================
# 6. CHANGELOG entry covers new scopes
# ==========================================
print_header "6. CHANGELOG bullets"
CHL="$PROJECT_ROOT/CHANGELOG.md"
awk '/^### PR #596 /{flag=1; next} /^### PR #/{flag=0} flag' "$CHL" | grep -qi "Remove (stop + drop)\|stop_and_remove\|/remove"
print_result $? "CHANGELOG entry mentions the Remove action"
awk '/^### PR #596 /{flag=1; next} /^### PR #/{flag=0} flag' "$CHL" | grep -qi "namespace"
print_result $? "CHANGELOG entry mentions the namespace-strip defense"

# ==========================================
# 7. Frontend imports Trash2 in the affected components
# ==========================================
print_header "7. Trash2 icon import"
grep -q "Trash2" "$FRONTEND_DIR/src/components/AgentPortal.jsx"
print_result $? "AgentPortal.jsx imports/uses Trash2"
grep -q "Trash2" "$FRONTEND_DIR/src/components/agent-portal/Pane.jsx"
print_result $? "Pane.jsx imports/uses Trash2"

# ==========================================
# Summary
# ==========================================
print_header "Summary"
echo "Passed: $PASSED"
echo "Failed: $FAILED"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
