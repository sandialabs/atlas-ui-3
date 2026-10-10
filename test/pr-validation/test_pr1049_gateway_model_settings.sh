#!/bin/bash
# Test script for PR #1049: per-model settings in a LiteLLM team gateway's
# models allowlist (issue #1048)
#
# Test plan:
# - Start the mock LiteLLM proxy (mocks/litellm-mock) and the real backend
#   with a fixture gateway whose allowlisted models set their own
#   supports_tools / supports_vision / supports_pdf / max_tokens
# - /api/config/shell gives the gateway's defaults and each allowlisted
#   model's own capabilities (model_capabilities)
# - GET /api/llm/gateways/enterprise/models gives each team model's
#   capabilities, a model the allowlist leaves unset taking the defaults
# - atlas-chat through two models of one team reaches the mock with each
#   model's own max_tokens
# - A WebSocket chat turn with a PDF attached, as the web UI sends it, reaches
#   the mock as an inline document for claude-sonnet (supports_pdf) and not
#   for gpt-4o-mini (the default, false)
# - A gateway model entry with a reserved field, an unknown field or an
#   invalid value is refused when the config loads, each with its own error,
#   and an unknown field in model_defaults is refused too
# - The backend refuses to start with an unknown field in model_defaults,
#   naming the field (the breaking change in changes/1048.breaking.md)
# - Run the backend unit test suite

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr1049"

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

cd "$PROJECT_ROOT"
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"

WORK_DIR=$(mktemp -d -t atlas-pr1049-XXXXXX)
CONFIG_DIR="$WORK_DIR/config"
LOG_DIR="$WORK_DIR/logs"
mkdir -p "$CONFIG_DIR" "$LOG_DIR"
cp "$FIXTURES_DIR/llmconfig.yml" "$CONFIG_DIR/llmconfig.yml"
echo '{}' > "$CONFIG_DIR/mcp.json"
MOCK_PID=""
BACKEND_PID=""
trap 'kill $BACKEND_PID $MOCK_PID 2>/dev/null || true; rm -rf "$WORK_DIR"' EXIT

MOCK_PORT=4177
PORT=8177
for p in $MOCK_PORT $PORT 8178; do
    if curl -s "http://127.0.0.1:$p/" > /dev/null 2>&1; then
        echo "FAILED: Port $p is already in use by another process"
        exit 1
    fi
done
MOCK_URL="http://127.0.0.1:$MOCK_PORT"
export PR1049_LITELLM_URL="$MOCK_URL"

# --- Mock LiteLLM proxy and backend ------------------------------------------
print_header "PR #1049: backend with a per-model gateway allowlist"
MOCK_LITELLM_PORT=$MOCK_PORT python mocks/litellm-mock/main.py > "$WORK_DIR/mock.log" 2>&1 &
MOCK_PID=$!
for i in $(seq 1 20); do
    curl -sf "$MOCK_URL/health" > /dev/null 2>&1 && break
    sleep 0.5
done

export PORT=$PORT
export ATLAS_HOST="127.0.0.1"
export APP_CONFIG_DIR="$CONFIG_DIR"
export APP_LOG_DIR="$LOG_DIR"
export USE_MOCK_S3=true
export DEBUG_MODE=true
export ENVIRONMENT=development
export MCP_TOKEN_ENCRYPTION_KEY
MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export SKIP_AUTHORIZATION_CHECKS=true
export FEATURE_CHAT_HISTORY_ENABLED=false

cd "$ATLAS_DIR"
python main.py > "$WORK_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
cd "$PROJECT_ROOT"
READY=0
for i in $(seq 1 60); do
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo "FAILED: Backend exited unexpectedly; log tail:"
        tail -30 "$WORK_DIR/backend.log"
        exit 1
    fi
    if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 1
done
if [ "$READY" -ne 1 ]; then
    echo "FAILED: Backend did not become ready; log tail:"
    tail -30 "$WORK_DIR/backend.log"
    exit 1
fi
API="http://127.0.0.1:$PORT"
USER_HDR="X-User-Email: test@test.com"

