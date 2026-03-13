#!/usr/bin/env bash
# PR #418 - Fix canvas file download 401 behind reverse proxy
#
# Validates:
# 1. notify_canvas_files includes download_url in canvas file entries
# 2. notify_canvas_files_v2 includes download_url in canvas file entries
# 3. CanvasPanel uses currentFile.download_url (not /api/files/download/)
# 4. download_url uses create_download_url from capabilities module
# 5. Backend test suite passes

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

pass() { echo "  PASSED: $1"; ((PASSED++)); }
fail() { echo "  FAILED: $1"; ((FAILED++)); }

echo "=== PR #418: Canvas File Download 401 Fix ==="
echo ""

# --- Check 1: file_processor imports create_download_url ---
echo "--- Check 1: file_processor imports create_download_url ---"
if grep -q 'from atlas.core.capabilities import create_download_url' "$PROJECT_ROOT/atlas/application/chat/utilities/file_processor.py"; then
    pass "file_processor imports create_download_url"
else
    fail "file_processor missing create_download_url import"
fi

# --- Check 2: notify_canvas_files (v1) includes download_url ---
echo "--- Check 2: notify_canvas_files includes download_url ---"
if grep -q 'download_url.*create_download_url' "$PROJECT_ROOT/atlas/application/chat/utilities/file_processor.py"; then
    pass "download_url field uses create_download_url"
else
    fail "download_url field not found in file_processor"
fi

# --- Check 3: CanvasPanel uses currentFile.download_url ---
echo "--- Check 3: CanvasPanel uses download_url ---"
if grep -q 'currentFile\.download_url' "$PROJECT_ROOT/frontend/src/components/CanvasPanel.jsx"; then
    pass "CanvasPanel uses currentFile.download_url"
else
    fail "CanvasPanel does not use currentFile.download_url"
fi

# --- Check 4: CanvasPanel does NOT hardcode /api/files/download/ ---
echo "--- Check 4: CanvasPanel does not hardcode /api/files/download/ ---"
if grep -q '/api/files/download/' "$PROJECT_ROOT/frontend/src/components/CanvasPanel.jsx"; then
    fail "CanvasPanel still contains hardcoded /api/files/download/ URL"
else
    pass "No hardcoded /api/files/download/ in CanvasPanel"
fi

# --- Check 5: Backend tests pass ---
echo "--- Check 5: Backend tests (capability + download + canvas) ---"
cd "$PROJECT_ROOT"
if PYTHONPATH="$PROJECT_ROOT" .venv/bin/python -m pytest atlas/tests/ -k "capability or download or canvas" --tb=short -q 2>&1 | tail -5; then
    RESULT="${PIPESTATUS[0]}"
    if [ "$RESULT" -eq 0 ]; then
        pass "Backend tests pass"
    else
        fail "Backend tests failed"
    fi
else
    fail "Backend tests failed to run"
fi

# --- Summary ---
echo ""
echo "=== Results: $PASSED passed, $FAILED failed ==="
exit $FAILED
