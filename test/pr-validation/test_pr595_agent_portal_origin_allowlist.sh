#!/usr/bin/env bash
# PR #595 - Agent Portal WebSocket Origin allowlist
#
# Scope validated by this script:
#   1. CHANGELOG has a correctly formatted `### PR #595 - YYYY-MM-DD` heading.
#   2. config_manager.py exposes the new agent_portal_allowed_origins field
#      bound to AGENT_PORTAL_ALLOWED_ORIGINS.
#   3. agent_portal_routes.py uses _origin_is_allowed (the old
#      _origin_is_loopback symbol has been replaced).
#   4. Loopback origins still pass and unlisted hostnames are still rejected
#      when the env var is unset (regression guard for the default-safe path).
#   5. A hostname listed in AGENT_PORTAL_ALLOWED_ORIGINS is accepted while
#      an unlisted one is still rejected.
#   6. docs/agentportal/threat-model.md describes the new env var.
#   7. The dedicated unit-test file for the origin check exists and passes.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"

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

print_header "PR #595: Agent Portal WS Origin allowlist"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# ==========================================
# 1. CHANGELOG heading
# ==========================================
print_header "1. CHANGELOG heading format"
grep -q "^### PR #595 - 2026-" "$PROJECT_ROOT/CHANGELOG.md"
print_result $? "CHANGELOG.md has '### PR #595 - YYYY-MM-DD' heading"

# ==========================================
# 2. Config field exists and is env-bound
# ==========================================
print_header "2. AppSettings.agent_portal_allowed_origins"
CFG="$ATLAS_DIR/modules/config/config_manager.py"
grep -q "agent_portal_allowed_origins" "$CFG"
print_result $? "config_manager.py declares agent_portal_allowed_origins"

grep -q "AGENT_PORTAL_ALLOWED_ORIGINS" "$CFG"
print_result $? "config_manager.py binds to AGENT_PORTAL_ALLOWED_ORIGINS"

# ==========================================
# 3. Route uses _origin_is_allowed (renamed from _origin_is_loopback)
# ==========================================
print_header "3. _origin_is_allowed in routes"
ROUTES="$ATLAS_DIR/routes/agent_portal_routes.py"
grep -q "_origin_is_allowed" "$ROUTES"
print_result $? "agent_portal_routes.py defines _origin_is_allowed"

# The old name must be gone — callers and tests reference the new one.
! grep -q "_origin_is_loopback" "$ROUTES"
print_result $? "agent_portal_routes.py no longer references _origin_is_loopback"

# ==========================================
# 4. Default-safe behaviour (env unset)
# ==========================================
print_header "4. Default-safe behaviour (env unset)"

python - <<'PY' > /tmp/pr595_default.log 2>&1
import sys
from atlas.routes import agent_portal_routes as ap

class S:
    agent_portal_allowed_origins = ""
class CM:
    app_settings = S()
ap.app_factory.get_config_manager = lambda: CM()

assert ap._origin_is_allowed("http://localhost:8000") is True, "loopback must pass"
assert ap._origin_is_allowed("http://127.0.0.1") is True, "loopback must pass"
assert ap._origin_is_allowed("https://attacker.example.com") is False, "non-loopback must be rejected when list is empty"
assert ap._origin_is_allowed(None) is False, "missing origin must be rejected"
print("OK")
PY
RC=$?
if [ "$RC" -ne 0 ]; then
    cat /tmp/pr595_default.log
fi
print_result "$RC" "loopback passes and unlisted hostnames are rejected when env is empty"

# ==========================================
# 5. Allowlisted hostname accepted, unlisted still rejected
# ==========================================
print_header "5. Allowlist behaviour (env populated)"

python - <<'PY' > /tmp/pr595_allowlist.log 2>&1
from atlas.routes import agent_portal_routes as ap

class S:
    agent_portal_allowed_origins = "  Atlas-Dev.Example.COM , atlas.internal "
class CM:
    app_settings = S()
ap.app_factory.get_config_manager = lambda: CM()

assert ap._origin_is_allowed("https://atlas-dev.example.com") is True, "listed host must pass"
assert ap._origin_is_allowed("https://ATLAS-DEV.example.com") is True, "case-insensitive"
assert ap._origin_is_allowed("https://atlas.internal") is True, "second listed host must pass"
assert ap._origin_is_allowed("https://attacker.example.com") is False, "unlisted host still rejected"
assert ap._origin_is_allowed("https://atlas-dev.example.com.attacker.com") is False, "no suffix-match leakage"
print("OK")
PY
RC=$?
if [ "$RC" -ne 0 ]; then
    cat /tmp/pr595_allowlist.log
fi
print_result "$RC" "allowlisted hostname accepted, unlisted hostname still rejected"

# ==========================================
# 6. Threat-model doc mentions the new env var
# ==========================================
print_header "6. Threat-model documentation"
grep -q "AGENT_PORTAL_ALLOWED_ORIGINS" "$PROJECT_ROOT/docs/agentportal/threat-model.md"
print_result $? "docs/agentportal/threat-model.md references AGENT_PORTAL_ALLOWED_ORIGINS"

# ==========================================
# 7. Unit-test module exists and passes
# ==========================================
print_header "7. Origin-check unit tests"
TEST_FILE="$ATLAS_DIR/tests/test_agent_portal_origin_check.py"
[ -f "$TEST_FILE" ]
print_result $? "atlas/tests/test_agent_portal_origin_check.py exists"

python -m pytest "$TEST_FILE" -q > /tmp/pr595_pytest.log 2>&1
PYTEST_RC=$?
if [ "$PYTEST_RC" -ne 0 ]; then
    tail -30 /tmp/pr595_pytest.log
fi
print_result "$PYTEST_RC" "origin-check unit tests pass"

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
