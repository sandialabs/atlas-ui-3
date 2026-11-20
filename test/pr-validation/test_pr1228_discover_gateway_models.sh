#!/bin/bash
# Test script for the PR fixing issue #1227: launch discovery
# (atlas_discover_subagent_options and GET /api/agent-runs/options) offers the
# Enterprise LiteLLM gateway team models the user can chat with, and a run can
# be launched on one.
#
# Test plan, against a real backend and the mock LiteLLM proxy
# (mocks/litellm-mock):
#   1. Options for a user list the static model and every model of that
#      user's LiteLLM teams as <gateway>::<team>::<model> names.
#   2. Options never list another user's teams.
#   3. A run launched on a discovered gateway model completes, and the chat
#      request reached the proxy with that team's header.
#   4. Affected backend unit suites (launch discovery, gateways, agent runs).
# All prompts are synthetic placeholders.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FIXTURES="$PROJECT_ROOT/test/pr-validation/fixtures/pr1228"
cd "$PROJECT_ROOT" || exit 1
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

PASSED=0
FAILED=0
print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"; PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"; FAILED=$((FAILED + 1))
    fi
}

WORK="$(mktemp -d)"
BACKEND_PID=""
MOCK_PID=""
cleanup() {
    # Only the processes this script started.
    for pid in "$BACKEND_PID" "$MOCK_PID"; do
        if [ -n "$pid" ]; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
        fi
    done
    rm -rf "$WORK"
}
trap cleanup EXIT

free_port() {
    python - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
}
wait_for_url() {
    for _ in $(seq 1 90); do
        curl -sf "$1" > /dev/null && return 0
        sleep 1
    done
    return 1
}

export MOCK_LITELLM_PORT="$(free_port)"
export ATLAS_PORT="$(free_port)"
export AGENT_RUNS_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
cp "$PROJECT_ROOT/test/pr-validation/fixtures/pr1043/mcp.json" "$WORK/config/mcp.json"
echo '{}' > "$WORK/config/rag-sources.json"
sed "s/__MOCK_LITELLM_PORT__/$MOCK_LITELLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"

python mocks/litellm-mock/main.py > "$WORK/litellm-mock.log" 2>&1 &
MOCK_PID=$!
wait_for_url "http://127.0.0.1:$MOCK_LITELLM_PORT/health" || { tail -20 "$WORK/litellm-mock.log"; echo "FAILED: mock LiteLLM did not start"; exit 1; }

# Started directly rather than through agent_start.sh, which stops every
# uvicorn on the machine.
(
    cd "$PROJECT_ROOT/atlas"
    export PYTHONPATH="$PROJECT_ROOT"
    export ENVIRONMENT=local DEBUG_MODE=true
    export FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_AGENT_MODE_AVAILABLE=true
    export FEATURE_TOOLS_ENABLED=true FEATURE_WORKSPACES_ENABLED=true FEATURE_RAG_ENABLED=false
    export FEATURE_AGENT_RUNS_API_ENABLED=true FEATURE_COMPLIANCE_LEVELS_ENABLED=false
    export FEATURE_OIDC_AUTH_ENABLED=false FEATURE_PROXY_SECRET_ENABLED=false
    export USE_MOCK_S3=true REQUIRE_TOOL_APPROVAL_BY_DEFAULT=false FORCE_TOOL_APPROVAL_GLOBALLY=false
    export CHAT_HISTORY_DB_URL="duckdb:///$WORK/data/chat_history.db"
    export APP_LOG_DIR="$WORK/logs" APP_CONFIG_DIR="$WORK/config"
    export MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    exec python -m uvicorn main:app --host 127.0.0.1 --port "$ATLAS_PORT"
) > "$WORK/backend.log" 2>&1 &
BACKEND_PID=$!
if ! wait_for_url "http://127.0.0.1:$ATLAS_PORT/api/heartbeat"; then
    tail -40 "$WORK/backend.log"
    echo "FAILED: backend did not start"
    exit 1
fi

python - <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = f"http://127.0.0.1:{os.environ['ATLAS_PORT']}"
MOCK = f"http://127.0.0.1:{os.environ['MOCK_LITELLM_PORT']}"
SERVICE = {"Authorization": f"Bearer {os.environ['AGENT_RUNS_API_KEY']}"}
ALICE = "alice@example.com"   # mock member of team-alpha only
BOB = "bob@example.com"       # mock member of team-beta and team-gamma
ALPHA = "team-alpha-7f3a"
failures = 0


def call(method, path, payload=None, headers=None, base=API):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


def check(ok, label, detail=None):
    global failures
    print(("PASSED" if ok else "FAILED") + ": " + label, flush=True)
    if not ok:
        failures += 1
        if detail is not None:
            print("  ", detail)


for user in (ALICE, BOB):
    status, _ = call("POST", "/api/workspaces", {
        "name": "Research", "description": "issue 1227 validation",
        "config": {"selected_tools": ["atlas_sleep"], "selected_data_sources": []},
    }, headers={"X-User-Email": user})
    assert status == 200, status

status, options = call("GET", f"/api/agent-runs/options?user={ALICE}", headers=SERVICE)
alice_models = sorted(m["name"] for m in (options or {}).get("models", []))
expected = sorted([
    "static-model",
    f"enterprise::{ALPHA}::gpt-4o-mini",
    f"enterprise::{ALPHA}::claude-sonnet",
])
check(status == 200 and alice_models == expected,
      "options list the static model and the user's gateway team models", (status, options))

status, options = call("GET", f"/api/agent-runs/options?user={BOB}", headers=SERVICE)
bob_models = [m["name"] for m in (options or {}).get("models", [])]
check(status == 200 and "enterprise::team-beta-19c2::llama-3.3-70b" in bob_models
      and not any(ALPHA in name for name in bob_models),
      "options never list another user's teams", (status, options))

call("DELETE", "/mock/requests", base=MOCK)
model = f"enterprise::{ALPHA}::gpt-4o-mini"
status, handle = call("POST", "/api/agent-runs", {
    "user": ALICE, "workspace": "Research", "model": model,
    "message": "Placeholder task for the validation run",
}, headers=SERVICE)
check(status == 202, "launch on a discovered gateway model is admitted", (status, handle))
final = {}
if status == 202:
    for _ in range(120):
        _, final = call("GET", f"/api/agent-runs/{handle['run_id']}", headers=SERVICE)
        if final.get("status") in ("completed", "failed", "cancelled"):
            break
        time.sleep(0.5)
check(final.get("status") == "completed" and "Project Alpha / gpt-4o-mini" in (final.get("result") or ""),
      "the run completes with the gateway model's reply", final)
_, log = call("GET", "/mock/requests", base=MOCK)
hits = [r for r in log.get("requests", []) if r.get("outcome") == "ok" and r.get("model") == "gpt-4o-mini"]
check(bool(hits) and all(r.get("team_header") == ALPHA for r in hits),
      "the chat request reached the proxy with the team header", log)

sys.exit(1 if failures else 0)
PY
print_result $? "end to end: gateway models in launch discovery and launch"

echo ""
echo "== affected backend unit suites"
(
    cd "$PROJECT_ROOT/atlas" && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q -p no:cacheprovider \
        tests/test_launch_gateway_models.py tests/test_atlas_launch_tool.py \
        tests/test_litellm_gateway.py tests/test_agent_runs_api.py
)
print_result $? "backend unit tests (affected suites)"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