curl -s -H "$USER_HDR" "$API/api/config/shell" | python3 -c "
import sys, json
g = {g['name']: g for g in json.load(sys.stdin)['llm_gateways']}['enterprise']
assert (g['supports_tools'], g['supports_vision'], g['supports_pdf']) == (False, False, False), g
caps = g['model_capabilities']
assert caps == {
    'gpt-4o-mini': {'supports_vision': False, 'supports_pdf': False, 'supports_tools': True},
    'claude-sonnet': {'supports_vision': True, 'supports_pdf': True, 'supports_tools': True},
    'llama-3.3-70b': {'supports_vision': False, 'supports_pdf': False, 'supports_tools': False},
}, caps
print(caps)
"
print_result $? "/api/config/shell gives the defaults and each allowlisted model's capabilities"

curl -s -H "$USER_HDR" "$API/api/llm/gateways/enterprise/models?team_id=team-alpha-7f3a" | python3 -c "
import sys, json
models = json.load(sys.stdin)['models']
got = [(m['model_id'], m['supports_tools'], m['supports_vision'], m['supports_pdf']) for m in models]
assert got == [('gpt-4o-mini', True, False, False), ('claude-sonnet', True, True, True)], got
print(got)
"
print_result $? "Models endpoint gives each of one team's models its own capabilities"

curl -s -H "$USER_HDR" "$API/api/llm/gateways/enterprise/models?team_id=team-beta-19c2" | python3 -c "
import sys, json
models = json.load(sys.stdin)['models']
got = [(m['model_id'], m['supports_tools'], m['supports_vision'], m['supports_pdf']) for m in models]
assert got == [('llama-3.3-70b', False, False, False)], got
print(got)
"
print_result $? "A model the allowlist leaves unset takes the gateway's defaults"

# --- Chat: each model's own max_tokens reaches LiteLLM ------------------------
print_header "PR #1049: chat with per-model settings"
curl -s -X DELETE "$MOCK_URL/mock/requests" > /dev/null
OUT=$(cd "$ATLAS_DIR" && python atlas_chat_cli.py "hello" --model "enterprise::team-alpha-7f3a::gpt-4o-mini" --user-email test@test.com 2>"$WORK_DIR/cli1.err")
echo "$OUT" | grep -q "\[Project Alpha / gpt-4o-mini\]"
print_result $? "atlas-chat through gpt-4o-mini reaches the team"

OUT=$(cd "$ATLAS_DIR" && python atlas_chat_cli.py "hello" --model "enterprise::team-alpha-7f3a::claude-sonnet" --user-email test@test.com 2>"$WORK_DIR/cli2.err")
echo "$OUT" | grep -q "\[Project Alpha / claude-sonnet\]"
print_result $? "atlas-chat through claude-sonnet reaches the same team"

curl -s "$MOCK_URL/mock/requests" | python3 -c "
import sys, json
reqs = json.load(sys.stdin)['requests']
got = [(r['model'], r['team_header'], r['max_tokens'], r['outcome']) for r in reqs]
assert got == [
    ('gpt-4o-mini', 'team-alpha-7f3a', 256, 'ok'),
    ('claude-sonnet', 'team-alpha-7f3a', 1024, 'ok'),
], got
print(got)
"
print_result $? "Mock received each model's own max_tokens (default 256, override 1024)"

# --- Chat with a PDF: only a supports_pdf model gets it inline -------------------
# supports_pdf has no effect in the UI: the server decides whether an attached
# PDF goes to the model inline or as a file reference, so it is checked at the
# mock, from a chat turn sent over the WebSocket as the web UI sends it.
curl -s -X DELETE "$MOCK_URL/mock/requests" > /dev/null
python "$FIXTURES_DIR/ws_pdf.py" "$PORT" "enterprise::team-alpha-7f3a::claude-sonnet"
print_result $? "Chat turn with a PDF through claude-sonnet completes"
python "$FIXTURES_DIR/ws_pdf.py" "$PORT" "enterprise::team-alpha-7f3a::gpt-4o-mini"
print_result $? "Chat turn with a PDF through gpt-4o-mini completes"

curl -s "$MOCK_URL/mock/requests" | python3 -c "
import sys, json
reqs = json.load(sys.stdin)['requests']
got = [(r['model'], r['pdf_parts'], r['outcome']) for r in reqs]
assert got == [('claude-sonnet', 1, 'ok'), ('gpt-4o-mini', 0, 'ok')], got
print(got)
"
print_result $? "Mock received the PDF inline from claude-sonnet only (supports_pdf per model)"

