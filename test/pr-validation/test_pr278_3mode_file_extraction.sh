#!/bin/bash
# Test script for PR #278: Add 3-mode file extraction (full / preview / none)
#
# Test plan:
# - E2E: Start backend, hit /api/config, verify file_extraction.default_behavior is "full"
# - E2E: Start backend with feature disabled, verify default_behavior is "none"
# - E2E: Verify legacy normalization via Python (extract->full, attach_only->none)
# - E2E: Verify 3-mode build_files_manifest output via Python
# - E2E: Verify handle_session_files respects extractMode field
# - Run backend unit tests
# - Run frontend unit tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
SCRATCHPAD_DIR="/tmp/pr278_test_$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0
BACKEND_PID=""

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
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill "$BACKEND_PID" 2>/dev/null
        wait "$BACKEND_PID" 2>/dev/null
    fi
    rm -rf "$SCRATCHPAD_DIR"
}
trap cleanup EXIT

mkdir -p "$SCRATCHPAD_DIR"
cd "$PROJECT_ROOT"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo -e "${RED}ERROR: Virtual environment not found at .venv${NC}"
    exit 1
fi

print_header "PR #278 Test Plan -- 3-Mode File Extraction"
echo "Project root: $PROJECT_ROOT"
echo "Date: $(date)"

# ==============================================================================
# Part 1: E2E -- Backend with extraction enabled, verify default_behavior="full"
# ==============================================================================
print_header "Part 1: E2E -- Backend /api/config returns default_behavior=full"

E2E_LOG_DIR="$SCRATCHPAD_DIR/logs_enabled"
E2E_PORT=18278
mkdir -p "$E2E_LOG_DIR"

cd "$ATLAS_DIR"

echo "  Starting backend on port $E2E_PORT with FEATURE_FILE_CONTENT_EXTRACTION_ENABLED=true..."
FEATURE_FILE_CONTENT_EXTRACTION_ENABLED=true \
APP_LOG_DIR="$E2E_LOG_DIR" \
PORT=$E2E_PORT \
LOG_LEVEL=INFO \
python main.py > "$SCRATCHPAD_DIR/backend_enabled.log" 2>&1 &
BACKEND_PID=$!

READY=0
for i in $(seq 1 30); do
    if curl -s "http://127.0.0.1:$E2E_PORT/api/config" > /dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 1
done

if [ $READY -eq 1 ]; then
    echo "  Backend started (PID=$BACKEND_PID)"

    curl -s "http://127.0.0.1:$E2E_PORT/api/config" \
        -H "X-User-Email: test@test.com" \
        > "$SCRATCHPAD_DIR/config_enabled.json" 2>&1
    print_result 0 "Backend /api/config endpoint responds"

    # Check default_behavior is "full" (not legacy "extract")
    BEHAVIOR=$(python -c "import json; d=json.load(open('$SCRATCHPAD_DIR/config_enabled.json')); print(d.get('file_extraction',{}).get('default_behavior','MISSING'))")
    if [ "$BEHAVIOR" = "full" ]; then
        print_result 0 "default_behavior is 'full' when extraction enabled"
    else
        echo "  Got default_behavior='$BEHAVIOR', expected 'full'"
        print_result 1 "default_behavior is 'full' when extraction enabled"
    fi

    kill "$BACKEND_PID" 2>/dev/null
    wait "$BACKEND_PID" 2>/dev/null
    BACKEND_PID=""
else
    echo -e "  ${RED}Backend did not start within 30s${NC}"
    tail -10 "$SCRATCHPAD_DIR/backend_enabled.log" 2>/dev/null | sed 's/^/    /'
    kill "$BACKEND_PID" 2>/dev/null
    wait "$BACKEND_PID" 2>/dev/null
    BACKEND_PID=""
    print_result 1 "Backend starts with extraction enabled"
fi

# ==============================================================================
# Part 2: E2E -- Backend with extraction disabled, verify default_behavior="none"
# ==============================================================================
print_header "Part 2: E2E -- Backend /api/config returns default_behavior=none when disabled"

