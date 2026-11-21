#!/bin/bash
# PR #1229: llmconfig.yml model picker order and default LiteLLM team/model.
#
# Against a real backend and the mock LiteLLM proxy (mocks/litellm-mock):
#   1. /api/config/shell and /api/config report model_picker_order,
#      default_model and the gateway's default_team_id / default_model_id.
#   2. GET /api/llm/default-model starts a team member on the gateway
#      default and falls back to the static model for a non-member.
#   3. A real chat turn on the resolved default reaches the proxy with the
#      default team header.
#   4. Invalid default keys fail llmconfig.yml validation.
#   5. atlas-chat --list-models --json reports the defaults.
#   6. Backend and frontend unit tests for the feature.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr1229"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"; PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"; FAILED=$((FAILED + 1))
    fi
}
print_header() { echo ""; echo "=========================================="; echo "$1"; echo "=========================================="; }

free_port() { python - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
}

wait_for_url() {
    local url="$1" tries="${2:-60}"
    for _ in $(seq 1 "$tries"); do
        if curl -s -o /dev/null "$url"; then return 0; fi
        sleep 1
    done
    return 1
}

cd "$PROJECT_ROOT" || exit 1
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

WORK="$(mktemp -d)"
export MOCK_LITELLM_PORT="$(free_port)"
export ATLAS_PORT="$(free_port)"
trap 'kill $MOCK_PID $BACKEND_PID 2>/dev/null; rm -rf "$WORK"' EXIT

print_header "Starting mock LiteLLM proxy on :$MOCK_LITELLM_PORT and backend on :$ATLAS_PORT"
MOCK_LITELLM_PORT="$MOCK_LITELLM_PORT" python mocks/litellm-mock/main.py > "$WORK/litellm-mock.log" 2>&1 &
MOCK_PID=$!

mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
cp "$SCRIPT_DIR/fixtures/pr1043/mcp.json" "$WORK/config/mcp.json"
echo '{}' > "$WORK/config/rag-sources.json"
cp "$SCRIPT_DIR/fixtures/pr1043/compliance-levels.json" "$WORK/config/compliance-levels.json"
sed "s/__MOCK_LITELLM_PORT__/$MOCK_LITELLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"

(
  cd "$PROJECT_ROOT/atlas" && \
  ENVIRONMENT=local DEBUG_MODE=true FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_COMPLIANCE_LEVELS_ENABLED=false \
  FEATURE_OIDC_AUTH_ENABLED=false FEATURE_PROXY_SECRET_ENABLED=false \
  FEATURE_TOOLS_ENABLED=false FEATURE_RAG_ENABLED=false FEATURE_WORKSPACES_ENABLED=false \
  USE_MOCK_S3=true CHAT_HISTORY_DB_URL="duckdb:///$WORK/data/chat_history.db" APP_LOG_DIR="$WORK/logs" \
  APP_CONFIG_DIR="$WORK/config" PYTHONPATH="$PROJECT_ROOT" \
  python -m uvicorn main:app --host 127.0.0.1 --port "$ATLAS_PORT" > "$WORK/backend.log" 2>&1
) &
BACKEND_PID=$!

if wait_for_url "http://127.0.0.1:$ATLAS_PORT/api/heartbeat" 60 && wait_for_url "http://127.0.0.1:$MOCK_LITELLM_PORT/mock/requests" 30; then
    print_result 0 "backend and mock proxy started"
else
    print_result 1 "backend and mock proxy started"
    tail -40 "$WORK/backend.log"
    exit 1
fi

print_header "Config endpoints, default-model resolution and a chat turn"
API="http://127.0.0.1:$ATLAS_PORT" MOCK="http://127.0.0.1:$MOCK_LITELLM_PORT" python - <<'PYEOF'
import asyncio, json, os, sys, urllib.request

API, MOCK = os.environ["API"], os.environ["MOCK"]
KEY = "enterprise::team-alpha-7f3a::claude-sonnet"
ok = True

def get(path, user="test@test.com", base=API):
    req = urllib.request.Request(base + path, headers={"X-User-Email": user})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def check(label, cond, detail=""):
    global ok
    print(("PASSED" if cond else "FAILED") + f": {label}" + (f" ({detail})" if not cond and detail else ""))
    ok = ok and bool(cond)

for path in ("/api/config/shell", "/api/config"):
    body = get(path)
    gw = (body.get("llm_gateways") or [{}])[0]
    check(f"{path} model_picker_order is gateways_first", body.get("model_picker_order") == "gateways_first", body.get("model_picker_order"))
    check(f"{path} default_model is null (only gateway defaults set)", body.get("default_model") is None)
    check(f"{path} gateway reports default team/model",
          gw.get("default_team_id") == "team-alpha-7f3a" and gw.get("default_model_id") == "claude-sonnet", gw)

