"""End-to-end scenario for issue #1134 against a running backend.

The backend's llmconfig.yml contains no static models at all -- one team-
scoped Enterprise LiteLLM gateway only (the mock LiteLLM proxy). The run
checks startup, model discovery/selection over the real REST endpoints, and
one real chat turn over the WebSocket on a gateway model key. The mock proxy
records every call, so "the model answered" is checked as "the gateway
received the request with the chosen team header".
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

import websockets

PORT = os.environ["ATLAS_PORT"]
MOCK = f"http://127.0.0.1:{os.environ['MOCK_LITELLM_PORT']}"
WS = f"ws://127.0.0.1:{PORT}/ws"
API = f"http://127.0.0.1:{PORT}"
USER = "test@test.com"
FAILED = 0


def check(ok, label):
    global FAILED
    print(("PASSED: " if ok else "FAILED: ") + label, flush=True)
    if not ok:
        FAILED += 1


def get(path, user=USER, base=API):
    req = urllib.request.Request(base + path, headers={"X-User-Email": user})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "null")


async def connect():
    return await websockets.connect(WS, additional_headers={"X-User-Email": USER})


async def recv_until(ws, pred, timeout=30):
    got = []
    try:
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            got.append(m)
            if pred(m):
                return m, got
    except asyncio.TimeoutError:
        return None, got


def is_done(m):
    return m.get("type") in ("conversation_saved", "error")


async def main():
    # 1. Startup + shell config: zero static models, the gateway offered.
    status, shell = get("/api/config/shell")
    check(status == 200, "GET /api/config/shell answers 200 on a gateway-only config")
    check(shell.get("models") == [], "the shell config lists no static models")
    gateways = shell.get("llm_gateways") or []
    check([g.get("name") for g in gateways] == ["enterprise"],
          "the shell config offers the Enterprise LiteLLM gateway")

    # 2. Team discovery for the signed-in user.
    status, teams = get("/api/llm/gateways/enterprise/teams")
    check(status == 200, "team discovery answers 200")
    team_ids = [t["team_id"] for t in teams.get("teams", [])]
    check("team-beta-19c2" in team_ids, f"the user's LiteLLM teams are listed ({team_ids})")

    # 3. Model discovery for one of the user's teams.
    status, models = get("/api/llm/gateways/enterprise/models?team_id=team-beta-19c2")
    check(status == 200, "model discovery answers 200")
    keys = [m["name"] for m in models.get("models", [])]
    check("enterprise::team-beta-19c2::llama-3.3-70b" in keys,
          f"the team's models are offered as selectable keys ({keys})")

    # 4. A real chat turn on the gateway model key.
    model_key = "enterprise::team-beta-19c2::llama-3.3-70b"
    ws = await connect()
    await ws.send(json.dumps({
        "type": "chat", "content": "placeholder gateway-only prompt",
        "model": model_key, "save_mode": "server",
    }))
    done, frames = await recv_until(ws, is_done)
    check(done is not None and done.get("type") == "conversation_saved",
          "a chat turn on a gateway model key completes and saves")
    text = "".join(f.get("token", "") for f in frames if f.get("type") == "token_stream")
    check("[Project Beta / llama-3.3-70b]" in text,
          f"the streamed answer comes from the gateway team's model ({text[:60]!r})")
    await ws.close()

    # 5. The gateway proxy saw the request carrying the chosen team.
    status, body = get("/mock/requests", base=MOCK)
    requests = (body or {}).get("requests") or []
    last = requests[-1] if requests else {}
    check(last.get("team_header") == "team-beta-19c2",
          f"the proxy received the team header ({last})")
    check(last.get("model") == "llama-3.3-70b", "the proxy received the chosen model id")

    # 6. A turn on a model that is no static model and no gateway key fails
    #    as a per-turn caller error: the user sees an error answer, and the
    #    proxy never receives a request for it.
    calls = len(get("/mock/requests", base=MOCK)[1].get("requests") or [])
    ws = await connect()
    await ws.send(json.dumps({
        "type": "chat", "content": "placeholder unknown-model prompt",
        "model": "no-such-model", "save_mode": "server",
    }))
    _, frames = await recv_until(ws, is_done, 30)
    text = "".join(f.get("token", "") for f in frames if f.get("type") == "token_stream")
    responses = [f for f in frames if f.get("type") == "chat_response"]
    check(bool(responses) and "[Project" not in text,
          "an unknown model answers with an error, not a gateway reply")
    await ws.close()
    now_calls = len(get("/mock/requests", base=MOCK)[1].get("requests") or [])
    check(now_calls == calls, "the unknown model never reaches the gateway proxy")

    return FAILED


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
