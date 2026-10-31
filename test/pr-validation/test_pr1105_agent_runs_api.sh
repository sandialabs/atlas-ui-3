#!/bin/bash
# PR #1105: drive the agent-runs REST API end to end against a real backend.
#
# A trusted service holding AGENT_RUNS_API_KEY launches a run as a user, polls
# it to completion, finds it in that user's history, follows it up (and checks
# the model saw the stored history), replays an idempotent launch, and is
# refused without the key. The LLM is the scripted mock from PR #949, which
# answers "ECHO: <message>" when given no directive.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source .venv/bin/activate

WORK="$(mktemp -d)"
FIXTURES="$PROJECT_ROOT/test/pr-validation/fixtures/pr949"
BACKEND_PID=""
MOCK_PID=""
cleanup() {
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
wait_for_health() {
    for _ in $(seq 1 90); do
        curl -sf "$1/health" > /dev/null && return 0
        sleep 1
    done
    cat "$WORK"/*.log
    return 1
}

export MOCK_LLM_PORT="$(free_port)"
export ATLAS_PORT="$(free_port)"
export AGENT_RUNS_API_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
cp "$FIXTURES/mcp.json" "$FIXTURES/rag-sources.json" "$WORK/config/"
sed "s/__MOCK_LLM_PORT__/$MOCK_LLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"

python "$FIXTURES/mock_llm.py" > "$WORK/mock.log" 2>&1 &
MOCK_PID=$!
wait_for_health "http://127.0.0.1:$MOCK_LLM_PORT"

# Started directly rather than through agent_start.sh, which stops every
# uvicorn on the machine.
(
    cd "$PROJECT_ROOT/atlas"
    export PYTHONPATH="$PROJECT_ROOT"
    export PORT="$ATLAS_PORT" DEBUG_MODE=true
    export FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_AGENT_MODE_AVAILABLE=true
    export FEATURE_TOOLS_ENABLED=true FEATURE_WORKSPACES_ENABLED=true FEATURE_RAG_ENABLED=false
    export FEATURE_AGENT_RUNS_API_ENABLED=true
    export USE_MOCK_S3=true REQUIRE_TOOL_APPROVAL_BY_DEFAULT=false FORCE_TOOL_APPROVAL_GLOBALLY=false
    export CHAT_HISTORY_DB_URL="duckdb:///$WORK/data/chat_history.db"
    export APP_LOG_DIR="$WORK/logs" APP_CONFIG_DIR="$WORK/config"
    export MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
    exec python -m uvicorn main:app --host 127.0.0.1 --port "$ATLAS_PORT"
) > "$WORK/backend.log" 2>&1 &
BACKEND_PID=$!
wait_for_health "http://127.0.0.1:$ATLAS_PORT/api"

python - <<'PY'
import json
import os
import time
import urllib.error
import urllib.request

API = f"http://127.0.0.1:{os.environ['ATLAS_PORT']}"
MOCK = f"http://127.0.0.1:{os.environ['MOCK_LLM_PORT']}"
KEY = os.environ["AGENT_RUNS_API_KEY"]
USER = "owner@example.com"
SERVICE = {"Authorization": f"Bearer {KEY}"}


def call(method, path, payload=None, headers=None, base=API):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "null"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null"), dict(e.headers)


def passed(label):
    print(f"PASSED: {label}", flush=True)


def wait_terminal(run_id):
    for _ in range(120):
        status, body, _ = call("GET", f"/api/agent-runs/{run_id}", headers=SERVICE)
        assert status == 200, (status, body)
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        time.sleep(0.5)
    raise AssertionError(f"run {run_id} did not finish: {body}")


status, _, _ = call("POST", "/api/workspaces", {
    "name": "Build triage",
    "description": "PR 1105 validation",
    "config": {"selected_tools": ["atlas_sleep"], "selected_data_sources": []},
}, headers={"X-User-Email": USER})
assert status == 200, status

status, body, headers = call("GET", "/api/agent-runs")
assert status == 401 and headers.get("www-authenticate") == "Bearer", (status, body)
status, _, _ = call("GET", "/api/agent-runs", headers={"X-User-Email": USER})
assert status == 401, status
status, _, _ = call("GET", "/api/agent-runs", headers={"Authorization": "Bearer " + "x" * 64})
assert status == 401, status
passed("no key, a browser identity header, or a wrong key is refused")

status, options, _ = call("GET", f"/api/agent-runs/options?user={USER}", headers=SERVICE)
assert status == 200, options
assert [w["name"] for w in options["workspaces"]] == ["Build triage"], options
assert "scripted-mock" in [m["name"] for m in options["models"]], options
passed("options lists the user's workspaces and models")

launch = {"user": USER, "workspace": "Build triage", "model": "scripted-mock",
          "message": "Summarize the failed build", "idempotency_key": "build-1105"}
status, handle, headers = call("POST", "/api/agent-runs", launch, headers=SERVICE)
assert status == 202, (status, handle)
assert headers.get("location") == handle["status_url"] == f"/api/agent-runs/{handle['run_id']}"
passed("launch returns 202 with a run handle")

final = wait_terminal(handle["run_id"])
assert final["status"] == "completed", final
assert final["result"] == "ECHO: Summarize the failed build", final
assert final["parent_run_id"] is None and final["depth"] == 0, final
passed("the run completes in the background and its reply is readable")

status, replay, _ = call("POST", "/api/agent-runs", launch, headers=SERVICE)
assert status == 200 and replay["run_id"] == handle["run_id"] and replay["idempotent_replay"], replay
status, conflict, _ = call("POST", "/api/agent-runs", {**launch, "message": "other"}, headers=SERVICE)
assert status == 409 and conflict["detail"]["code"] == "idempotency_conflict", conflict
passed("an idempotent retry returns the original run; a different body is a conflict")

status, history, _ = call("GET", "/api/conversations", headers={"X-User-Email": USER})
assert status == 200, history
ids = [c["id"] for c in history["conversations"]]
assert handle["conversation_id"] in ids, (handle["conversation_id"], ids)
status, other, _ = call("GET", "/api/conversations", headers={"X-User-Email": "other@example.com"})
assert handle["conversation_id"] not in [c["id"] for c in other["conversations"]]
passed("the conversation is in the target user's history and nobody else's")

_, log_before, _ = call("GET", "/test/log", base=MOCK)
status, follow, _ = call("POST", f"/api/agent-runs/{handle['run_id']}/follow-up",
                         {"message": "Now list the flaky tests"}, headers=SERVICE)
assert status == 202, (status, follow)
assert follow["conversation_id"] == handle["conversation_id"], follow
assert follow["previous_run_id"] == handle["run_id"], follow
final2 = wait_terminal(follow["run_id"])
assert final2["status"] == "completed" and final2["result"] == "ECHO: Now list the flaky tests", final2
_, log_after, _ = call("GET", "/test/log", base=MOCK)
first_turn = next(e for e in log_before if e["user"] == "Summarize the failed build")
second_turn = next(e for e in log_after if e["user"] == "Now list the flaky tests")
assert second_turn["n_msgs"] >= first_turn["n_msgs"] + 2, (first_turn, second_turn)
passed("a follow-up continues the same conversation with its stored history")

status, refused, _ = call("POST", "/api/agent-runs", {**launch, "user": "other@example.com",
                                                       "idempotency_key": None}, headers=SERVICE)
assert status == 404 and refused["detail"]["code"] == "workspace_not_found", (status, refused)
# The caller's own input is echoed back; the owner's workspace list is not.
assert "Available workspaces" not in refused["detail"]["message"], refused
status, refused, _ = call("POST", "/api/agent-runs", {**launch, "model": "nope",
                                                       "idempotency_key": None}, headers=SERVICE)
assert status == 422 and refused["detail"]["code"] == "model_not_available", refused
passed("another user's workspace and an unknown model are refused with structured errors")

status, cancelled, _ = call("POST", f"/api/agent-runs/{follow['run_id']}/cancel", headers=SERVICE)
assert status == 200 and cancelled["cancelled"] is False and cancelled["status"] == "completed"
status, listing, _ = call("GET", f"/api/agent-runs?user={USER}", headers=SERVICE)
assert [r["run_id"] for r in listing["runs"]] == [follow["run_id"], handle["run_id"]], listing
passed("listing and cancel see only API runs")

status, schema, _ = call("GET", "/openapi.json", headers={"X-User-Email": USER})
assert status == 200 and "/api/agent-runs/{run_id}/follow-up" in schema["paths"], status
passed("the OpenAPI document describes the API")
PY

echo "PASSED: agent-runs REST API validation"
