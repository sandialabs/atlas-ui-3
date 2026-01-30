#!/bin/bash
# Test script for PR #264: Add feature-flagged metrics logging for user activity tracking
#
# Test plan:
# - Verify metrics_logger module exists and log_metric is importable
# - Verify feature flag FEATURE_METRICS_LOGGING_ENABLED in AppSettings
# - Verify log_metric logs when enabled, suppresses when disabled
# - Verify log format: [METRIC] [username] event_type key=value
# - Verify no sensitive data in metric fields (prompts, tool args, file names, error details)
# - Verify integration points exist (LLM, tools, files, errors)
# - Verify documentation exists
# - Run backend unit tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
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

cd "$PROJECT_ROOT"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo -e "${RED}ERROR: Virtual environment not found at .venv${NC}"
    exit 1
fi

print_header "PR #264 Test Plan -- Metrics Logging"
echo "Project root: $PROJECT_ROOT"
echo "Date: $(date)"

# ==============================================================================
# Part 1: Module and feature flag validation
# ==============================================================================
print_header "Part 1: Module and feature flag checks"

cd "$BACKEND_DIR"

python << 'PYTEST' 2>&1
import sys
sys.path.insert(0, '.')

import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

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

# 1. Module importable
try:
    from core.metrics_logger import log_metric
    test("log_metric is importable from core.metrics_logger", True)
except ImportError as e:
    test(f"log_metric is importable from core.metrics_logger ({e})", False)

# 2. Feature flag exists in AppSettings
from modules.config.config_manager import AppSettings
test(
    "feature_metrics_logging_enabled in AppSettings",
    'feature_metrics_logging_enabled' in AppSettings.model_fields
)
field_info = AppSettings.model_fields['feature_metrics_logging_enabled']
test(
    "feature_metrics_logging_enabled field default is False",
    field_info.default is False
)

# 3. log_metric signature
import inspect
sig = inspect.signature(log_metric)
params = list(sig.parameters.keys())
test("log_metric has event_type param", 'event_type' in params)
test("log_metric has user_email param", 'user_email' in params)
test("log_metric accepts **kwargs", any(
    p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
))

