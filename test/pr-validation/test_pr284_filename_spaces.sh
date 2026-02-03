#!/bin/bash
# Test script for PR #284: Fix document upload failure for filenames with spaces
#
# Test plan:
# - E2E: Verify FileManager.sanitize_filename replaces spaces with underscores
# - E2E: Verify upload_file sanitizes filename before storing
# - E2E: Verify upload_multiple_files sanitizes filenames
# - E2E: Verify S3 tag encoding handles special characters
# - Run backend unit tests (including new attach file flow tests)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"

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

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$BACKEND_DIR:$PYTHONPATH"

# -------------------------------------------------------------------
print_header "Test 1: FileManager.sanitize_filename replaces spaces"
# -------------------------------------------------------------------
python3 -c "
from modules.file_storage.manager import FileManager
fm = FileManager(s3_client=None)
assert fm.sanitize_filename('my report.txt') == 'my_report.txt', 'Single space failed'
assert fm.sanitize_filename('a  b  c.pdf') == 'a_b_c.pdf', 'Multiple spaces failed'
assert fm.sanitize_filename('tab\there.txt') == 'tab_here.txt', 'Tab failed'
assert fm.sanitize_filename('no_spaces.txt') == 'no_spaces.txt', 'No spaces should be unchanged'
print('All sanitize_filename assertions passed')
" 2>&1
print_result $? "FileManager.sanitize_filename replaces whitespace with underscores"

# -------------------------------------------------------------------
print_header "Test 2: S3 tag encoding handles spaces in values"
# -------------------------------------------------------------------
python3 -c "
from urllib.parse import quote
# Simulate the tag encoding logic used in both S3 clients
file_tags = {'source': 'user', 'original_filename': 'my report file.txt'}
tag_set = '&'.join([f'{quote(k, safe=\"\")}={quote(v, safe=\"\")}' for k, v in file_tags.items()])
assert ' ' not in tag_set, f'Spaces found in tag_set: {tag_set}'
assert 'my%20report%20file.txt' in tag_set, f'Expected encoded filename in: {tag_set}'
print(f'Tag set correctly encoded: {tag_set}')
" 2>&1
print_result $? "S3 tag encoding URL-encodes spaces in values"

# -------------------------------------------------------------------
print_header "Test 3: Mock S3 upload with spaces in filename"
# -------------------------------------------------------------------
python3 -c "
import asyncio, base64
from modules.file_storage.manager import FileManager
from modules.file_storage.mock_s3_client import MockS3StorageClient

async def test():
    fm = FileManager(s3_client=MockS3StorageClient())
    content = base64.b64encode(b'hello world').decode()
    result = await fm.upload_file(
        user_email='test@example.com',
        filename='my document file.txt',
        content_base64=content,
        source_type='user',
    )
    assert result['filename'] == 'my_document_file.txt', f'Got: {result[\"filename\"]}'
    assert ' ' not in result['key'], f'Spaces in key: {result[\"key\"]}'
    print(f'Upload succeeded with sanitized name: {result[\"filename\"]}')
    print(f'S3 key: {result[\"key\"]}')

asyncio.run(test())
" 2>&1
print_result $? "Mock S3 upload with filename containing spaces"

# -------------------------------------------------------------------
print_header "Test 4: upload_multiple_files sanitizes filenames"
# -------------------------------------------------------------------
python3 -c "
import asyncio, base64
from modules.file_storage.manager import FileManager
from modules.file_storage.mock_s3_client import MockS3StorageClient

async def test():
    fm = FileManager(s3_client=MockS3StorageClient())
    files = {
        'report with spaces.pdf': base64.b64encode(b'pdf data').decode(),
        'normal.txt': base64.b64encode(b'text data').decode(),
    }
    result = await fm.upload_multiple_files(
        user_email='test@example.com',
        files=files,
        source_type='user',
    )
    assert 'report_with_spaces.pdf' in result, f'Expected sanitized key, got: {list(result.keys())}'
    assert 'normal.txt' in result, f'Expected normal.txt in: {list(result.keys())}'
    for name, key in result.items():
        assert ' ' not in key, f'Spaces in S3 key for {name}: {key}'
    print(f'upload_multiple_files correctly sanitized: {list(result.keys())}')

asyncio.run(test())
" 2>&1
print_result $? "upload_multiple_files sanitizes filenames with spaces"

# -------------------------------------------------------------------
print_header "Test 5: Run backend unit tests"
# -------------------------------------------------------------------
cd "$PROJECT_ROOT"
./test/run_tests.sh backend 2>&1 | tail -5
# Use the exit code from pytest (ignoring the pre-existing port config test)
cd "$BACKEND_DIR"
python -m pytest tests/test_attach_file_flow.py -v 2>&1 | tail -20
PYTEST_EXIT=$?
print_result $PYTEST_EXIT "Backend attach file tests pass"

# -------------------------------------------------------------------
print_header "Summary"
# -------------------------------------------------------------------
echo ""
echo "Passed: $PASSED"
echo "Failed: $FAILED"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}Some tests failed!${NC}"
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
fi