member = get("/api/llm/default-model")
check("team member starts on the gateway default", member == {"model": KEY, "source": "configured"}, member)
outsider = get("/api/llm/default-model", user="bob@example.com")
check("non-member falls back to the static model", outsider == {"model": "static-model", "source": "first_model"}, outsider)

async def chat():
    import websockets
    ws_url = API.replace("http", "ws") + "/ws"
    async with websockets.connect(ws_url, additional_headers={"X-User-Email": "test@test.com"}) as ws:
        await ws.send(json.dumps({"type": "chat", "content": "placeholder default-model prompt",
                                  "model": KEY, "save_mode": "server"}))
        text = ""
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            if msg.get("type") == "token_stream":
                text += msg.get("token") or ""
            if msg.get("type") == "conversation_saved":
                return text
            if msg.get("type") == "error":
                return "ERROR: " + str(msg)

reply = asyncio.run(chat())
requests = get("/mock/requests", base=MOCK).get("requests") or []
last = requests[-1] if requests else {}
check("chat on the default reaches the proxy with the default team header",
      last.get("team_header") == "team-alpha-7f3a" and last.get("model") == "claude-sonnet", f"{last} reply={reply[:80]!r}")
sys.exit(0 if ok else 1)
PYEOF
print_result $? "live endpoint checks"

print_header "Load-time validation"
PYTHONPATH="$PROJECT_ROOT" python - <<'PYEOF'
import sys
from pydantic import ValidationError
from atlas.modules.config.models import LLMConfig

gw = {"base_url": "http://x", "api_key": "k"}
static = {"m": {"model_name": "m", "model_url": "http://x", "api_key": "k"}}
cases = {
    "unknown model_picker_order": dict(models=static, model_picker_order="alpha"),
    "default_model typo": dict(models=static, default_model="nope"),
    "gateway default_model without team": dict(models=static, litellm_gateways={"g": {**gw, "default_model": "x"}}),
    "gateway default_model outside allowlist": dict(models=static, litellm_gateways={"g": {**gw, "default_team": "t", "default_model": "x", "models": ["y"]}}),
    "default_team with separator": dict(models=static, litellm_gateways={"g": {**gw, "default_team": "a::b"}}),
}
ok = True
for label, kwargs in cases.items():
    try:
        LLMConfig(**kwargs)
        print(f"FAILED: {label} was accepted"); ok = False
    except ValidationError:
        print(f"PASSED: {label} is rejected")
LLMConfig(models=static, model_picker_order="gateways_first", default_model="g::t::y",
          litellm_gateways={"g": {**gw, "default_team": "t", "default_model": "y", "models": ["y"]}})
print("PASSED: a valid configuration loads")
sys.exit(0 if ok else 1)
PYEOF
print_result $? "load-time validation"

print_header "atlas-chat --list-models --json"
CLI_JSON="$(cd "$WORK" && APP_CONFIG_DIR="$WORK/config" PYTHONPATH="$PROJECT_ROOT" python -m atlas.atlas_chat_cli --list-models --json 2>/dev/null)"
echo "$CLI_JSON" | python -c '
import json, sys
p = json.load(sys.stdin)
g = p["litellm_gateways"][0]
assert p["model_picker_order"] == "gateways_first", p
assert g["default_team"] == "team-alpha-7f3a" and g["default_model"] == "claude-sonnet", g
print("PASSED: CLI JSON reports the defaults")
'
print_result $? "CLI list-models reports the defaults"

print_header "Unit tests"
( cd "$PROJECT_ROOT/atlas" && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q \
    tests/test_llm_default_model_and_picker_order.py tests/test_llmconfig_model_sources.py tests/test_litellm_gateway.py )
print_result $? "backend unit tests (affected suites)"
if [ -d "$PROJECT_ROOT/frontend/node_modules" ]; then
    ( cd "$PROJECT_ROOT/frontend" && npx vitest run src/test/llm-default-model-and-picker-order.test.jsx \
        src/test/gateway-model-picker.test.jsx src/test/per-user-browser-state.test.jsx src/test/gateway-only-model-hint.test.jsx )
    print_result $? "frontend unit tests (affected suites)"
fi

echo ""
echo "=========================================="
echo "PR #1229 validation: $PASSED passed, $FAILED failed"
echo "=========================================="
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
