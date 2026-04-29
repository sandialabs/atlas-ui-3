#!/usr/bin/env bash
# PR #558 - Agent Portal (initial + UX refresh + CLI + E2E)
#
# Scope validated by this script:
#   1. CHANGELOG has a correctly formatted `### PR #558 - YYYY-MM-DD` heading.
#   2. Backend test suite (process manager + presets + e2e) passes.
#   3. Ruff is clean for the agent-portal modules.
#   4. The atlas-portal CLI parser builds and each subcommand responds to
#      --help without crashing.
#   5. AgentPortal.jsx contains no window.prompt/window.alert/window.confirm
#      (replaced by the toast + dialog primitives).
#   6. agent_portal_routes.py carries the TODO(graduation) ownership-check
#      markers on the per-process {id} endpoints (deliberate dev-preview
#      deferral — regression-guarded).
#   7. Startup guard rejects FEATURE_AGENT_PORTAL_ENABLED when DEBUG_MODE
#      is false (grep on the guard location; enforced by runtime code).
#   8. docs/agentportal/ has README, threat-model, design-considerations,
#      presets, and cli entries.
#   9. atlas-portal is registered in pyproject.toml [project.scripts].
#  10. Frontend builds cleanly (vite build).

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

print_header "PR #558: Agent Portal"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# ==========================================
# 1. CHANGELOG heading follows convention
# ==========================================
print_header "1. CHANGELOG heading format"

grep -q "^### PR #558 - 2026-" "$PROJECT_ROOT/CHANGELOG.md"
print_result $? "CHANGELOG.md has '### PR #558 - YYYY-MM-DD' heading"

# ==========================================
# 2. Backend tests pass
# ==========================================
print_header "2. Backend test suite"

python -m pytest \
    "$ATLAS_DIR/tests/test_process_manager.py" \
    "$ATLAS_DIR/tests/test_agent_portal_presets.py" \
    "$ATLAS_DIR/tests/test_agent_portal_e2e.py" \
    --tb=short -q > /tmp/pr558_pytest.log 2>&1
PYTEST_RC=$?
if [ "$PYTEST_RC" -ne 0 ]; then
    tail -40 /tmp/pr558_pytest.log
fi
print_result "$PYTEST_RC" "Agent-portal test suite (process_manager + presets + e2e)"

# ==========================================
# 3. Ruff clean on agent-portal modules
# ==========================================
print_header "3. Ruff"

(cd "$PROJECT_ROOT" && ruff check atlas) > /tmp/pr558_ruff.log 2>&1
RUFF_RC=$?
if [ "$RUFF_RC" -ne 0 ]; then
    tail -20 /tmp/pr558_ruff.log
fi
print_result "$RUFF_RC" "ruff check atlas (agent-portal code included)"

# ==========================================
# 4. atlas-portal CLI help
# ==========================================
print_header "4. atlas-portal CLI"

python -m atlas.portal_cli --help > /tmp/pr558_cli.log 2>&1
CLI_RC=$?
print_result "$CLI_RC" "atlas-portal --help exits 0"

python -m atlas.portal_cli launch --help > /dev/null 2>&1
print_result $? "atlas-portal launch --help exits 0"

python -m atlas.portal_cli presets list 2>&1 | grep -qE "(connection error|HTTP|presets)"
# We do not expect a server to be running during validation, just that the
# parser builds and the subcommand dispatches. A connection error from the
# request layer counts as success for the parser path.
print_result $? "atlas-portal presets list dispatches (connection error expected without a server)"

# ==========================================
# 5. No window.prompt/alert/confirm in AgentPortal.jsx
# ==========================================
print_header "5. No browser-native dialogs in AgentPortal.jsx"

AGENT_PORTAL_JSX="$FRONTEND_DIR/src/components/AgentPortal.jsx"
NATIVE_DIALOG_COUNT=$(grep -cE "window\.(prompt|alert|confirm)" "$AGENT_PORTAL_JSX" || true)
[ "$NATIVE_DIALOG_COUNT" -eq 0 ]
print_result $? "AgentPortal.jsx does not call window.prompt/alert/confirm (found $NATIVE_DIALOG_COUNT)"

# ==========================================
# 6. Per-process {id} endpoints carry graduation TODO markers
# ==========================================
print_header "6. Per-process {id} graduation TODO markers"

ROUTES_FILE="$ATLAS_DIR/routes/agent_portal_routes.py"
# Four per-process {id} endpoints: GET, DELETE, PATCH, WS stream.
TODO_COUNT=$(grep -cE "TODO\(graduation\): add per-user ownership check" "$ROUTES_FILE" || true)
[ "$TODO_COUNT" -ge 4 ]
print_result $? "agent_portal_routes.py has at least 4 graduation TODO markers (found $TODO_COUNT)"

# ==========================================
# 7. Startup guard is wired
# ==========================================
print_header "7. Startup guard"

# The guard lives in config_manager.py and raises if the flag is on while
# DEBUG_MODE is off. Cheap check: the relevant refusal string must exist.
grep -qE "FEATURE_AGENT_PORTAL_ENABLED.*DEBUG_MODE|agent_portal.*debug" \
    "$ATLAS_DIR/modules/config/config_manager.py" \
    || grep -qE "agent_portal.*dev-only|feature_agent_portal_enabled.*debug_mode" \
       "$ATLAS_DIR/modules/config/config_manager.py"
print_result $? "config_manager.py has a startup guard referencing FEATURE_AGENT_PORTAL_ENABLED + DEBUG_MODE"

# ==========================================
# 8. Agent-portal docs present
# ==========================================
print_header "8. Agent-portal docs"

DOCS_DIR="$PROJECT_ROOT/docs/agentportal"
for f in README.md threat-model.md design-considerations.md presets.md cli.md; do
    [ -f "$DOCS_DIR/$f" ]
    print_result $? "docs/agentportal/$f exists"
done

# ==========================================
# 9. atlas-portal registered as entry point
# ==========================================
print_header "9. pyproject entry point"

grep -q "^atlas-portal = \"atlas.portal_cli:main\"" "$PROJECT_ROOT/pyproject.toml"
print_result $? "pyproject.toml registers atlas-portal = atlas.portal_cli:main"

# ==========================================
# 10. Frontend build clean
# ==========================================
print_header "10. Frontend build"

(cd "$FRONTEND_DIR" && npx --no-install vite build) > /tmp/pr558_vite.log 2>&1
VITE_RC=$?
if [ "$VITE_RC" -ne 0 ]; then
    tail -30 /tmp/pr558_vite.log
fi
print_result "$VITE_RC" "frontend vite build succeeds"

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
