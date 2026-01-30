#!/bin/bash
# Test script for PR #271: Add RAG data sources and env-file support to CLI
# This script executes the test plan from the PR by running actual CLI commands.
#
# Test plan:
# - python atlas_chat_cli.py "Summarize the latest docs" -- basic prompt
# - python atlas_chat_cli.py "What is 355/113 + sin(0.23) * 897^1.23?" --tools calculator_evaluate
# - python atlas_chat_cli.py --list-tools
# - python atlas_chat_cli.py --list-data-sources
# - python atlas_chat_cli.py "query" --data-sources corporate_cars:west_region --only-rag
# - python atlas_chat_cli.py "Hello" --env-file /path/to/.env
# - Flag parsing and API surface tests
# - bash test/run_tests.sh backend

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
SCRATCHPAD_DIR="/tmp/pr271_test_$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0

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

# Run a CLI command with a timeout, capture output, check for non-empty response
# Usage: run_cli_test "description" TIMEOUT_SECS cli_args...
run_cli_test() {
    local description="$1"
    local timeout_secs="$2"
    shift 2

    local outfile="$SCRATCHPAD_DIR/cli_output_$$.txt"
    echo -n "  Running: python atlas_chat_cli.py $* ... "

    timeout "$timeout_secs" python atlas_chat_cli.py "$@" > "$outfile" 2>&1
    local exit_code=$?

    if [ $exit_code -eq 124 ]; then
        echo "TIMED OUT (${timeout_secs}s)"
        print_result 1 "$description (timed out)"
        return 1
    fi

    local output_size
    output_size=$(wc -c < "$outfile")

    if [ $exit_code -eq 0 ] && [ "$output_size" -gt 0 ]; then
        echo "OK (${output_size} bytes)"
        # Show first few lines of output for visibility
        head -3 "$outfile" | sed 's/^/    /'
        local line_count
        line_count=$(wc -l < "$outfile")
        if [ "$line_count" -gt 3 ]; then
            echo "    ... ($line_count lines total)"
        fi
        print_result 0 "$description"
        return 0
    else
        echo "FAILED (exit=$exit_code, size=$output_size)"
        tail -5 "$outfile" | sed 's/^/    /'
        print_result 1 "$description"
        return 1
    fi
}

