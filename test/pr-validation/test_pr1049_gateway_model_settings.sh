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
# - A gateway model entry with a reserved field, an unknown field or an
#   invalid value is refused when the config loads
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
for p in $MOCK_PORT $PORT; do
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

# --- Invalid entries are refused at load ---------------------------------------
print_header "PR #1049: invalid model entries"
for case in "api_key: sk-other|A reserved field" "supports_telepathy: true|An unknown field" "max_tokens: lots|An invalid value"; do
    entry="${case%%|*}"
    label="${case##*|}"
    python3 - "$entry" <<'EOF'
import sys, yaml
from pydantic import ValidationError
from atlas.modules.config.models import LLMConfig
entry = yaml.safe_load(sys.argv[1])
config = {
    "models": {},
    "litellm_gateways": {"enterprise": {"base_url": "http://127.0.0.1:1", "models": {"gpt-4o-mini": entry}}},
}
try:
    LLMConfig(**config)
except ValidationError as exc:
    print(exc.errors()[0]["msg"][:160])
    sys.exit(0)
print("accepted:", entry)
sys.exit(1)
EOF
    print_result $? "$label in a model entry is refused at load"
done

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