# --- Invalid entries are refused at load ---------------------------------------
print_header "PR #1049: invalid model entries"
# Each case: where the setting goes (entry or defaults) | the setting | the
# error expected ("" = accepted) | label. The gateway is otherwise valid (it
# has its api_key), so only the setting under test can refuse it.
for case in \
    "entry|supports_tools: true||A valid setting in a model entry is accepted" \
    "entry|api_key_source: user|may not set api_key_source|A reserved field (api_key_source) in a model entry is refused" \
    "entry|delegation: {scope: api://other/.default}|may not set delegation|A reserved field (delegation) in a model entry is refused" \
    "entry|supports_telepathy: true|unknown model setting(s) in a models entry|An unknown field in a model entry is refused" \
    "entry|max_tokens: lots|valid integer|An invalid value in a model entry is refused" \
    "defaults|supports_telepathy: true|unknown model setting(s) in model_defaults|An unknown field in model_defaults is refused"; do
    where="${case%%|*}"; rest="${case#*|}"
    setting="${rest%%|*}"; rest="${rest#*|}"
    expected="${rest%%|*}"; label="${rest#*|}"
    python3 - "$where" "$setting" "$expected" <<'EOF'
import sys, yaml
from pydantic import ValidationError
from atlas.modules.config.models import LLMConfig
where, setting, expected = sys.argv[1], yaml.safe_load(sys.argv[2]), sys.argv[3]
gateway = {"base_url": "http://127.0.0.1:1", "api_key": "sk-mock-litellm-master", "models": {"gpt-4o-mini": {}}}
if where == "entry":
    gateway["models"]["gpt-4o-mini"] = setting
else:
    gateway["model_defaults"] = setting
try:
    LLMConfig(models={}, litellm_gateways={"enterprise": gateway})
except ValidationError as exc:
    message = "; ".join(error["msg"] for error in exc.errors())
    print(message[:200])
    sys.exit(0 if expected and expected in message else 1)
print("accepted:", setting)
sys.exit(0 if not expected else 1)
EOF
    print_result $? "$label"
done

# --- A refused config stops startup ------------------------------------------
# The LLM config is fatal when invalid, so the upgrade-time effect of the new
# model_defaults check is a server that does not start, with the field named.
BAD_CONFIG_DIR="$WORK_DIR/config-bad"
mkdir -p "$BAD_CONFIG_DIR"
sed 's/^      max_tokens: 256$/      max_tokens: 256\n      supports_telepathy: true/' \
    "$FIXTURES_DIR/llmconfig.yml" > "$BAD_CONFIG_DIR/llmconfig.yml"
echo '{}' > "$BAD_CONFIG_DIR/mcp.json"
cd "$ATLAS_DIR"
APP_CONFIG_DIR="$BAD_CONFIG_DIR" PORT=8178 python main.py > "$WORK_DIR/backend-bad.log" 2>&1 &
BAD_PID=$!
cd "$PROJECT_ROOT"
for i in $(seq 1 30); do
    kill -0 $BAD_PID 2>/dev/null || break
    sleep 1
done
if kill -0 $BAD_PID 2>/dev/null; then
    kill $BAD_PID 2>/dev/null
    echo "  backend was still running after 30s"
    false
else
    wait $BAD_PID
    STATUS=$?
    echo "  exit status $STATUS"
    [ "$STATUS" -ne 0 ] && grep -q "unknown model setting(s) in model_defaults: supports_telepathy" "$WORK_DIR/backend-bad.log"
fi
print_result $? "Backend refuses to start with an unknown model_defaults field, naming it"

# --- Unit tests -------------------------------------------------------------
print_header "Backend unit test suite"
# Without this script's fixture environment: the suite's own e2e checks pick
# the first configured model and would otherwise talk to the mock proxy.
env -u APP_CONFIG_DIR -u APP_LOG_DIR -u PORT -u ATLAS_HOST -u PR1049_LITELLM_URL \
    -u ENVIRONMENT -u MCP_TOKEN_ENCRYPTION_KEY -u SKIP_AUTHORIZATION_CHECKS -u FEATURE_CHAT_HISTORY_ENABLED \
    ./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests (./test/run_tests.sh backend)"

print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC} | Failed: ${RED}$FAILED${NC}"
[ $FAILED -eq 0 ] && exit 0 || exit 1
