#!/bin/bash
# PR #493 - plain_text_types fast path for direct text file reading
# Date: 2026-04-02

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

cd "$PROJECT_ROOT"
source .venv/bin/activate 2>/dev/null || true

echo "=== PR #493: plain_text_types fast path ==="
echo ""

# ---------------------------------------------------------------------------
# 1. Config: plain_text_types key shipped in default file-extractors.json
# ---------------------------------------------------------------------------
echo "--- Check 1: file-extractors.json defines plain_text_types ---"
if python -c "
import json
with open('atlas/config/file-extractors.json') as f:
    c = json.load(f)
assert 'plain_text_types' in c, 'plain_text_types key missing'
assert isinstance(c['plain_text_types'], list), 'plain_text_types must be a list'
assert len(c['plain_text_types']) > 0, 'plain_text_types is empty'
print('OK')
" 2>&1 | grep -q '^OK$'; then
    pass "file-extractors.json has non-empty plain_text_types list"
else
    fail "file-extractors.json missing or empty plain_text_types"
fi

# ---------------------------------------------------------------------------
# 2. Security: .env and .pem must NOT be in default plain_text_types
# ---------------------------------------------------------------------------
echo "--- Check 2: .env not in default plain_text_types ---"
if python -c "
import json
with open('atlas/config/file-extractors.json') as f:
    c = json.load(f)
types = [t.lower() for t in c.get('plain_text_types', [])]
assert '.env' not in types, '.env must not be in plain_text_types'
assert '.pem' not in types, '.pem must not be in plain_text_types'
assert '.key' not in types, '.key must not be in plain_text_types'
print('OK')
" 2>&1 | grep -q '^OK$'; then
    pass "Secret-bearing extensions excluded from default list"
else
    fail ".env / .pem / .key appear in default plain_text_types"
fi

# ---------------------------------------------------------------------------
# 3. Overlap validator rejects bad config
# ---------------------------------------------------------------------------
echo "--- Check 3: overlap between plain_text_types and extension_mapping is rejected ---"
if python -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from atlas.modules.config.config_manager import FileExtractorsConfig, FileExtractorConfig
try:
    FileExtractorsConfig(
        enabled=True,
        plain_text_types=['.pdf'],
        extension_mapping={'.pdf': 'pdf-text'},
        extractors={'pdf-text': FileExtractorConfig(url='http://localhost/pdf', enabled=True)},
    )
    print('FAIL: overlap was not rejected')
except Exception:
    print('OK')
" 2>&1 | grep -q '^OK$'; then
    pass "Overlap validator correctly rejects bad config"
else
    fail "Overlap validator did not reject overlap between plain_text_types and extension_mapping"
fi

# ---------------------------------------------------------------------------
# 4. Lowercase normalization of plain_text_types
# ---------------------------------------------------------------------------
echo "--- Check 4: plain_text_types normalized to lowercase on load ---"
if python -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from atlas.modules.config.config_manager import FileExtractorsConfig
cfg = FileExtractorsConfig(plain_text_types=['.TXT', '.PY', '.C'])
assert cfg.plain_text_types == ['.txt', '.py', '.c'], f'got: {cfg.plain_text_types}'
print('OK')
" 2>&1 | grep -q '^OK$'; then
    pass "Extensions normalized to lowercase"
else
    fail "Lowercase normalization broken"
fi

# ---------------------------------------------------------------------------
# 5. End-to-end: extract_content fast path decodes plain text without HTTP
# ---------------------------------------------------------------------------
echo "--- Check 5: extract_content fast path returns decoded text ---"
if python -c "
import sys, asyncio, base64
from unittest.mock import patch, Mock
sys.path.insert(0, '$PROJECT_ROOT')
from atlas.modules.config.config_manager import FileExtractorsConfig
from atlas.modules.file_storage.content_extractor import FileContentExtractor

cfg = FileExtractorsConfig(enabled=True, plain_text_types=['.py', '.c'])
ex = FileContentExtractor(config=cfg)

source = \"print('hello, plain text fast path')\n\"
encoded = base64.b64encode(source.encode()).decode()

settings = Mock()
settings.feature_file_content_extraction_enabled = True

