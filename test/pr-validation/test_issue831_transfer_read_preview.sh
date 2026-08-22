#!/bin/bash
set -uo pipefail
# Test script for issue #831: transfer_read_file_from_disk should limit file
# head and tail sent to llm
#
# Test plan:
# - Long UTF-8 file: tool-result text is trimmed to head+tail preview with
#   omission marker; full file is still in the base64 artifact
# - Short UTF-8 file: full content returned in tool result (no truncation)
# - Binary file: no text or base64 injected into tool result; bytes only in artifact
# - MCP_TRANSFER_PREVIEW_LINES env var controls the head/tail line budget
# - MCP_TRANSFER_PREVIEW_BYTES byte ceiling caps single long-line files
# - _preview_lines helper fallbacks (unset, empty, non-numeric, zero, negative)
# - Run the transfer MCP unit tests

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

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

# -------------------------------------------------------------------
print_header "Test 1: Long text file is trimmed to head+tail preview"
# -------------------------------------------------------------------
python3 -c "
import base64, importlib, os, sys, types, tempfile, shutil

class _DummyMCP:
    def tool(self, func=None):
        return func if func else (lambda w: w)

fake = types.ModuleType('atlas.mcp_shared.server_factory')
fake.create_stdio_server = lambda name: _DummyMCP()
sys.modules['atlas.mcp_shared.server_factory'] = fake
transfer = importlib.import_module('atlas.mcp.transfer.main')

d = tempfile.mkdtemp()
try:
    os.environ['MCP_TRANSFER_BASE_DIR'] = d
    os.environ['MCP_TRANSFER_PREVIEW_LINES'] = '2'

    lines = [f'line {i:03d}\n' for i in range(20)]
    with open(os.path.join(d, 'long.txt'), 'w') as f:
        f.writelines(lines)

    result = transfer.read_file_from_disk('long.txt')
    assert not result['meta_data']['is_error'], result
    assert 'content' not in result['results']
    content = result['results']['content_preview']
    assert result['results']['truncated'] is True
    assert result['results']['total_lines'] == 20
    assert result['results']['omitted_lines'] == 16
    assert 'line 000\n' in content and 'line 001\n' in content
    assert 'line 018\n' in content and 'line 019\n' in content
    assert 'line 002\n' not in content
    assert 'full content in artifact' in content
    assert base64.b64decode(result['artifacts'][0]['b64']) == ''.join(lines).encode()
    print('Long file preview verified')
finally:
    shutil.rmtree(d, ignore_errors=True)
" 2>&1
print_result $? "Long text file trimmed to head+tail, full bytes in artifact"

# -------------------------------------------------------------------
print_header "Test 2: Short text file returned in full"
# -------------------------------------------------------------------
python3 -c "
import importlib, os, sys, types, tempfile, shutil

class _DummyMCP:
    def tool(self, func=None):
        return func if func else (lambda w: w)

fake = types.ModuleType('atlas.mcp_shared.server_factory')
fake.create_stdio_server = lambda name: _DummyMCP()
sys.modules['atlas.mcp_shared.server_factory'] = fake
transfer = importlib.import_module('atlas.mcp.transfer.main')

d = tempfile.mkdtemp()
try:
    os.environ['MCP_TRANSFER_BASE_DIR'] = d
    os.environ['MCP_TRANSFER_PREVIEW_LINES'] = '5'

    text = '\n'.join(f'line {i}' for i in range(10)) + '\n'
    with open(os.path.join(d, 'short.txt'), 'w') as f:
        f.write(text)

    result = transfer.read_file_from_disk('short.txt')
    assert not result['meta_data']['is_error']
    assert result['results']['content'] == text
    assert 'content_preview' not in result['results']
    assert result['results']['truncated'] is False
    print('Short file returned in full')
finally:
    shutil.rmtree(d, ignore_errors=True)
" 2>&1
print_result $? "Short text file returned in full (no truncation)"

# -------------------------------------------------------------------
print_header "Test 3: Binary file has no text in tool result"
# -------------------------------------------------------------------
python3 -c "
import base64, importlib, os, sys, types, tempfile, shutil

class _DummyMCP:
    def tool(self, func=None):
        return func if func else (lambda w: w)

fake = types.ModuleType('atlas.mcp_shared.server_factory')
fake.create_stdio_server = lambda name: _DummyMCP()
sys.modules['atlas.mcp_shared.server_factory'] = fake
transfer = importlib.import_module('atlas.mcp.transfer.main')

d = tempfile.mkdtemp()
try:
    os.environ['MCP_TRANSFER_BASE_DIR'] = d

    payload = b'\x00\x01\x02binary\xff\xfe'
    with open(os.path.join(d, 'blob.bin'), 'wb') as f:
        f.write(payload)

    result = transfer.read_file_from_disk('blob.bin')
    assert not result['meta_data']['is_error']
    assert 'content' not in result['results']
    assert 'content_preview' not in result['results']
    assert 'content_base64' not in result['results']
    assert 'truncated' not in result['results']
    assert base64.b64decode(result['artifacts'][0]['b64']) == payload
    print('Binary file: no text injected, bytes only in artifact')
finally:
    shutil.rmtree(d, ignore_errors=True)
