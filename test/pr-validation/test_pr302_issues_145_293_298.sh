#!/bin/bash
# Test script for PR fixing issues #145, #293, #298
#
# Covers:
# - #293: f-string backslash SyntaxError (verify no remaining instances)
# - #145: Help page takes full width (verify CSS class change)
# - #298: MCP timeouts (verify settings load, asyncio.wait_for in client)
# - Run backend unit tests

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
BOLD='\033[1m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo -e "${BOLD}==========================================${NC}"
    echo -e "${BOLD}$1${NC}"
    echo -e "${BOLD}==========================================${NC}"
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

# Activate venv
source "$PROJECT_ROOT/.venv/bin/activate"

###############################################
print_header "Issue #293: No f-string backslash expressions remain"
###############################################

# Check that no f-strings contain backslash escapes inside expressions
# Pattern: f"...{...\.replace('\n'...}..." inside the expression part
COUNT=$(grep -rn "f\".*{.*\\\\n.*}.*\"" "$ATLAS_DIR" --include="*.py" | grep -c "replace" || true)
if [ "$COUNT" -eq 0 ]; then
    print_result 0 "No f-string backslash expressions found in atlas"
else
    print_result 1 "Found $COUNT f-string backslash expressions in backend"
fi

###############################################
print_header "Issue #145: Help page uses full width"
###############################################

# Verify HelpPage.jsx content wrapper has max-w-none
if grep -q "max-w-none" "$PROJECT_ROOT/frontend/src/components/HelpPage.jsx"; then
    print_result 0 "HelpPage.jsx content wrapper includes max-w-none"
else
    print_result 1 "HelpPage.jsx content wrapper missing max-w-none"
fi

###############################################
print_header "Issue #298: MCP timeout settings exist"
###############################################

# Check AppSettings has mcp_discovery_timeout
if grep -q "mcp_discovery_timeout" "$ATLAS_DIR/modules/config/config_manager.py"; then
    print_result 0 "mcp_discovery_timeout defined in AppSettings"
else
    print_result 1 "mcp_discovery_timeout missing from AppSettings"
fi

# Check AppSettings has mcp_call_timeout
if grep -q "mcp_call_timeout" "$ATLAS_DIR/modules/config/config_manager.py"; then
    print_result 0 "mcp_call_timeout defined in AppSettings"
else
    print_result 1 "mcp_call_timeout missing from AppSettings"
fi

# Check asyncio.wait_for is used in client.py for discovery
WAIT_FOR_COUNT=$(grep -c "asyncio.wait_for" "$ATLAS_DIR/modules/mcp_tools/client.py" || true)
if [ "$WAIT_FOR_COUNT" -ge 3 ]; then
    print_result 0 "asyncio.wait_for used $WAIT_FOR_COUNT times in MCP client (list_tools, list_prompts, call_tool)"
else
    print_result 1 "Expected at least 3 asyncio.wait_for calls, found $WAIT_FOR_COUNT"
fi

# Verify timeout settings load correctly via Python import
cd "$ATLAS_DIR"
python -c "
from modules.config.config_manager import AppSettings
s = AppSettings()
assert hasattr(s, 'mcp_discovery_timeout'), 'missing mcp_discovery_timeout'
assert hasattr(s, 'mcp_call_timeout'), 'missing mcp_call_timeout'
assert s.mcp_discovery_timeout == 30, f'expected 30, got {s.mcp_discovery_timeout}'
assert s.mcp_call_timeout == 120, f'expected 120, got {s.mcp_call_timeout}'
print(f'Defaults: discovery={s.mcp_discovery_timeout}s, call={s.mcp_call_timeout}s')
" 2>/dev/null
print_result $? "AppSettings loads with correct timeout defaults"

# Verify env var override works
MCP_DISCOVERY_TIMEOUT=10 MCP_CALL_TIMEOUT=60 python -c "
from modules.config.config_manager import AppSettings
s = AppSettings()
assert s.mcp_discovery_timeout == 10, f'expected 10, got {s.mcp_discovery_timeout}'
assert s.mcp_call_timeout == 60, f'expected 60, got {s.mcp_call_timeout}'
print(f'Overrides: discovery={s.mcp_discovery_timeout}s, call={s.mcp_call_timeout}s')
" 2>/dev/null
print_result $? "MCP timeout env var overrides work correctly"

###############################################
print_header "Backend unit tests"
###############################################

cd "$PROJECT_ROOT"
./test/run_tests.sh backend
print_result $? "Backend unit tests"

###############################################
print_header "Summary"
###############################################

echo ""
echo -e "${BOLD}Results: ${GREEN}$PASSED passed${NC}, ${RED}$FAILED failed${NC}"
echo ""

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
