#!/bin/bash
# Exercise the retained chat pipeline through the real CLI and bundled LLM mock.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/.venv/bin/activate"

WORKDIR="$(mktemp -d /tmp/atlas-pr1018.XXXXXX)"
mkdir -p "$PROJECT_ROOT/config"
CONFIG_DIR="$(mktemp -d "$PROJECT_ROOT/config/pr1018.XXXXXX")"
mkdir -p "$WORKDIR/tmp" "$CONFIG_DIR"
export TMPDIR="$WORKDIR/tmp"
MOCK_PID=""
cleanup() {
    if [ -n "$MOCK_PID" ]; then
        kill "$MOCK_PID" 2>/dev/null || true
        wait "$MOCK_PID" 2>/dev/null || true
    fi
    rm -rf "$WORKDIR/tmp" "$CONFIG_DIR"
    echo "Validation logs: $WORKDIR"
}
trap cleanup EXIT

export MOCK_LLM_PORT="${PR1018_MOCK_PORT:-18118}"
export MOCK_LLM_REQUIRE_AUTH=false
export MCP_TOKEN_ENCRYPTION_KEY
MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export APP_CONFIG_DIR="$CONFIG_DIR"
export APP_LOG_DIR="$WORKDIR/logs"
export DEBUG_MODE=true
export USE_MOCK_S3=true
export FEATURE_CHAT_HISTORY_ENABLED=false
export FEATURE_RAG_ENABLED=false
export FEATURE_COMPLIANCE_LEVELS_ENABLED=false
export FEATURE_AGENT_PORTAL_ENABLED=false
export FEATURE_TOOLS_ENABLED=true
export LLM_CONFIG_FILE=llmconfig.yml
export MCP_CONFIG_FILE=mcp.json
export RAG_SOURCES_CONFIG_FILE=rag-sources.json
export ATLAS_ENV_FILE="$CONFIG_DIR/test.env"
touch "$ATLAS_ENV_FILE"

cat > "$CONFIG_DIR/llmconfig.yml" <<EOF
models:
  cleanup-mock:
    model_name: openai/mock-model
    model_url: http://127.0.0.1:$MOCK_LLM_PORT/v1
    api_key: unused-local-mock
    supports_tools: true
    compliance_level: Internal
EOF
printf '{}\n' > "$CONFIG_DIR/mcp.json"
printf '{}\n' > "$CONFIG_DIR/rag-sources.json"

python "$PROJECT_ROOT/mocks/llm-mock/main.py" > "$WORKDIR/mock.log" 2>&1 &
MOCK_PID=$!
ready=false
for _ in $(seq 1 50); do
    if ! kill -0 "$MOCK_PID" 2>/dev/null; then
        cat "$WORKDIR/mock.log"
        exit 1
    fi
    if curl --silent --fail "http://127.0.0.1:$MOCK_LLM_PORT/health" > /dev/null; then
        ready=true
        break
    fi
    sleep 0.2
done
if [ "$ready" != true ]; then
    cat "$WORKDIR/mock.log"
    echo "FAILED: local LLM mock did not become ready"
    exit 1
fi

atlas-chat "Hello" --model cleanup-mock > "$WORKDIR/plain.txt" 2> "$WORKDIR/plain.log"
grep -q "Hello!" "$WORKDIR/plain.txt"
echo "PASSED: streaming plain chat"

atlas-chat "Hello" --model cleanup-mock --json > "$WORKDIR/plain.json" 2> "$WORKDIR/json.log"
python - "$WORKDIR/plain.json" <<'PY'
import json
import sys

with open(sys.argv[1]) as stream:
    result = json.load(stream)
assert "Hello!" in result["message"], result
PY
echo "PASSED: buffered CLI output still uses the supported turn pipeline"

atlas-chat "Hello" --model cleanup-mock --tools atlas_sleep > "$WORKDIR/tools.txt" 2> "$WORKDIR/tools.log"
grep -q "Hello!" "$WORKDIR/tools.txt"
echo "PASSED: tools mode can finish with a text response"

curl --silent --fail -X POST "http://127.0.0.1:$MOCK_LLM_PORT/test/force-error" \
    -H 'Content-Type: application/json' -d '{"error_type":"auth","count":0}' > /dev/null
if atlas-chat "Hello" --model cleanup-mock > "$WORKDIR/error.txt" 2>&1; then
    echo "FAILED: provider authentication failure must return a nonzero exit status"
    exit 1
fi
# A nonzero exit alone would also pass on an ImportError or uncaught crash.
# (litellm logs handled exceptions with a traceback, so "no Traceback" is not
# a usable signal.) The CLI's own handler prints the classified message as a
# final "Error: ..." line; an uncaught crash never would.
grep -q "^Error: There was an authentication issue with the LLM service" "$WORKDIR/error.txt" || {
    echo "FAILED: provider error was not classified as an authentication failure"
    cat "$WORKDIR/error.txt"
    exit 1
}
echo "PASSED: streaming provider errors still fail the CLI"

# Do not let the smoke test's empty MCP config and disabled features alter the
# suite's defaults. Tests establish their own isolated stores and settings.
(
    unset APP_CONFIG_DIR ATLAS_ENV_FILE APP_LOG_DIR LLM_CONFIG_FILE RAG_SOURCES_CONFIG_FILE
    unset FEATURE_CHAT_HISTORY_ENABLED FEATURE_RAG_ENABLED FEATURE_COMPLIANCE_LEVELS_ENABLED
    unset FEATURE_AGENT_PORTAL_ENABLED FEATURE_TOOLS_ENABLED
    export MCP_CONFIG_FILE=mcp-test.json
    cd "$PROJECT_ROOT/atlas"
    python -m pytest tests -q --tb=short --basetemp="$TMPDIR/pytest"
) 2>&1 | tee "$WORKDIR/backend-tests.log"
