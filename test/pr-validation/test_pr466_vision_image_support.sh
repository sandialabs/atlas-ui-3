#!/bin/bash
# Test script for PR #466: Vision image support for multimodal LLM models
#
# Test plan:
# - Verify ModelConfig recognizes supports_vision field
# - Verify handle_session_files stores image_b64 for vision models
# - Verify build_files_manifest excludes vision images when requested
# - Verify _build_vision_user_message creates multimodal content blocks
# - Verify MessageBuilder integrates vision images into messages
# - Verify /api/config exposes supports_vision to the frontend
# - Full vision test suite passes

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr466"

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

print_header "PR #466: Vision Image Support Tests"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# Load fixture env
if [ -f "$FIXTURES_DIR/.env" ]; then
    set -a
    source "$FIXTURES_DIR/.env"
    set +a
fi

print_header "1. ModelConfig supports_vision field"
cd "$ATLAS_DIR" && python -m pytest tests/test_vision_image_support.py::TestModelConfigSupportsVision -v --tb=short 2>&1
print_result $? "ModelConfig correctly recognizes supports_vision=true and defaults to false"

print_header "2. handle_session_files stores image data for vision models"
cd "$ATLAS_DIR" && python -m pytest tests/test_vision_image_support.py::TestHandleSessionFilesVision -v --tb=short 2>&1
print_result $? "Image base64 and MIME type stored in session context for vision models"

print_header "3. build_files_manifest excludes vision images"
cd "$ATLAS_DIR" && python -m pytest tests/test_vision_image_support.py::TestBuildFilesManifestVision -v --tb=short 2>&1
print_result $? "Files manifest excludes image files when exclude_vision_images=True"

print_header "4. _build_vision_user_message creates multimodal blocks"
cd "$ATLAS_DIR" && python -m pytest tests/test_vision_image_support.py::TestBuildVisionUserMessage -v --tb=short 2>&1
print_result $? "Multimodal user message contains text and image_url content blocks"

print_header "5. MessageBuilder integrates vision images"
cd "$ATLAS_DIR" && python -m pytest tests/test_vision_image_support.py::TestMessageBuilderVisionIntegration -v --tb=short 2>&1
print_result $? "MessageBuilder attaches inline images to last user message for vision models"

print_header "6. SVG files excluded from vision embedding"
cd "$ATLAS_DIR" && python -m pytest tests/test_vision_image_support.py -k "svg" -v --tb=short 2>&1
print_result $? "SVG files are excluded from vision image embedding"

print_header "7. Stale image cleanup between turns"
cd "$ATLAS_DIR" && python -m pytest tests/test_vision_image_support.py -k "stale" -v --tb=short 2>&1
print_result $? "Stale image data cleared from session context between turns"

print_header "8. Verify supports_vision exposed in config API"
cd "$PROJECT_ROOT" && python -c "
from atlas.modules.config.config_manager import ModelConfig

# Model with vision
m1 = ModelConfig(model_name='gpt-4o', model_url='http://x', supports_vision=True)
assert m1.supports_vision is True, 'supports_vision=True should be preserved'

# Model without vision (default)
m2 = ModelConfig(model_name='gpt-3', model_url='http://x')
assert m2.supports_vision is False, 'supports_vision should default to False'

# Verify serialization includes the field
d = m1.model_dump()
assert 'supports_vision' in d, 'supports_vision should be in model dump'
assert d['supports_vision'] is True

print('supports_vision field serializes correctly for API exposure')
" 2>&1
print_result $? "supports_vision field exposed correctly via ModelConfig serialization"

print_header "9. Full vision test suite"
cd "$ATLAS_DIR" && python -m pytest tests/test_vision_image_support.py -v --tb=short 2>&1
print_result $? "All vision image support tests pass"

print_header "10. Backend test suite (no regressions)"
cd "$PROJECT_ROOT" && bash test/run_tests.sh backend 2>&1
print_result $? "Backend test suite"

echo ""
echo "=========================================="
echo "RESULTS: ${PASSED} passed, ${FAILED} failed"
echo "=========================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
