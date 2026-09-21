"""End-to-end scenario for PR #956 against a running backend.

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


def chat(content, conversation_id=None):
    frame = {
        "type": "chat", "content": content, "model": "scripted-mock",
        "selected_tools": ["atlas_sleep"], "agent_mode": True, "save_mode": "server",
    }
    if conversation_id:
        frame["conversation_id"] = conversation_id
    return json.dumps(frame)


async def main():
    owner = await connect(OWNER)
    other = await connect(OTHER)

    # 1. A run started from a new chat carries a title and announces itself first.
    await owner.send(chat("label=PR956 sleep=1 steps=1 words=3"))
    started, before = await recv_until(owner, lambda m: m.get("type") == "run_started")
    check(started is not None, "run_started arrives for a new-chat agent turn")
    check(all(not m.get("run_id") for m in before[:-1]), "no tagged frame precedes run_started")
    check(started and started.get("title") == "label=PR956 sleep=1 steps=1 words=3", "run_started carries the first prompt as title")
    run_id, conv_id = started["run_id"], started["conversation_id"]

    # 2. The approval request pauses the run: status, snapshot marker, stored request.
    req, _ = await recv_until(owner, lambda m: m.get("type") == "tool_approval_request")
    check(req is not None and req.get("run_id") == run_id, "tool_approval_request is tagged with the run")
    st, _ = await recv_until(owner, lambda m: m.get("type") == "run_status" and m["run"]["status"] == "waiting_for_input", 5)
    check(st is not None, "run_status reports waiting_for_input while the approval is pending")
    await owner.send(json.dumps({"type": "list_runs"}))
    snap, _ = await recv_until(owner, lambda m: m.get("type") == "runs_snapshot")
    mine = next((r for r in snap["runs"] if r["run_id"] == run_id), None)
    check(mine is not None and mine["status"] == "waiting_for_input" and mine.get("title"), "runs_snapshot lists the paused run with its title")

    # 3. The unsaved conversation is readable while the run is in flight.
    status, body = get(f"/api/conversations/{conv_id}", OWNER)
    check(status == 200 and body and body.get("in_flight") is True, "GET /api/conversations/{id} answers from the run session before the first save")
    check(bool(body) and any(m["role"] == "user" and "PR956" in m["content"] for m in body.get("messages", [])), "live record contains the in-flight prompt")
    status_other, _ = get(f"/api/conversations/{conv_id}", OTHER)
    check(status_other == 404, "another user cannot read the in-flight conversation")

    # 4. Restore replays the pending approval request.
    await owner.send(json.dumps({"type": "restore_conversation", "conversation_id": conv_id, "messages": []}))
    restored, got = await recv_until(owner, lambda m: m.get("type") == "tool_approval_request" and m.get("tool_call_id") == req["tool_call_id"], 10)
    check(any(m.get("type") == "conversation_restored" for m in got), "restore_conversation succeeds for an unsaved in-flight conversation")
    check(restored is not None, "pending approval request is replayed on restore")

    # 5. Another user naming the in-flight id is refused before admission.
    await other.send(chat("hijack", conversation_id=conv_id))
    resp, _ = await recv_until(other, lambda m: m.get("type") in ("error", "run_started"), 10)
    check(resp is not None and resp.get("type") == "error" and resp.get("error_type") == "authorization", "foreign in-flight conversation id is refused with authorization")
    await other.send(chat("hijack", conversation_id="  " + conv_id + "  "))
    resp2, _ = await recv_until(other, lambda m: m.get("type") in ("error", "run_started"), 10)
    check(resp2 is not None and resp2.get("type") == "error", "padded foreign conversation id is refused too")

    # 6. Answering the approval resumes the run to completion and persists it.
    await owner.send(json.dumps({"type": "tool_approval_response", "tool_call_id": req["tool_call_id"], "approved": True, "run_id": run_id}))
    done, got = await recv_until(owner, lambda m: m.get("type") == "run_status" and m["run"]["status"] in ("completed", "failed", "cancelled"), 30)
    check(done is not None and done["run"]["status"] == "completed", "run completes after the replayed approval is answered")
    check(any(m.get("type") == "conversation_saved" and m.get("conversation_id") == conv_id for m in got), "conversation_saved names the run's conversation")
    status, body = get(f"/api/conversations/{conv_id}", OWNER)
    check(status == 200 and body and not body.get("in_flight") and any("END[PR956]" in m.get("content", "") for m in body["messages"]), "stored record is served once the run ends and holds the final answer")

    await owner.close()
    await other.close()
    sys.exit(1 if FAILED else 0)


asyncio.run(main())
