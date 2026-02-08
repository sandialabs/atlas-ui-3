#!/bin/bash
# Test script for PR #291: Fix RAG feature flag and make retrieval best-effort
#
# Covers test plan items:
# - FEATURE_RAG_ENABLED=false disables RAG in CLI discovery
# - FEATURE_RAG_ENABLED=true enables HTTP RAG discovery via mock
# - Best-effort discovery: one failing HTTP RAG source does not block other sources
# - Best-effort retrieval: including a failing source does not fail the overall CLI RAG-only chat
# - Run backend unit tests

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
RAG_MOCK_DIR="$PROJECT_ROOT/mocks/atlas-rag-api-mock"
LLM_MOCK_DIR="$PROJECT_ROOT/mocks/llm-mock"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr291"
SCRATCHPAD_DIR="/tmp/pr291_test_$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
BOLD='\033[1m'

PASSED=0
FAILED=0
RAG_MOCK_PID=""
LLM_MOCK_PID=""
BACKEND_PID=""

print_header() {
    echo ""
    echo -e "${BOLD}==========================================${NC}"
    echo -e "${BOLD}$1${NC}"
    echo -e "${BOLD}==========================================${NC}"
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

wait_http_ok() {
    local url="$1"
    local max_attempts="${2:-80}"
    local sleep_seconds="${3:-0.25}"

    for _ in $(seq 1 "$max_attempts"); do
        if curl -fsS "$url" >/dev/null 2>&1; then
            return 0
        fi
        sleep "$sleep_seconds"
    done
    return 1
}

cleanup() {
    if [ -n "$RAG_MOCK_PID" ] && kill -0 "$RAG_MOCK_PID" 2>/dev/null; then
        kill "$RAG_MOCK_PID" 2>/dev/null || true
        wait "$RAG_MOCK_PID" 2>/dev/null || true
    fi
    if [ -n "$LLM_MOCK_PID" ] && kill -0 "$LLM_MOCK_PID" 2>/dev/null; then
        kill "$LLM_MOCK_PID" 2>/dev/null || true
        wait "$LLM_MOCK_PID" 2>/dev/null || true
    fi
    rm -rf "$SCRATCHPAD_DIR"
}
trap cleanup EXIT

mkdir -p "$SCRATCHPAD_DIR/overrides"

# Activate venv
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
else
    echo -e "${YELLOW}WARNING${NC}: .venv not found; continuing with system python"
fi

# Choose ports that should not collide with developer runs
BASE_PORT=$((18000 + ($$ % 1000)))
RAG_MOCK_PORT="${PR291_RAG_MOCK_PORT:-$((BASE_PORT + 1))}"
LLM_MOCK_PORT="${PR291_LLM_MOCK_PORT:-$((BASE_PORT + 2))}"
RAG_MOCK_URL="http://127.0.0.1:${RAG_MOCK_PORT}"
LLM_MOCK_URL="http://127.0.0.1:${LLM_MOCK_PORT}"

print_header "Setup: Start ATLAS RAG API mock (${RAG_MOCK_URL})"
(
    cd "$RAG_MOCK_DIR"
    export ATLAS_RAG_MOCK_PORT="$RAG_MOCK_PORT"
    export ATLAS_RAG_SHARED_KEY="test-atlas-rag-token"
    bash ./run.sh >"$SCRATCHPAD_DIR/rag_mock.log" 2>&1
) &
RAG_MOCK_PID=$!

wait_http_ok "${RAG_MOCK_URL}/health" 80 0.25
print_result $? "RAG mock is healthy"

print_header "Setup: Start mock LLM (${LLM_MOCK_URL})"
(
    cd "$LLM_MOCK_DIR"
    "$PROJECT_ROOT/.venv/bin/python" -m uvicorn main:app --host 127.0.0.1 --port "$LLM_MOCK_PORT" \
        >"$SCRATCHPAD_DIR/llm_mock.log" 2>&1
) &
LLM_MOCK_PID=$!

wait_http_ok "${LLM_MOCK_URL}/health" 80 0.25
print_result $? "LLM mock is healthy"

write_env_file() {
    local path="$1"
    local feature_rag_enabled="$2"
    cat >"$path" <<EOF
FEATURE_RAG_ENABLED=$feature_rag_enabled
FEATURE_TOOLS_ENABLED=false
FEATURE_SUPPRESS_LITELLM_LOGGING=true
EOF
}

write_llm_config() {
    local dest_path="$1"
    sed "s|__LLM_MOCK_URL__|${LLM_MOCK_URL}|g" \
        "$FIXTURES_DIR/llmconfig.mock.template.yml" \
        > "$dest_path"
}

assert_cli_list_data_sources() {
    local env_file="$1"
    local expected_server="$2"  # empty allowed
    local expect_sources_nonempty="$3"  # true/false

    local out_json="$SCRATCHPAD_DIR/list_data_sources.json"

    (
        cd "$PROJECT_ROOT"
        "$PROJECT_ROOT/.venv/bin/python" atlas/atlas_chat_cli.py \
            --list-data-sources \
            --json \
            --user-email "test@test.com" \
            --env-file "$env_file" \
            --config-overrides "$SCRATCHPAD_DIR/overrides" \
            --rag-sources-config "$SCRATCHPAD_DIR/overrides/rag-sources.json" \
            --llm-config "$SCRATCHPAD_DIR/overrides/llmconfig.yml" \
            >"$out_json" 2>"$SCRATCHPAD_DIR/cli_list_data_sources.stderr"
    )
    local rc=$?
    if [ $rc -ne 0 ]; then
        return 1
    fi

    python - "$expected_server" "$expect_sources_nonempty" "$out_json" <<'PY'
import json
import sys

expected_server = sys.argv[1]
expect_sources_nonempty = sys.argv[2].lower() == "true"
path = sys.argv[3]

with open(path, "r", encoding="utf-8") as f:
    payload = json.load(f)

assert isinstance(payload.get("servers"), dict)
assert isinstance(payload.get("sources"), list)

if expected_server:
    assert expected_server in payload["servers"], payload["servers"]

if expect_sources_nonempty:
    assert len(payload["sources"]) > 0, payload
else:
    assert payload["sources"] == [], payload

print("OK")
PY
}

print_header "Check 1: FEATURE_RAG_ENABLED=false disables backend RAG"

# Write a config that would otherwise work, then prove the flag disables discovery.
sed "s|__RAG_MOCK_URL__|${RAG_MOCK_URL}|g" \
    "$FIXTURES_DIR/rag-sources-feature-flag.template.json" \
    > "$SCRATCHPAD_DIR/overrides/rag-sources.json"

write_llm_config "$SCRATCHPAD_DIR/overrides/llmconfig.yml"
write_env_file "$SCRATCHPAD_DIR/env_rag_false.env" "false"
write_env_file "$SCRATCHPAD_DIR/env_rag_true.env" "true"

assert_cli_list_data_sources "$SCRATCHPAD_DIR/env_rag_false.env" "" "false"
print_result $? "CLI list-data-sources returns empty when FEATURE_RAG_ENABLED=false"

print_header "Check 2: FEATURE_RAG_ENABLED=true enables HTTP RAG discovery"

assert_cli_list_data_sources "$SCRATCHPAD_DIR/env_rag_true.env" "atlas_rag" "true"
print_result $? "CLI list-data-sources discovers HTTP RAG sources when enabled"

print_header "Check 3: Best-effort discovery with failing sources"

# Swap config to include one good HTTP source, one bad HTTP source, and one failing MCP source.
sed "s|__RAG_MOCK_URL__|${RAG_MOCK_URL}|g" \
    "$FIXTURES_DIR/rag-sources-best-effort.template.json" \
    > "$SCRATCHPAD_DIR/overrides/rag-sources.json"

# Expect discovery succeeds (non-empty sources) and includes good_http in server config.
assert_cli_list_data_sources "$SCRATCHPAD_DIR/env_rag_true.env" "good_http" "true"
print_result $? "One failing RAG source does not block discovery of other sources"

# Best-effort retrieval: include a failing source in --data-sources and ensure chat succeeds.
(
    cd "$PROJECT_ROOT"
    "$PROJECT_ROOT/.venv/bin/python" atlas/atlas_chat_cli.py \
        "test" \
        --model "mock-model" \
        --only-rag \
        --data-sources "good_http:engineering-docs,bad_http:engineering-docs" \
        --env-file "$SCRATCHPAD_DIR/env_rag_true.env" \
        --config-overrides "$SCRATCHPAD_DIR/overrides" \
        --rag-sources-config "$SCRATCHPAD_DIR/overrides/rag-sources.json" \
        --llm-config "$SCRATCHPAD_DIR/overrides/llmconfig.yml" \
        >"$SCRATCHPAD_DIR/cli_chat.stdout" 2>"$SCRATCHPAD_DIR/cli_chat.stderr"
)
print_result $? "CLI RAG-only chat succeeds even with one failing source"

print_header "Final: Run backend unit tests"
cd "$PROJECT_ROOT"
./test/run_tests.sh backend
print_result $? "Backend unit tests"

print_header "Summary"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
