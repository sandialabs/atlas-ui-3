#!/bin/bash
# Test script for PR #426: Follow-up question suggestion buttons
#
# Test plan:
# - Verify suggestion_routes module imports cleanly
# - Verify feature flag default is False
# - Verify suggestion endpoint is registered on the app router
# - Verify SuggestFollowupsRequest/Response models validate correctly
# - Verify config exposes followup_suggestions feature flag
# - Run frontend tests for follow-up suggestion rendering
# - Run backend unit tests

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
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

print_header "PR #426: Follow-up Question Suggestions Tests"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

print_header "1. Suggestion routes module imports"
python -c "from atlas.routes.suggestion_routes import suggestion_router, SuggestFollowupsRequest, SuggestFollowupsResponse; print('Import OK')" 2>&1
print_result $? "suggestion_routes module imports cleanly"

print_header "2. Feature flag default is False"
python -c "
from atlas.modules.config.config_manager import AppSettings
settings = AppSettings()
assert settings.feature_followup_suggestions_enabled is False, 'Expected default False'
print('Default is False')
" 2>&1
print_result $? "Feature flag defaults to False"

print_header "3. Feature flag can be enabled via env"
FEATURE_FOLLOWUP_SUGGESTIONS_ENABLED=true python -c "
import os
os.environ['FEATURE_FOLLOWUP_SUGGESTIONS_ENABLED'] = 'true'
from atlas.modules.config.config_manager import AppSettings
settings = AppSettings()
assert settings.feature_followup_suggestions_enabled is True, 'Expected True when env set'
print('Flag enabled via env')
" 2>&1
print_result $? "Feature flag enabled via environment variable"

print_header "4. Request/Response model validation"
python -c "
from atlas.routes.suggestion_routes import SuggestFollowupsRequest, SuggestFollowupsResponse

# Valid request
req = SuggestFollowupsRequest(
    messages=[{'role': 'user', 'content': 'Hello'}, {'role': 'assistant', 'content': 'Hi there'}],
    model='gpt-4'
)
assert len(req.messages) == 2
assert req.model == 'gpt-4'

# Valid response
resp = SuggestFollowupsResponse(questions=['Q1?', 'Q2?', 'Q3?'])
assert len(resp.questions) == 3

# Empty response
resp_empty = SuggestFollowupsResponse(questions=[])
assert len(resp_empty.questions) == 0

print('Models validate correctly')
" 2>&1
print_result $? "Pydantic models validate correctly"

print_header "5. Suggestion router is registered with correct prefix"
python -c "
from atlas.routes.suggestion_routes import suggestion_router
routes = [r.path for r in suggestion_router.routes]
assert any('suggest_followups' in r for r in routes), f'Expected suggest_followups in {routes}'
print(f'Routes: {routes}')
" 2>&1
print_result $? "Suggestion router has /suggest_followups endpoint"

print_header "6. Config routes expose followup_suggestions feature"
python -c "
import inspect
from atlas.routes import config_routes
source = inspect.getsource(config_routes)
assert 'followup_suggestions' in source, 'followup_suggestions not found in config_routes'
print('Config routes expose followup_suggestions')
" 2>&1
print_result $? "Config routes include followup_suggestions flag"

print_header "7. Frontend tests (drag-drop with followUpSuggestions mock)"
cd "$PROJECT_ROOT/frontend" && npx vitest run src/test/drag-drop-file-attach.test.jsx --reporter=verbose 2>&1
print_result $? "Frontend drag-drop tests pass with followUpSuggestions mock"

print_header "8. Backend unit tests"
cd "$ATLAS_DIR" && python -m pytest tests/ -x -q --tb=short 2>&1
print_result $? "Backend unit tests pass"

print_header "Summary"
echo "=========================================="
echo -e "Passed: ${GREEN}${PASSED}${NC}"
echo -e "Failed: ${RED}${FAILED}${NC}"
echo "=========================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