with patch('atlas.modules.file_storage.content_extractor.get_app_settings', return_value=settings):
    with patch('httpx.AsyncClient') as mock_http:
        result = asyncio.run(ex.extract_content('demo.py', encoded))
        assert result.success, f'extract failed: {result.error}'
        assert result.content == source, f'content mismatch: {result.content!r}'
        assert result.metadata == {'method': 'plain_text_read'}, f'bad metadata: {result.metadata}'
        mock_http.assert_not_called()
print('OK')
" 2>&1 | grep -q '^OK$'; then
    pass "Fast path decodes content without calling HTTP extractor"
else
    fail "Fast path broken"
fi

# ---------------------------------------------------------------------------
# 6. Size guard: oversized plain-text file is rejected
# ---------------------------------------------------------------------------
echo "--- Check 6: max_plain_text_size_mb guard enforced ---"
if python -c "
import sys, asyncio, base64
from unittest.mock import patch, Mock
sys.path.insert(0, '$PROJECT_ROOT')
from atlas.modules.config.config_manager import FileExtractorsConfig
from atlas.modules.file_storage.content_extractor import FileContentExtractor

cfg = FileExtractorsConfig(enabled=True, plain_text_types=['.txt'], max_plain_text_size_mb=1)
ex = FileContentExtractor(config=cfg)

big = ('A' * 1_500_000).encode()
encoded = base64.b64encode(big).decode()

settings = Mock()
settings.feature_file_content_extraction_enabled = True

with patch('atlas.modules.file_storage.content_extractor.get_app_settings', return_value=settings):
    result = asyncio.run(ex.extract_content('big.txt', encoded))
    assert not result.success, 'oversized file should be rejected'
    assert 'too large' in result.error.lower(), f'wrong error: {result.error}'
print('OK')
" 2>&1 | grep -q '^OK$'; then
    pass "Size guard rejects oversized plain-text files"
else
    fail "Size guard not enforced"
fi

# ---------------------------------------------------------------------------
# 7. /api/config exposes plain_text_types in supported_extensions
# ---------------------------------------------------------------------------
echo "--- Check 7: /api/config includes plain_text_types in supported_extensions ---"
if python -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/atlas')
sys.path.insert(0, '$PROJECT_ROOT')
from starlette.testclient import TestClient
from main import app
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config.config_manager import FileExtractorsConfig, FileExtractorConfig

cm = app_factory.get_config_manager()
orig_feat = cm.app_settings.feature_file_content_extraction_enabled
orig_cfg = cm._file_extractors_config
try:
    cm.app_settings.feature_file_content_extraction_enabled = True
    cm._file_extractors_config = FileExtractorsConfig(
        enabled=True,
        extractors={'pdf-text': FileExtractorConfig(url='http://localhost/pdf', enabled=True)},
        extension_mapping={'.pdf': 'pdf-text'},
        plain_text_types=['.py', '.txt', '.md'],
    )
    with TestClient(app) as client:
        r = client.get('/api/config', headers={'X-User-Email': 'test@test.com'})
        assert r.status_code == 200, f'got {r.status_code}'
        exts = r.json()['file_extraction']['supported_extensions']
        for e in ('.py', '.txt', '.md', '.pdf'):
            assert e in exts, f'{e} missing from supported_extensions: {exts}'
finally:
    cm.app_settings.feature_file_content_extraction_enabled = orig_feat
    cm._file_extractors_config = orig_cfg
print('OK')
" 2>&1 | grep -q '^OK$'; then
    pass "/api/config surfaces plain_text_types in supported_extensions"
else
    fail "/api/config does not include plain_text_types"
fi

# ---------------------------------------------------------------------------
# 8. CHANGELOG references PR #493
# ---------------------------------------------------------------------------
echo "--- Check 8: CHANGELOG references PR #493 ---"
if grep -q 'PR #493' CHANGELOG.md; then
    pass "CHANGELOG references PR #493"
else
    fail "CHANGELOG does not reference PR #493"
fi

# ---------------------------------------------------------------------------
# 9. Run the relevant backend test classes
# ---------------------------------------------------------------------------
echo ""
echo "--- Check 9: Backend unit tests pass ---"
if python -m pytest \
    atlas/tests/test_file_content_extraction.py::TestPlainTextTypes \
    atlas/tests/test_file_extraction_routes.py \
    -v --tb=short 2>&1; then
    pass "Backend tests pass"
else
    fail "Backend tests failed"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Summary: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