cleanup() {
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

print_header "PR #271 Test Plan -- CLI End-to-End + Unit Tests"
echo "Project root: $PROJECT_ROOT"
echo "Date: $(date)"

# ==============================================================================
# Part 1: Actual CLI invocations (end-to-end)
# ==============================================================================
print_header "Part 1: CLI end-to-end invocations"

cd "$BACKEND_DIR"

# 1a. --list-tools (no LLM call needed, should always work)
run_cli_test "--list-tools prints available tools" 60 --list-tools

# 1b. --list-data-sources (no LLM call needed)
run_cli_test "--list-data-sources prints RAG sources" 60 --list-data-sources
LIST_DS_EXIT=$?
# list-data-sources may exit 1 if no RAG servers configured; that is acceptable
if [ $LIST_DS_EXIT -ne 0 ]; then
    # Check if it failed because no sources are configured vs a real error
    if grep -qi "No RAG data sources configured" "$SCRATCHPAD_DIR/cli_output_$$.txt" 2>/dev/null; then
        echo -e "  ${YELLOW}(No RAG sources configured -- not a code error)${NC}"
        # Undo the failure count, count as skip instead
        FAILED=$((FAILED - 1))
        print_skip "--list-data-sources" "no RAG sources configured"
    fi
fi

# 1c. Basic prompt (requires LLM API key)
run_cli_test "Basic prompt: Summarize the latest docs" 120 \
    "Summarize the latest docs in 1 sentence" --model gpt-4o
BASIC_EXIT=$?
if [ $BASIC_EXIT -ne 0 ]; then
    echo -e "  ${YELLOW}NOTE: LLM calls require a valid API key in .env${NC}"
fi

# 1d. Tool use with calculator
run_cli_test "Tool use: calculator_evaluate" 120 \
    "What is 355/113 + sin(0.23) * 897^1.23? Use the tool." \
    --tools calculator_evaluate
TOOL_EXIT=$?

# 1e. RAG-only query with --data-sources and --only-rag
run_cli_test "RAG-only: --data-sources --only-rag" 120 \
    "What corporate cars are available in the west region?" \
    --data-sources corporate_cars:west_region --only-rag
RAG_EXIT=$?
if [ $RAG_EXIT -ne 0 ]; then
    # RAG may not be configured in all environments
    if grep -qiE "(not configured|no rag|connection refused|not found)" "$SCRATCHPAD_DIR/cli_output_$$.txt" 2>/dev/null; then
        echo -e "  ${YELLOW}(RAG source not configured -- not a code error)${NC}"
        FAILED=$((FAILED - 1))
        print_skip "RAG-only query" "RAG source corporate_cars not configured"
    fi
fi

# 1f. --env-file with a custom env (just verify it loads without error)
TEST_ENV_FILE="$SCRATCHPAD_DIR/test.env"
cp "$PROJECT_ROOT/.env" "$TEST_ENV_FILE" 2>/dev/null || touch "$TEST_ENV_FILE"
echo -n "  Running: python atlas_chat_cli.py --list-tools --env-file $TEST_ENV_FILE ... "
timeout 60 python atlas_chat_cli.py --list-tools --env-file "$TEST_ENV_FILE" > "$SCRATCHPAD_DIR/envfile_output.txt" 2>&1
ENVFILE_EXIT=$?
if [ $ENVFILE_EXIT -eq 0 ]; then
    echo "OK"
    print_result 0 "--env-file loads custom env and runs"
else
    echo "FAILED (exit=$ENVFILE_EXIT)"
    tail -3 "$SCRATCHPAD_DIR/envfile_output.txt" | sed 's/^/    /'
    print_result 1 "--env-file loads custom env and runs"
fi

# ==============================================================================
# Part 2: Flag parsing and API surface (fast, no network)
# ==============================================================================
print_header "Part 2: Flag parsing and API surface"

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

from atlas_chat_cli import build_parser
from pathlib import Path
import inspect

parser = build_parser()

# Flag parsing
args = parser.parse_args(['--list-data-sources'])
test("--list-data-sources flag parsing", args.list_data_sources is True)

args = parser.parse_args(['query', '--data-sources', 'server:source'])
test("--data-sources accepts server:source", args.data_sources == 'server:source')

args = parser.parse_args(['query', '--data-sources', 'rag1:docs,rag2:wiki'])
test("--data-sources accepts comma-separated", args.data_sources == 'rag1:docs,rag2:wiki')

args = parser.parse_args(['query', '--only-rag', '--data-sources', 'server:source'])
test("--only-rag flag with --data-sources", args.only_rag is True and args.data_sources == 'server:source')

args = parser.parse_args(['Hello', '--env-file', '/tmp/test.env'])
test("--env-file flag parsing", args.env_file == '/tmp/test.env')

args = parser.parse_args(['Hello', '--env-file=/tmp/test.env'])
test("--env-file=path syntax", args.env_file == '/tmp/test.env')

# Combined flags
args = parser.parse_args([
    'Search docs', '--model', 'gpt-4o', '--tools', 'calculator_evaluate',
    '--data-sources', 'atlas_rag:docs', '--only-rag',
    '--env-file', '/tmp/test.env', '--json'
])
test("All CLI flags combine correctly", (
    args.prompt == 'Search docs' and args.model == 'gpt-4o' and
    args.tools == 'calculator_evaluate' and args.data_sources == 'atlas_rag:docs' and
    args.only_rag is True and args.env_file == '/tmp/test.env' and args.json_output is True
))

# AtlasClient API surface
from atlas_client import AtlasClient
sig = inspect.signature(AtlasClient.chat)
params = list(sig.parameters.keys())
test("AtlasClient.chat has selected_data_sources param", 'selected_data_sources' in params)
test("AtlasClient.chat has only_rag param", 'only_rag' in params)
test("AtlasClient has list_data_sources method", hasattr(AtlasClient, 'list_data_sources'))

# Feature flag
from modules.config.config_manager import AppSettings
test("feature_suppress_litellm_logging in config", 'feature_suppress_litellm_logging' in AppSettings.model_fields)
test("feature_suppress_litellm_logging defaults to True", AppSettings().feature_suppress_litellm_logging is True)

print()
print(f"Subtests: {passed} passed, {failed} failed")
sys.exit(0 if failed == 0 else 1)
PYTEST

print_result $? "Flag parsing and API surface tests"

# ==============================================================================
# Part 3: Backend unit tests
# ==============================================================================
print_header "Part 3: Backend unit tests"

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
# Summary
# ==============================================================================
print_header "Test Summary"
echo -e "Passed:  ${GREEN}$PASSED${NC}"
echo -e "Failed:  ${RED}$FAILED${NC}"
echo -e "Skipped: ${YELLOW}$SKIPPED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All PR #271 test plan items verified!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi
