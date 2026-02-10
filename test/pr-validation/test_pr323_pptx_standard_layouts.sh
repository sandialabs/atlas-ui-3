#!/bin/bash
# Test script for PR #323 - PPTX standard Office layouts
#
# Covers:
# - Template search path function exists and works
# - Three-tier layout strategy: template -> built-in -> blank fallback
# - PPTX_TEMPLATE_PATH environment variable support
# - _populate_content_frame reusable function exists
# - _find_template_path function exists
# - _get_layout_by_name function exists
# - No ambiguous variable names (ruff clean)
# - MCP server imports and starts successfully
# - Backend unit tests pass

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

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

PPTX_FILE="$PROJECT_ROOT/atlas/mcp/pptx_generator/main.py"

# -------------------------------------------------------
print_header "Check 1: _find_template_path function exists"
# -------------------------------------------------------
if grep -q 'def _find_template_path' "$PPTX_FILE"; then
    print_result 0 "_find_template_path function found"
else
    print_result 1 "_find_template_path function not found"
fi

# -------------------------------------------------------
print_header "Check 2: PPTX_TEMPLATE_PATH env var support"
# -------------------------------------------------------
if grep -q 'PPTX_TEMPLATE_PATH' "$PPTX_FILE"; then
    print_result 0 "PPTX_TEMPLATE_PATH environment variable supported"
else
    print_result 1 "PPTX_TEMPLATE_PATH environment variable not found"
fi

# -------------------------------------------------------
print_header "Check 3: TEMPLATE_SEARCH_PATHS defined"
# -------------------------------------------------------
if grep -q 'TEMPLATE_SEARCH_PATHS' "$PPTX_FILE"; then
    print_result 0 "TEMPLATE_SEARCH_PATHS defined"
else
    print_result 1 "TEMPLATE_SEARCH_PATHS not found"
fi

# -------------------------------------------------------
print_header "Check 4: _get_layout_by_name function exists"
# -------------------------------------------------------
if grep -q 'def _get_layout_by_name' "$PPTX_FILE"; then
    print_result 0 "_get_layout_by_name function found"
else
    print_result 1 "_get_layout_by_name function not found"
fi

# -------------------------------------------------------
print_header "Check 5: _populate_content_frame reusable function"
# -------------------------------------------------------
if grep -q 'def _populate_content_frame' "$PPTX_FILE"; then
    print_result 0 "_populate_content_frame function found"
else
    print_result 1 "_populate_content_frame function not found"
fi

# -------------------------------------------------------
print_header "Check 6: Three-tier layout strategy in markdown_to_pptx"
# -------------------------------------------------------
# Check for template path usage, built-in layout check, and blank layout fallback
TIER1=$(grep -c 'template_path' "$PPTX_FILE" 2>/dev/null || echo 0)
TIER2=$(grep -c "built-in.*Title and Content" "$PPTX_FILE" 2>/dev/null || echo 0)
TIER3=$(grep -c "blank layout" "$PPTX_FILE" 2>/dev/null || echo 0)

if [ "$TIER1" -gt 0 ] && [ "$TIER2" -gt 0 ] && [ "$TIER3" -gt 0 ]; then
    print_result 0 "Three-tier layout strategy present (template=$TIER1, built-in=$TIER2, blank=$TIER3)"
else
    print_result 1 "Missing tier(s): template=$TIER1, built-in=$TIER2, blank=$TIER3"
fi

# -------------------------------------------------------
print_header "Check 7: Placeholder-based slide generation"
# -------------------------------------------------------
if grep -q 'use_placeholders' "$PPTX_FILE" && grep -q 'placeholders\[0\]' "$PPTX_FILE" && grep -q 'placeholders\[1\]' "$PPTX_FILE"; then
    print_result 0 "Placeholder-based slide generation using standard layout indices 0 and 1"
else
    print_result 1 "Placeholder-based slide generation not found"
fi

# -------------------------------------------------------
print_header "Check 8: Header/footer bars skipped for template slides"
# -------------------------------------------------------
if grep -q 'not template_path' "$PPTX_FILE"; then
    print_result 0 "Header/footer bars conditionally skipped for template-based slides"
else
    print_result 1 "Header/footer bar condition not found"
fi

# -------------------------------------------------------
print_header "Check 9: Python linting passes"
# -------------------------------------------------------
cd "$PROJECT_ROOT"
RUFF_OUTPUT=$(ruff check atlas/mcp/pptx_generator/main.py 2>&1)
if [ $? -eq 0 ]; then
    print_result 0 "ruff check passes for pptx_generator/main.py"
else
    echo "$RUFF_OUTPUT"
    print_result 1 "ruff check failed for pptx_generator/main.py"
fi

# -------------------------------------------------------
print_header "Check 10: MCP server imports successfully"
# -------------------------------------------------------
cd "$PROJECT_ROOT"
source .venv/bin/activate 2>/dev/null || true
IMPORT_OUTPUT=$(python -c "from atlas.mcp.pptx_generator.main import markdown_to_pptx, _find_template_path, _get_layout_by_name, _populate_content_frame; print('All imports OK')" 2>&1)
if echo "$IMPORT_OUTPUT" | grep -q "All imports OK"; then
    print_result 0 "MCP server imports successfully"
else
    echo "$IMPORT_OUTPUT"
    print_result 1 "MCP server import failed"
fi

# -------------------------------------------------------
print_header "Check 11: End-to-end PPTX generation via Python"
# -------------------------------------------------------
cd "$PROJECT_ROOT"
E2E_OUTPUT=$(python -c "
from atlas.mcp.pptx_generator.main import markdown_to_pptx
# FastMCP @mcp.tool wraps the function; access the underlying fn
fn = markdown_to_pptx.fn if hasattr(markdown_to_pptx, 'fn') else markdown_to_pptx
result = fn('# Slide 1\n- Bullet one\n- Bullet two\n\n# Slide 2\n- More content')
assert 'results' in result, 'Missing results key'
assert 'artifacts' in result, 'Missing artifacts key'
assert result['results'].get('operation') == 'markdown_to_pptx', 'Wrong operation'
assert len(result['artifacts']) >= 1, 'No artifacts generated'
assert result['artifacts'][0]['name'].endswith('.pptx'), 'No PPTX artifact'
assert result['meta_data']['generated_slides'] == 2, f'Expected 2 slides, got {result[\"meta_data\"][\"generated_slides\"]}'
print(f'Generated {result[\"meta_data\"][\"generated_slides\"]} slides with {len(result[\"artifacts\"])} artifacts')
" 2>&1)
if echo "$E2E_OUTPUT" | grep -q "Generated 2 slides"; then
    print_result 0 "End-to-end PPTX generation: $E2E_OUTPUT"
else
    echo "$E2E_OUTPUT"
    print_result 1 "End-to-end PPTX generation failed"
fi

# -------------------------------------------------------
print_header "Check 12: Template path with non-existent env var logs warning"
# -------------------------------------------------------
E2E_TEMPLATE=$(PPTX_TEMPLATE_PATH="/tmp/nonexistent_template_12345.pptx" python -c "
from atlas.mcp.pptx_generator.main import _find_template_path
result = _find_template_path()
# Should return None since the file doesn't exist
if result is None:
    print('Correctly returned None for missing template')
else:
    print(f'Unexpected: returned {result}')
" 2>&1)
if echo "$E2E_TEMPLATE" | grep -q "Correctly returned None"; then
    print_result 0 "Template path gracefully handles missing PPTX_TEMPLATE_PATH"
else
    echo "$E2E_TEMPLATE"
    print_result 1 "Template path did not handle missing PPTX_TEMPLATE_PATH correctly"
fi

# -------------------------------------------------------
print_header "Check 13: Backend unit tests"
# -------------------------------------------------------
cd "$PROJECT_ROOT"
if "$PROJECT_ROOT/test/run_tests.sh" backend 2>&1 | tail -5 | grep -qE '(passed|PASSED)'; then
    print_result 0 "Backend tests passed"
else
    "$PROJECT_ROOT/test/run_tests.sh" backend 2>&1 | tail -10
    print_result 1 "Backend tests failed"
fi

# -------------------------------------------------------
print_header "SUMMARY"
# -------------------------------------------------------
echo ""
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ "$FAILED" -gt 0 ]; then
    echo -e "${RED}Some checks failed.${NC}"
    exit 1
else
    echo -e "${GREEN}All checks passed.${NC}"
    exit 0
fi