" 2>&1
print_result $? "Binary file has no text/base64 in tool result"

# -------------------------------------------------------------------
print_header "Test 4: MCP_TRANSFER_PREVIEW_LINES controls budget"
# -------------------------------------------------------------------
python3 -c "
import importlib, os, sys, types, tempfile, shutil

class _DummyMCP:
    def tool(self, func=None):
        return func if func else (lambda w: w)

fake = types.ModuleType('atlas.mcp_shared.server_factory')
fake.create_stdio_server = lambda name: _DummyMCP()
sys.modules['atlas.mcp_shared.server_factory'] = fake
transfer = importlib.import_module('atlas.mcp.transfer.main')

d = tempfile.mkdtemp()
try:
    os.environ['MCP_TRANSFER_BASE_DIR'] = d
    os.environ['MCP_TRANSFER_PREVIEW_LINES'] = '3'

    lines = [f'row {i}\n' for i in range(30)]
    with open(os.path.join(d, 'data.txt'), 'w') as f:
        f.writelines(lines)

    result = transfer.read_file_from_disk('data.txt')
    content = result['results']['content_preview']
    assert result['results']['truncated'] is True
    assert result['results']['omitted_lines'] == 24
    assert 'row 0\n' in content and 'row 1\n' in content and 'row 2\n' in content
    assert 'row 27\n' in content and 'row 28\n' in content and 'row 29\n' in content
    assert 'row 3\n' not in content and 'row 26\n' not in content
    print('Preview line budget env var verified')
finally:
    shutil.rmtree(d, ignore_errors=True)
" 2>&1
print_result $? "MCP_TRANSFER_PREVIEW_LINES controls head/tail budget"

# -------------------------------------------------------------------
print_header "Test 5: Byte ceiling caps single long-line file"
# -------------------------------------------------------------------
python3 -c "
import base64, importlib, os, sys, types, tempfile, shutil

class _DummyMCP:
    def tool(self, func=None):
        return func if func else (lambda w: w)

fake = types.ModuleType('atlas.mcp_shared.server_factory')
fake.create_stdio_server = lambda name: _DummyMCP()
sys.modules['atlas.mcp_shared.server_factory'] = fake
transfer = importlib.import_module('atlas.mcp.transfer.main')

d = tempfile.mkdtemp()
try:
    os.environ['MCP_TRANSFER_BASE_DIR'] = d
    os.environ['MCP_TRANSFER_PREVIEW_BYTES'] = '200'

    payload = 'X' * 4000 + '\n'
    with open(os.path.join(d, 'minified.txt'), 'w') as f:
        f.write(payload)

    result = transfer.read_file_from_disk('minified.txt')
    assert not result['meta_data']['is_error']
    assert result['results']['truncated'] is True
    assert result['results']['total_lines'] == 1
    assert result['results']['omitted_lines'] == 0
    assert 'content' not in result['results']
    preview = result['results']['content_preview']
    assert 'preview trimmed to 200 bytes' in preview
    assert len(preview.encode('utf-8')) <= 200
    assert base64.b64decode(result['artifacts'][0]['b64']) == payload.encode()
    print('Byte ceiling verified')
finally:
    shutil.rmtree(d, ignore_errors=True)
" 2>&1
print_result $? "MCP_TRANSFER_PREVIEW_BYTES caps single long-line files"

# -------------------------------------------------------------------
print_header "Test 6: _preview_lines helper fallbacks"
# -------------------------------------------------------------------
python3 -c "
import importlib, os, sys, types

class _DummyMCP:
    def tool(self, func=None):
        return func if func else (lambda w: w)

fake = types.ModuleType('atlas.mcp_shared.server_factory')
fake.create_stdio_server = lambda name: _DummyMCP()
sys.modules['atlas.mcp_shared.server_factory'] = fake
transfer = importlib.import_module('atlas.mcp.transfer.main')

default = transfer.DEFAULT_PREVIEW_LINES
os.environ.pop('MCP_TRANSFER_PREVIEW_LINES', None)
assert transfer._preview_lines() == default
os.environ['MCP_TRANSFER_PREVIEW_LINES'] = ''
assert transfer._preview_lines() == default
os.environ['MCP_TRANSFER_PREVIEW_LINES'] = 'abc'
assert transfer._preview_lines() == default
os.environ['MCP_TRANSFER_PREVIEW_LINES'] = '0'
assert transfer._preview_lines() == default
os.environ['MCP_TRANSFER_PREVIEW_LINES'] = '-5'
assert transfer._preview_lines() == default
os.environ['MCP_TRANSFER_PREVIEW_LINES'] = '7'
assert transfer._preview_lines() == 7
print('Helper fallbacks verified')
" 2>&1
print_result $? "_preview_lines fallback branches (unset, empty, abc, 0, -5, valid)"

# -------------------------------------------------------------------
print_header "Test 7: Run transfer MCP unit tests"
# -------------------------------------------------------------------
cd "$ATLAS_DIR"
python3 -m pytest tests/test_mcp_transfer.py -v 2>&1 | tail -30
PYTEST_EXIT=${PIPESTATUS[0]}
print_result $PYTEST_EXIT "Transfer MCP unit tests pass"

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