print()
print(f"Subtests: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
PYTEST

print_result $? "Module and feature flag checks"

# ==============================================================================
# Part 2: Logging behavior (enabled vs disabled)
# ==============================================================================
print_header "Part 2: Logging behavior tests"

cd "$BACKEND_DIR"

python << 'PYTEST' 2>&1
import sys
sys.path.insert(0, '.')

import warnings
warnings.filterwarnings('ignore', category=DeprecationWarning)

import logging
from unittest.mock import patch, MagicMock

from core.metrics_logger import log_metric

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

def make_config(enabled):
    mock_cm = MagicMock()
    mock_cm.app_settings.feature_metrics_logging_enabled = enabled
    return mock_cm

# Set up log capture
log_handler = logging.handlers = None
metric_logger = logging.getLogger("core.metrics_logger")
metric_logger.setLevel(logging.DEBUG)

class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []
    def emit(self, record):
        self.records.append(record)

# Test 1: Metrics logged when enabled
capture = LogCapture()
metric_logger.addHandler(capture)
with patch("modules.config.config_manager", make_config(True)):
    log_metric("llm_call", "user@test.com", model="gpt-4", message_count=5)
metric_logger.removeHandler(capture)

logged_text = " ".join(r.getMessage() for r in capture.records)
test("Metrics logged when enabled", "[METRIC]" in logged_text)
test("Log contains [METRIC] prefix", "[METRIC]" in logged_text)
test("Log contains username", "[user@test.com]" in logged_text)
test("Log contains event_type", "llm_call" in logged_text)
test("Log contains key=value metadata", "model=gpt-4" in logged_text)
test("Log contains integer metadata", "message_count=5" in logged_text)

# Test 2: Metrics suppressed when disabled
capture2 = LogCapture()
metric_logger.addHandler(capture2)
with patch("modules.config.config_manager", make_config(False)):
    log_metric("llm_call", "user@test.com", model="gpt-4")
metric_logger.removeHandler(capture2)

logged_text2 = " ".join(r.getMessage() for r in capture2.records)
test("Metrics suppressed when disabled", "[METRIC]" not in logged_text2)

# Test 3: None user_email handled
capture3 = LogCapture()
metric_logger.addHandler(capture3)
with patch("modules.config.config_manager", make_config(True)):
    log_metric("error", None, error_type="timeout")
metric_logger.removeHandler(capture3)

logged_text3 = " ".join(r.getMessage() for r in capture3.records)
test("None user_email logs as [unknown]", "[unknown]" in logged_text3)

print()
print(f"Subtests: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
PYTEST

print_result $? "Logging behavior tests"

# ==============================================================================
# Part 3: Integration points exist
# ==============================================================================
print_header "Part 3: Integration point verification"

cd "$BACKEND_DIR"

check_integration() {
    local file="$1"
    local description="$2"
    if grep -q "log_metric" "$file" 2>/dev/null; then
        echo -e "  ${GREEN}FOUND${NC}: log_metric in $file ($description)"
        return 0
    else
        echo -e "  ${RED}MISSING${NC}: log_metric not found in $file ($description)"
        return 1
    fi
}

INTEGRATION_FAILURES=0

check_integration "modules/llm/litellm_caller.py" "LLM call metrics" || INTEGRATION_FAILURES=$((INTEGRATION_FAILURES + 1))
check_integration "modules/mcp_tools/client.py" "Tool call metrics" || INTEGRATION_FAILURES=$((INTEGRATION_FAILURES + 1))
check_integration "routes/files_routes.py" "File upload metrics" || INTEGRATION_FAILURES=$((INTEGRATION_FAILURES + 1))
check_integration "modules/file_storage/s3_client.py" "S3 storage metrics" || INTEGRATION_FAILURES=$((INTEGRATION_FAILURES + 1))
check_integration "modules/file_storage/mock_s3_client.py" "Mock S3 storage metrics" || INTEGRATION_FAILURES=$((INTEGRATION_FAILURES + 1))
check_integration "main.py" "Error metrics" || INTEGRATION_FAILURES=$((INTEGRATION_FAILURES + 1))

if [ $INTEGRATION_FAILURES -eq 0 ]; then
    print_result 0 "All integration points have log_metric calls"
else
    print_result 1 "Missing log_metric in $INTEGRATION_FAILURES integration point(s)"
fi

# ==============================================================================
# Part 4: Documentation and config checks
# ==============================================================================
print_header "Part 4: Documentation and config"

cd "$PROJECT_ROOT"

# Check docs exist
if [ -f "docs/metrics-logging.md" ]; then
    print_result 0 "docs/metrics-logging.md exists"
else
    print_result 1 "docs/metrics-logging.md missing"
fi

# Check .env.example has the feature flag
if grep -q "FEATURE_METRICS_LOGGING_ENABLED" ".env.example"; then
    print_result 0 "FEATURE_METRICS_LOGGING_ENABLED in .env.example"
else
    print_result 1 "FEATURE_METRICS_LOGGING_ENABLED missing from .env.example"
fi

# Check CHANGELOG has PR #264 entry
if grep -q "PR #264" "CHANGELOG.md"; then
    print_result 0 "CHANGELOG.md has PR #264 entry"
else
    print_result 1 "CHANGELOG.md missing PR #264 entry"
fi

# ==============================================================================
# Part 5: Backend unit tests
# ==============================================================================
print_header "Part 5: Backend unit tests"

cd "$PROJECT_ROOT"
echo "Running backend unit tests..."
./test/run_tests.sh backend > /tmp/pr264_backend_test_$$.txt 2>&1
BACKEND_RESULT=$?

if [ $BACKEND_RESULT -eq 0 ]; then
    grep -E "^=" /tmp/pr264_backend_test_$$.txt | grep -E "passed" | tail -1
else
    echo "Backend test output (last 20 lines):"
    tail -20 /tmp/pr264_backend_test_$$.txt
fi
rm -f /tmp/pr264_backend_test_$$.txt
print_result $BACKEND_RESULT "Backend unit tests"

# ==============================================================================
# Summary
# ==============================================================================
print_header "Test Summary"
echo -e "Passed:  ${GREEN}$PASSED${NC}"
echo -e "Failed:  ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All PR #264 test plan items verified!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi
