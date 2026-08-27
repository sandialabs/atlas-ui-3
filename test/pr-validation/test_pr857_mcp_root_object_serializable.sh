#!/bin/bash
# Validation script for PR #857: an MCP tool that returns a typed object must
# not crash the agent loop with "Object of type Root is not JSON serializable".
#
# Background: FastMCP 3.x advertises an auto-generated output_schema for every
# tool. For a typed-object return (typed dict / pydantic model / dataclass) the
# advertised schema has `properties` but no `title` (compress_schema prunes
# titles). On the client side, _parse_call_tool_result validates the response
# structuredContent against that schema and rebuilds a pydantic model/dataclass
# named "Root" when the schema has no title, so CallToolResult.data arrives as a
# model instance, not a plain dict. Atlas's _normalize_mcp_tool_result only
# handled the dict case, wrapped the instance as {"results": <Root>}, and
# execute_tool's json.dumps then raised "Object of type Root is not JSON
# serializable", failing the whole tool call. Any MCP tool returning a typed
# object (including third-party servers) hit this.
#
# Test plan (end-to-end through a real in-process FastMCP server + real FastMCP
# client + real Atlas result normalization, not import checks):
# - A tool returning a typed object (pydantic model) yields a client `data`
#   whose type is literally "Root" (proving we exercise the bug path), and
#   Atlas serializes it to JSON carrying the object's fields.
# - A tool returning Dict[str, Any] still serializes (no 'Root' model).
# - A tool returning str still serializes (wrapped on the wire, unwrapped to
#   a string on the client).
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

print_header "PR #857: typed-object MCP tool result serialization"
python "$SCRIPT_DIR/fixtures/pr857/harness.py"
print_result $? "in-process FastMCP server+client: Root data serializes; str/dict unchanged"

print_header "Backend unit tests"
"$PROJECT_ROOT/test/run_tests.sh" backend >/dev/null 2>&1
print_result $? "test/run_tests.sh backend"

echo ""
echo "=========================================="
echo "Results: $PASSED passed, $FAILED failed"
echo "=========================================="
[ "$FAILED" -eq 0 ]