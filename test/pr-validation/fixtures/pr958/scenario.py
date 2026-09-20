"""End-to-end scenario for PR #958 against a running backend.

Issue #958: a chat frame naming a *stored* conversation another user saved
must be refused before a run is admitted -- no `run_started`, no `run_status`
frames, no run record in the caller's snapshot.

Drives the real WebSocket endpoint and REST API as two users. Exits non-zero
on the first failed check; prints one PASSED/FAILED line per check.
"""
import asyncio
import json
import os
import sys
import urllib.request

import websockets

PORT = os.environ["ATLAS_PORT"]
WS = f"ws://127.0.0.1:{PORT}/ws"
API = f"http://127.0.0.1:{PORT}"
OWNER = "owner@example.com"
OTHER = "other@example.com"
FAILED = 0


def check(ok, label):
    global FAILED
    print(("PASSED: " if ok else "FAILED: ") + label, flush=True)
    if not ok:
        FAILED += 1


def get(path, user):
    req = urllib.request.Request(API + path, headers={"X-User-Email": user})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None


async def connect(user):
    return await websockets.connect(WS, additional_headers={"X-User-Email": user})


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


def chat(content, conversation_id=None, agent=True):
    frame = {
        "type": "chat", "content": content, "model": "scripted-mock",
        "save_mode": "server",
    }
    if agent:
        frame["selected_tools"] = ["atlas_sleep"]
        frame["agent_mode"] = True
    if conversation_id:
        frame["conversation_id"] = conversation_id
    return json.dumps(frame)


async def run_to_completion(ws, label):
    """Start-to-finish an admitted agent run: approval included."""
    started, _ = await recv_until(ws, lambda m: m.get("type") == "run_started")
    req, _ = await recv_until(ws, lambda m: m.get("type") == "tool_approval_request", 20)
    if req is None:
        return started, None, []
    await ws.send(json.dumps({
        "type": "tool_approval_response",
        "tool_call_id": req["tool_call_id"],
        "approved": True,
        "run_id": req.get("run_id"),
    }))
    done, got = await recv_until(
        ws,
        lambda m: m.get("type") == "run_status" and m["run"]["status"] in ("completed", "failed"),
        40,
    )
    return started, done, got


async def main():
    owner = await connect(OWNER)
    other = await connect(OTHER)

    # 1. User A runs an agent turn that completes and saves, producing a
    #    stored conversation, exactly as the issue's step 1 describes.
    await owner.send(chat("label=PR958A sleep=1 steps=1 words=3"))
    started, done, got = await run_to_completion(owner, "A")
    check(started is not None, "user A's agent turn is admitted as a run")
    conv_id = started["conversation_id"]
    check(done is not None and done["run"]["status"] == "completed", "user A's run completes")
    check(
        any(m.get("type") == "conversation_saved" and m.get("conversation_id") == conv_id for m in got),
        "user A's conversation is saved to the store",
    )
    status, body = get(f"/api/conversations/{conv_id}", OWNER)
    check(status == 200, "the conversation is stored and readable by its owner")
    baseline_count = body.get("message_count") if body else None

    # 2. User B names A's stored conversation id (issue step 2). The turn must
    #    be refused with the authorization error and NOTHING else: no
    #    run_started, no run_status for a run that never should have existed.
    await other.send(chat("hijack", conversation_id=conv_id))
    resp, got = await recv_until(other, lambda m: m.get("type") in ("error", "run_started"), 10)
    check(resp is not None and resp.get("type") == "error", "the foreign id is refused with an error frame")
    check(resp is not None and resp.get("error_type") == "authorization", "the refusal is an authorization error")
    check("not found" in (resp.get("message") or "").lower(), "the refusal says the conversation was not found")
    check(not any(m.get("type") == "run_started" for m in got), "no run_started precedes the refusal")
    check(not any(m.get("type") == "run_status" for m in got), "no run_status frames are emitted for the refused turn")

    # 3. No run record exists for the refused turn.
    await other.send(json.dumps({"type": "list_runs"}))
    snap, _ = await recv_until(other, lambda m: m.get("type") == "runs_snapshot", 10)
    foreign_runs = [r for r in snap["runs"] if r.get("conversation_id") == conv_id] if snap else []
    check(snap is not None and not foreign_runs, "no run record exists for the foreign conversation in B's snapshot")

    # 4. The stored conversation is untouched by the refused turn.
    status, body = get(f"/api/conversations/{conv_id}", OWNER)
    check(status == 200 and body.get("message_count") == baseline_count, "the stored conversation is untouched by the refused turn")
    status_other, _ = get(f"/api/conversations/{conv_id}", OTHER)
    check(status_other == 404, "user B still cannot read the foreign conversation over REST")

    # 5. A plain (non-agent) turn naming the foreign id is refused too, exactly
    #    as it was before -- this path never created a run.
    await other.send(chat("plain hijack", conversation_id=conv_id, agent=False))
    resp2, _ = await recv_until(other, lambda m: m.get("type") == "error", 10)
    check(resp2 is not None and resp2.get("error_type") == "authorization", "a plain turn naming the foreign id is refused as before")

    # 6. Control: user B's own new-chat agent run is still admitted and completes.
    await other.send(chat("label=PR958B sleep=1 steps=1 words=3"))
    b_started, b_done, _ = await run_to_completion(other, "B")
    check(b_started is not None and b_started.get("conversation_id") != conv_id, "user B's own new conversation is still admitted as a run")
    check(b_done is not None and b_done["run"]["status"] == "completed", "user B's own run completes normally")

    await owner.close()
    await other.close()
    sys.exit(1 if FAILED else 0)


asyncio.run(main())