E2E_LOG_DIR_OFF="$SCRATCHPAD_DIR/logs_disabled"
E2E_PORT_OFF=18279
mkdir -p "$E2E_LOG_DIR_OFF"

cd "$ATLAS_DIR"

echo "  Starting backend on port $E2E_PORT_OFF with FEATURE_FILE_CONTENT_EXTRACTION_ENABLED=false..."
FEATURE_FILE_CONTENT_EXTRACTION_ENABLED=false \
APP_LOG_DIR="$E2E_LOG_DIR_OFF" \
PORT=$E2E_PORT_OFF \
LOG_LEVEL=INFO \
python main.py > "$SCRATCHPAD_DIR/backend_disabled.log" 2>&1 &
BACKEND_PID=$!

READY=0
for i in $(seq 1 30); do
    if curl -s "http://127.0.0.1:$E2E_PORT_OFF/api/config" > /dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 1
done

if [ $READY -eq 1 ]; then
    echo "  Backend started (PID=$BACKEND_PID)"

    curl -s "http://127.0.0.1:$E2E_PORT_OFF/api/config" \
        -H "X-User-Email: test@test.com" \
        > "$SCRATCHPAD_DIR/config_disabled.json" 2>&1

    BEHAVIOR_OFF=$(python -c "import json; d=json.load(open('$SCRATCHPAD_DIR/config_disabled.json')); print(d.get('file_extraction',{}).get('default_behavior','MISSING'))")
    if [ "$BEHAVIOR_OFF" = "none" ]; then
        print_result 0 "default_behavior is 'none' when extraction disabled"
    else
        echo "  Got default_behavior='$BEHAVIOR_OFF', expected 'none'"
        print_result 1 "default_behavior is 'none' when extraction disabled"
    fi

    kill "$BACKEND_PID" 2>/dev/null
    wait "$BACKEND_PID" 2>/dev/null
    BACKEND_PID=""
else
    echo -e "  ${RED}Backend did not start within 30s${NC}"
    tail -10 "$SCRATCHPAD_DIR/backend_disabled.log" 2>/dev/null | sed 's/^/    /'
    kill "$BACKEND_PID" 2>/dev/null
    wait "$BACKEND_PID" 2>/dev/null
    BACKEND_PID=""
    print_result 1 "Backend starts with extraction disabled"
fi

# ==============================================================================
# Part 3: E2E -- Legacy normalization and 3-mode config via actual code path
# ==============================================================================
print_header "Part 3: E2E -- Legacy normalization via actual config model"

cd "$ATLAS_DIR"

python << 'PYTEST' 2>&1
import sys, os
sys.path.insert(0, '.')

import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

from modules.config.config_manager import FileExtractorsConfig

passed = 0
failed = 0

def test(name, condition):
    global passed, failed
    if condition:
        print(f"  PASSED: {name}")
        passed += 1
    else:
        print(f"  FAILED: {name}")
        failed += 1

# Legacy normalization
c1 = FileExtractorsConfig(default_behavior="extract")
test("Legacy 'extract' normalizes to 'full'", c1.default_behavior == "full")

c2 = FileExtractorsConfig(default_behavior="attach_only")
test("Legacy 'attach_only' normalizes to 'none'", c2.default_behavior == "none")

# New values pass through
c3 = FileExtractorsConfig(default_behavior="full")
test("'full' passes through unchanged", c3.default_behavior == "full")

c4 = FileExtractorsConfig(default_behavior="preview")
test("'preview' passes through unchanged", c4.default_behavior == "preview")

c5 = FileExtractorsConfig(default_behavior="none")
test("'none' passes through unchanged", c5.default_behavior == "none")

# Default value
c6 = FileExtractorsConfig()
test("Default is 'full'", c6.default_behavior == "full")

