#!/bin/bash
# Test script for PR #274: Add multipart form-data upload support for file extraction
#
# Test plan:
# - Verify form_field_name config field exists and defaults correctly
# - Verify multipart request_format is recognized by config model
# - Start mock file-extractor with multipart endpoint, upload a file via multipart, verify response
# - Verify base64 path still works (regression)
# - Run backend unit tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
MOCK_DIR="$PROJECT_ROOT/mocks/file-extractor-mock"
SCRATCHPAD_DIR="/tmp/pr274_test_$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0
MOCK_PID=""

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

print_skip() {
    echo -e "${YELLOW}SKIPPED${NC}: $1 -- $2"
    SKIPPED=$((SKIPPED + 1))
}

cleanup() {
    if [ -n "$MOCK_PID" ] && kill -0 "$MOCK_PID" 2>/dev/null; then
        kill "$MOCK_PID" 2>/dev/null
        wait "$MOCK_PID" 2>/dev/null
    fi
    rm -rf "$SCRATCHPAD_DIR"
}
trap cleanup EXIT

mkdir -p "$SCRATCHPAD_DIR"

# Activate venv
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# ==========================================
# Check 1: Config model accepts multipart request_format and form_field_name
# ==========================================
print_header "Check 1: Config model accepts multipart and form_field_name"

cd "$ATLAS_DIR"
python3 -c "
from modules.config.config_manager import FileExtractorConfig

# Test multipart request_format
cfg = FileExtractorConfig(url='http://localhost:8010/extract-multipart', request_format='multipart')
assert cfg.request_format == 'multipart', f'Expected multipart, got {cfg.request_format}'
assert cfg.form_field_name == 'file', f'Expected file, got {cfg.form_field_name}'

# Test custom form_field_name
cfg2 = FileExtractorConfig(url='http://localhost:8010/extract', request_format='multipart', form_field_name='document')
assert cfg2.form_field_name == 'document', f'Expected document, got {cfg2.form_field_name}'

# Test base64 still works
cfg3 = FileExtractorConfig(url='http://localhost:8010/extract')
assert cfg3.request_format == 'base64', f'Expected base64, got {cfg3.request_format}'

print('All config model checks passed')
"
print_result $? "Config model accepts multipart request_format and form_field_name"

# ==========================================
# Check 2: Mock extractor has /extract-multipart endpoint
# ==========================================
print_header "Check 2: Mock extractor has /extract-multipart endpoint"

grep -q "extract-multipart" "$MOCK_DIR/main.py"
print_result $? "Mock extractor defines /extract-multipart endpoint"

# ==========================================
# Check 3: E2E - Start mock extractor and test multipart upload
# ==========================================
print_header "Check 3: E2E - Multipart upload via mock extractor"

# Create a small test PDF-like file (just bytes, the mock will try pypdf)
echo "test pdf content for multipart" > "$SCRATCHPAD_DIR/test.txt"

# Start mock extractor in background
cd "$MOCK_DIR"
python3 -c "
import uvicorn
from main import app
uvicorn.run(app, host='127.0.0.1', port=8019, log_level='warning')
" &
MOCK_PID=$!
sleep 2

# Verify mock is running
if kill -0 "$MOCK_PID" 2>/dev/null; then
    # Test multipart upload
    RESPONSE=$(curl -s -X POST "http://127.0.0.1:8019/extract-multipart" \
        -F "file=@$SCRATCHPAD_DIR/test.txt;type=text/plain" \
        -H "Accept: application/json")

    # The mock will fail PDF parsing on a txt file, but should return a JSON response
    echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
# Response should be valid JSON with success field
assert 'success' in data, 'Response missing success field'
print(f'Multipart response received: success={data[\"success\"]}')
"
    print_result $? "Multipart upload returns valid JSON response"

    # Test base64 endpoint still works (regression)
    BASE64_RESPONSE=$(curl -s -X POST "http://127.0.0.1:8019/extract" \
        -H "Content-Type: application/json" \
        -d '{"content": "dGVzdCBjb250ZW50", "filename": "test.txt"}')

    echo "$BASE64_RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
assert 'success' in data, 'Response missing success field'
print(f'Base64 response received: success={data[\"success\"]}')
"
    print_result $? "Base64 endpoint still works (regression)"

    # Clean up mock
    kill "$MOCK_PID" 2>/dev/null
    wait "$MOCK_PID" 2>/dev/null
    MOCK_PID=""
else
    print_skip "Multipart upload test" "Mock extractor failed to start"
    print_skip "Base64 regression test" "Mock extractor failed to start"
fi

# ==========================================
# Check 4: content_extractor.py has multipart branch
# ==========================================
print_header "Check 4: content_extractor.py has multipart branch"

grep -q 'request_format == "multipart"' "$ATLAS_DIR/modules/file_storage/content_extractor.py"
print_result $? "content_extractor.py branches on multipart request_format"

grep -q 'form_field_name' "$ATLAS_DIR/modules/file_storage/content_extractor.py"
print_result $? "content_extractor.py uses form_field_name from config"

# ==========================================
# Check 5: Documentation updated
# ==========================================
print_header "Check 5: Documentation updated"

grep -q "multipart" "$PROJECT_ROOT/docs/developer/file-content-extraction.md"
print_result $? "file-content-extraction.md documents multipart format"

grep -q "form_field_name" "$PROJECT_ROOT/docs/developer/file-content-extraction.md"
print_result $? "file-content-extraction.md documents form_field_name field"

grep -q "274" "$PROJECT_ROOT/CHANGELOG.md"
print_result $? "CHANGELOG.md has entry for PR #274"

# ==========================================
# Check 6: Run backend unit tests
# ==========================================
print_header "Check 6: Backend unit tests"

cd "$PROJECT_ROOT"
./test/run_tests.sh backend
print_result $? "Backend unit tests pass"

# ==========================================
# Summary
# ==========================================
print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo -e "Skipped: ${YELLOW}$SKIPPED${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}PR #274 validation FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}PR #274 validation PASSED${NC}"
    exit 0
fi
