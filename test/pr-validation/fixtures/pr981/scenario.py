"""End-to-end scenario for PR #981 against a running backend.

Covers the contract the mid-run live refresh depends on (issue #980): while a
parallel run is still executing, `GET /api/conversations/{id}` serves the
run's live session and that session grows as the run works -- the tool row
that lands after the view was opened is visible there before the run ends,
which is what lets the client append it without waiting for the final reload.

Drives the real WebSocket endpoint and REST API with the scripted mock model,
as one user over the connection that started the run. Exits non-zero on the
first failed check; prints one PASSED/FAILED line per check.
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


def chat(content, selected_tools, conversation_id=None):
    frame = {
        "type": "chat", "content": content, "model": "scripted-mock",
        "selected_tools": selected_tools, "agent_mode": True, "save_mode": "server",
    }
    if conversation_id:
        frame["conversation_id"] = conversation_id
    return json.dumps(frame)


def tool_rows(record):
    """The tool rows a live (or stored) transcript holds."""
    rows = []
    for m in record.get("messages", []):
        metadata = m.get("metadata") or {}
        if m.get("message_type") == "tool_call" or metadata.get("message_type") == "tool_call":
            rows.append(m)
    return rows


async def main():
    origin = await connect(OWNER)

    # 1. A tracked agent run starts and pauses on the first tool approval.
    prompt = "label=PR981 sleep=1 steps=2 words=3"
    await origin.send(chat(prompt, ["atlas_sleep"]))
    started, _ = await recv_until(origin, lambda m: m.get("type") == "run_started")
    check(started is not None, "run_started arrives for the new-chat agent turn")
    if started is None:
        await origin.close()
        sys.exit(1)
    run_id, conv_id = started["run_id"], started["conversation_id"]

    approval1, _ = await recv_until(origin, lambda m: m.get("type") == "tool_approval_request")
    check(approval1 is not None, "run pauses on the first tool approval")

    # 2. The snapshot taken now is what a joined view would load. It is in
    # flight and holds no tool row yet -- the first call has not executed.
    status, snap1 = get(f"/api/conversations/{conv_id}", OWNER)
    check(status == 200 and snap1 and snap1.get("in_flight") is True,
          "live record is served as in_flight while the run is paused")
    snap1_rows = len(tool_rows(snap1 or {}))

    # 3. Approve the first call. The tool executes, the run loops, and pauses
    # again on the second call -- still in flight, now with a tool row behind it.
    await origin.send(json.dumps({
        "type": "tool_approval_response",
        "tool_call_id": approval1["tool_call_id"], "approved": True, "run_id": run_id,
    }))
    approval2, _ = await recv_until(origin, lambda m: m.get("type") == "tool_approval_request")
    check(approval2 is not None, "run pauses on a second tool approval after executing the first")

    # 4. The mid-run poll sees the newer activity. This is the guarantee the
    # client's `refreshJoinedConversation(..., { live: true })` relies on: the
    # row that landed after the view was opened is already in the live record,
    # without waiting for the run to end.
    status, snap2 = get(f"/api/conversations/{conv_id}", OWNER)
    check(status == 200 and snap2 and snap2.get("in_flight") is True,
          "live record is still in_flight after the first tool executes")
    snap2_rows = tool_rows(snap2 or {})
    check(len(snap2_rows) > snap1_rows,
          f"a tool row that landed mid-run is visible to a poll ({snap1_rows} -> {len(snap2_rows)})")
    check(all((m.get("metadata") or {}).get("tool_call_id") for m in snap2_rows),
          "live tool rows carry tool_call_id, the key the client matches them on")

    # 5. Approve the second call; the run completes and persists.
    await origin.send(json.dumps({
        "type": "tool_approval_response",
        "tool_call_id": approval2["tool_call_id"], "approved": True, "run_id": run_id,
    }))
    done, _ = await recv_until(
        origin,
        lambda m: m.get("type") == "run_status" and m["run"]["status"] in ("completed", "failed", "cancelled"),
    )
    check(done is not None and done["run"]["status"] == "completed", "run completes after both approvals")

    # 6. Once the run ends the live view stops being served; the store owns it.
    status, final = get(f"/api/conversations/{conv_id}", OWNER)
    check(status == 200 and final and not final.get("in_flight"),
          "store record is served once the run ends")
    contents = [m.get("content", "") for m in final.get("messages", [])]
    check(any("END[PR981]" in c for c in contents), "store record holds the run's final answer")
    check(len(tool_rows(final)) >= len(snap2_rows),
          "stored transcript holds at least the tool rows the mid-run poll saw")

    await origin.close()
    sys.exit(1 if FAILED else 0)


asyncio.run(main())
