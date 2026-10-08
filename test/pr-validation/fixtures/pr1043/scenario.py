"""End-to-end scenario for issue #1042 against a running backend.

A conversation saved under one compliance level must not be restored, fetched
or continued under another -- over the real WebSocket and REST API, from a new
connection (the "next day" case), mid-session, and with a client that tries
to supply its own classification. The scripted mock model logs every call, so
"refused" is checked as "the model was never called", not just as an error
frame. All prompts are synthetic placeholders.
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

import websockets

PORT = os.environ["ATLAS_PORT"]
MOCK = f"http://127.0.0.1:{os.environ['MOCK_LLM_PORT']}"
WS = f"ws://127.0.0.1:{PORT}/ws"
API = f"http://127.0.0.1:{PORT}"
USER = "owner@example.com"
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


def model_calls():
    return len(get("/test/log", base=MOCK)[1])


async def connect():
    return await websockets.connect(WS, additional_headers={"X-User-Email": USER})


async def recv_until(ws, pred, timeout=15):
    got = []
    try:
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            got.append(m)
            if pred(m):
                return m, got
    except asyncio.TimeoutError:
        return None, got


def chat(content, level, conversation_id=None, **extra):
    frame = {
        "type": "chat", "content": content, "model": "scripted-mock",
        "save_mode": "server", "compliance_level_filter": level,
    }
    if conversation_id:
        frame["conversation_id"] = conversation_id
    frame.update(extra)
    return json.dumps(frame)


def is_done(m):
    return m.get("type") in ("conversation_saved", "error")


async def main():
    # 1. Day one: a conversation under CUI, saved to the store.
    ws = await connect()
    await ws.send(chat("placeholder-alpha synthetic prompt", "CUI"))
    saved, _ = await recv_until(ws, is_done, 30)
    check(saved is not None and saved.get("type") == "conversation_saved", "a CUI conversation is saved")
    conv_id = saved["conversation_id"]
    await ws.close()

    status, body = get("/api/conversations")
    row = next((c for c in body["conversations"] if c["id"] == conv_id), {})
    check(row.get("data_classification") == "CUI", "the listing reports the server-recorded classification CUI")

    # 2. Day two, new connection, UUR active: the turn is refused, the model is
    #    never called, and nothing is saved.
    calls = model_calls()
    ws = await connect()
    await ws.send(chat("placeholder-beta synthetic prompt", "UUR", conversation_id=conv_id))
    err, _ = await recv_until(ws, is_done, 15)
    check(err is not None and err.get("error_type") == "conversation_classification",
          "resuming the CUI conversation under UUR is refused")
    check("placeholder-alpha" not in json.dumps(err), "the refusal does not echo conversation content")
    check(model_calls() == calls, "the model received nothing for the refused turn")

    # 3. A client-supplied classification cannot override the record.
    await ws.send(chat("placeholder-gamma", "UUR", conversation_id=conv_id,
                       data_classification="UUR", metadata={"data_classification": "UUR"}))
    err, _ = await recv_until(ws, is_done, 15)
    check(err is not None and err.get("error_type") == "conversation_classification",
          "client-supplied classification fields are ignored")

    # 4. Restore under UUR is refused before anything is loaded.
    await ws.send(json.dumps({"type": "restore_conversation", "conversation_id": conv_id,
                              "messages": [], "compliance_level_filter": "UUR"}))
    err, _ = await recv_until(ws, lambda m: m.get("type") in ("error", "conversation_restored"), 10)
    check(err is not None and err.get("error_type") == "conversation_classification",
          "restoring the CUI conversation under UUR is refused")

    # 5. REST fetch under UUR: 409, classification only, no messages.
    status, body = get(f"/api/conversations/{conv_id}?compliance_level=UUR")
    check(status == 409 and "messages" not in (body or {}) and body.get("data_classification") == "CUI",
          "REST fetch under UUR returns 409 without content")
    status, body = get(f"/api/conversations/{conv_id}?compliance_level=CUI")
    check(status == 200 and len(body.get("messages", [])) == 2, "REST fetch under CUI returns the conversation")
    check(model_calls() == calls, "still no model call after the refused paths")

    # 6. Under CUI the conversation resumes with its history.
    await ws.send(chat("placeholder-delta synthetic prompt", "CUI", conversation_id=conv_id))
    saved, _ = await recv_until(ws, is_done, 30)
    check(saved is not None and saved.get("type") == "conversation_saved", "resuming under CUI is allowed")
    last = get("/test/log", base=MOCK)[1][-1]
    check(last["n_msgs"] >= 3, "the CUI model call carried the stored history")

    # 7. Switching to UUR mid-session (even omitting the id) is refused.
    calls = model_calls()
    await ws.send(chat("placeholder-epsilon", "UUR", conversation_id=conv_id))
    err, _ = await recv_until(ws, is_done, 15)
    check(err is not None and err.get("error_type") == "conversation_classification",
          "switching the level mid-conversation is refused")
    await ws.send(chat("placeholder-zeta", "UUR"))
    err, _ = await recv_until(ws, is_done, 15)
    check(err is not None and err.get("error_type") == "conversation_classification",
          "omitting the conversation id does not detach the session history")
    check(model_calls() == calls, "the model received nothing for the mid-session switches")

    # 8. A new chat under UUR works and is recorded as UUR.
    await ws.send(json.dumps({"type": "reset_session"}))
    await recv_until(ws, lambda m: m.get("type") == "session_reset", 10)
    await ws.send(chat("placeholder-eta synthetic prompt", "UUR"))
    saved, _ = await recv_until(ws, is_done, 30)
    check(saved is not None and saved.get("type") == "conversation_saved", "a new UUR conversation works")
    status, body = get(f"/api/conversations/{saved['conversation_id']}")
    check(body.get("data_classification") == "UUR", "the new conversation is recorded as UUR")
    status, body = get(f"/api/conversations/{conv_id}")
    check(body.get("data_classification") == "CUI" and len(body["messages"]) == 4,
          "the CUI conversation is intact and still CUI")

    await ws.close()
    sys.exit(1 if FAILED else 0)


asyncio.run(main())
