#!/bin/bash
# Validation script for PR #914 (issue #909): MCP tool-returned images reached
# the frontend canvas but never the LLM. A tool whose payload is an image
# normalized to {"results": {}} for the model -- the images lived only in
# ToolResult.artifacts, which drive canvas display -- so the agent reported it
# could not inspect the screenshot and kept working blind.
#
# Test plan (end-to-end through a real in-process FastMCP server + real
# FastMCP client + real Atlas result normalization + the real injection
# hook, not import checks):
# - An ImageContent-returning tool normalizes to {"results": {}} text and
#   one image/png artifact (the bug path, verbatim from the issue).
# - ToolImageInjector (vision on) appends a synthetic user message holding
#   the image as an image_url data-URI block whose bytes decode to the
#   exact PNG the tool returned, with an intro naming the tool.
# - ToolImageInjector (vision off) adds one separate role:system note
#   message and leaves the tool result JSON untouched (it must stay
#   parseable).
# - A text-only tool triggers neither injection nor a note.
# - Backend unit tests pass.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

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

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

print_header "PR #914: tool-returned images reach the LLM transcript"
python "$SCRIPT_DIR/fixtures/pr914/harness.py"
print_result $? "in-process FastMCP image tool -> injection pipeline"

print_header "Backend unit tests"
"$PROJECT_ROOT/test/run_tests.sh" backend >/dev/null 2>&1
print_result $? "test/run_tests.sh backend"

echo ""
echo "=========================================="
echo "Results: $PASSED passed, $FAILED failed"
echo "=========================================="
[ "$FAILED" -eq 0 ] && [ "$PASSED" -eq 2 ]