print()
print(f"Subtests: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
PYTEST

print_result $? "Legacy normalization and 3-mode config values"

# ==============================================================================
# Part 4: E2E -- build_files_manifest produces correct output for each mode
# ==============================================================================
print_header "Part 4: E2E -- build_files_manifest for full/preview/none modes"

cd "$ATLAS_DIR"

python << 'PYTEST' 2>&1
import sys, os
sys.path.insert(0, '.')

import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

from application.chat.utilities.file_processor import build_files_manifest

passed = 0
failed = 0

def test(name, condition):
    global passed, failed
    if condition:
        print(f"  PASSED: {name}")
        passed += 1
    else:
        print(f"  FAILED: {name}")
        failed += 1

# Test full mode -- content wrapped in markers, no truncation
ctx_full = {
    "files": {
        "report.pdf": {
            "extract_mode": "full",
            "extracted_content": "Full document content here with lots of text.",
            "extracted_preview": "Full document...",
        }
    }
}
manifest_full = build_files_manifest(ctx_full)
content = manifest_full["content"]
test("Full mode: has content start marker", "<< content of file report.pdf >>" in content)
test("Full mode: has content end marker", "<< end content of file report.pdf >>" in content)
test("Full mode: includes full content", "Full document content here with lots of text." in content)
test("Full mode: context note mentions full content", "fully extracted" in content)

# Test preview mode -- truncated preview
ctx_preview = {
    "files": {
        "notes.txt": {
            "extract_mode": "preview",
            "extracted_content": "Very long content " * 100,
            "extracted_preview": "Line1\nLine2\nLine3",
        }
    }
}
manifest_preview = build_files_manifest(ctx_preview)
content_p = manifest_preview["content"]
test("Preview mode: has Content preview label", "Content preview:" in content_p)
test("Preview mode: no content markers", "<< content of file" not in content_p)
test("Preview mode: context note mentions partially", "partially analyzed" in content_p)

# Test none mode -- filename only
ctx_none = {
    "files": {
        "data.csv": {
            "extract_mode": "none",
        }
    }
}
manifest_none = build_files_manifest(ctx_none)
content_n = manifest_none["content"]
test("None mode: lists filename", "data.csv" in content_n)
test("None mode: no content markers", "<< content of file" not in content_n)
test("None mode: no Content preview", "Content preview:" not in content_n)
test("None mode: context note mentions name only", "name only" in content_n)

# Test mixed modes in one manifest
ctx_mixed = {
    "files": {
        "a.pdf": {
            "extract_mode": "full",
            "extracted_content": "Full PDF text",
            "extracted_preview": "Full PDF...",
        },
        "b.txt": {
            "extract_mode": "preview",
            "extracted_preview": "Some preview text",
        },
        "c.bin": {
            "extract_mode": "none",
        }
    }
}
manifest_mixed = build_files_manifest(ctx_mixed)
content_m = manifest_mixed["content"]
test("Mixed: full content marker for a.pdf", "<< content of file a.pdf >>" in content_m)
test("Mixed: preview label for b.txt", "Content preview:" in content_m)
test("Mixed: c.bin listed", "c.bin" in content_m)
test("Mixed: context note mentions fully extracted", "fully extracted" in content_m)
test("Mixed: context note mentions partially analyzed", "partially analyzed" in content_m)
test("Mixed: context note mentions name only", "name only" in content_m)

print()
print(f"Subtests: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
PYTEST

print_result $? "build_files_manifest for full/preview/none modes"

# ==============================================================================
# Part 5: E2E -- handle_session_files respects extractMode from wire format
# ==============================================================================
print_header "Part 5: E2E -- handle_session_files respects extractMode field"

cd "$ATLAS_DIR"

python << 'PYTEST' 2>&1
import sys, os, asyncio
sys.path.insert(0, '.')

import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

from unittest.mock import AsyncMock, MagicMock, patch

passed = 0
failed = 0

def test(name, condition):
    global passed, failed
    if condition:
        print(f"  PASSED: {name}")
        passed += 1
    else:
        print(f"  FAILED: {name}")
        failed += 1

async def run_tests():
    from application.chat.utilities.file_processor import handle_session_files

    # Mock file_manager
    mock_fm = MagicMock()
    mock_fm.upload_file = AsyncMock(return_value={
        "key": "test/key",
        "content_type": "application/pdf",
        "size": 100,
        "last_modified": "2026-01-30"
    })

    # Mock extractor to be disabled (so extraction does not actually run)
    mock_extractor = MagicMock()
    mock_extractor.is_enabled.return_value = False
    mock_extractor.get_default_behavior.return_value = "full"

    with patch('application.chat.utilities.file_processor.get_content_extractor', return_value=mock_extractor):
        # Test new extractMode field
        ctx = await handle_session_files(
            session_context={},
            user_email="user@test.com",
            files_map={
                "doc.pdf": {"content": "dGVzdA==", "extractMode": "preview"},
            },
            file_manager=mock_fm
        )
        mode = ctx["files"]["doc.pdf"].get("extract_mode")
        test("extractMode='preview' from wire format stored correctly", mode == "preview")

        # Test legacy extract=true maps to full
        ctx2 = await handle_session_files(
            session_context={},
            user_email="user@test.com",
            files_map={
                "old.pdf": {"content": "dGVzdA==", "extract": True},
            },
            file_manager=mock_fm
        )
        mode2 = ctx2["files"]["old.pdf"].get("extract_mode")
        test("Legacy extract=true maps to 'full'", mode2 == "full")

        # Test legacy extract=false maps to none
        ctx3 = await handle_session_files(
            session_context={},
            user_email="user@test.com",
            files_map={
                "skip.pdf": {"content": "dGVzdA==", "extract": False},
            },
            file_manager=mock_fm
        )
        mode3 = ctx3["files"]["skip.pdf"].get("extract_mode")
        test("Legacy extract=false maps to 'none'", mode3 == "none")

        # Test string-only (legacy format) uses default
        mock_extractor.get_default_behavior.return_value = "none"
        ctx4 = await handle_session_files(
            session_context={},
            user_email="user@test.com",
            files_map={
                "raw.pdf": "dGVzdA==",
            },
            file_manager=mock_fm
        )
        mode4 = ctx4["files"]["raw.pdf"].get("extract_mode")
        test("String-only legacy format uses default_extract_mode='none'", mode4 == "none")

asyncio.run(run_tests())

print()
print(f"Subtests: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
PYTEST

print_result $? "handle_session_files respects extractMode field"

# ==============================================================================
# Part 6: Backend unit tests
# ==============================================================================
print_header "Part 6: Backend unit tests"

cd "$PROJECT_ROOT"
echo "Running backend unit tests..."
./test/run_tests.sh backend > "$SCRATCHPAD_DIR/backend_test_output.txt" 2>&1
BACKEND_RESULT=$?

if [ $BACKEND_RESULT -eq 0 ]; then
    grep -E "^=" "$SCRATCHPAD_DIR/backend_test_output.txt" | grep -E "passed" | tail -1
else
    echo "Backend test output (last 20 lines):"
    tail -20 "$SCRATCHPAD_DIR/backend_test_output.txt"
fi
print_result $BACKEND_RESULT "Backend unit tests"

# ==============================================================================
# Part 7: Frontend unit tests
# ==============================================================================
print_header "Part 7: Frontend unit tests"

cd "$PROJECT_ROOT"
echo "Running frontend unit tests..."
./test/run_tests.sh frontend > "$SCRATCHPAD_DIR/frontend_test_output.txt" 2>&1
FRONTEND_RESULT=$?

if [ $FRONTEND_RESULT -eq 0 ]; then
    grep -E "Tests.*passed" "$SCRATCHPAD_DIR/frontend_test_output.txt" | tail -1
else
    echo "Frontend test output (last 20 lines):"
    tail -20 "$SCRATCHPAD_DIR/frontend_test_output.txt"
fi
print_result $FRONTEND_RESULT "Frontend unit tests"

# ==============================================================================
# Summary
# ==============================================================================
print_header "Test Summary"
echo -e "Passed:  ${GREEN}$PASSED${NC}"
echo -e "Failed:  ${RED}$FAILED${NC}"
echo -e "Skipped: ${YELLOW}$SKIPPED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All PR #278 test plan items verified!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi
