"""End-to-end scenario for PR #1074 (issue #1014) against a running backend.

Two tracked agent runs on one socket, finishing out of order:

* the older run is rate limited after the newer one started, and
* the newer run is rate limited while the older one is still working.

Each run's terminal status must come from its own turn -- live, and in the
`runs_snapshot` a reconnecting browser receives -- and a failed run's
conversation must be in the store with its prompt and the failure marker,
not vanish from history. The scripted mock answers 429 for `fail=429`.
"""
import asyncio
import json
import os
import sys
import urllib.request

import websockets

PORT = os.environ["ATLAS_PORT"]
USER = "owner@example.com"
FAILED = 0
FAILED_MARKER = "[This turn ended with an error before it finished.]"


def check(ok, label):
    global FAILED
    print(("PASSED: " if ok else "FAILED: ") + label, flush=True)
    if not ok:
        FAILED += 1


def chat(content):
    return json.dumps({
        "type": "chat", "content": content, "model": "scripted-mock", "save_mode": "server",
        "agent_mode": True, "agent_max_steps": 5, "selected_tools": ["atlas_sleep"],
    })


async def recv_until(ws, pred, timeout=60):
    while True:
        m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        # Approve tool calls the way the browser does with auto-approve on.
        req = m.get("frame") if m.get("type") == "background_activity" else m
        if req and req.get("type") == "tool_approval_request":
            await ws.send(json.dumps({
                "type": "tool_approval_response", "tool_call_id": req["tool_call_id"],
                "approved": True, "arguments": req.get("arguments"),
                "run_id": req.get("run_id"), "conversation_id": req.get("conversation_id"),
            }))
        if pred(m):
            return m


async def connect():
    return await websockets.connect(
        f"ws://127.0.0.1:{PORT}/ws", additional_headers={"X-User-Email": USER})


async def snapshot():
    ws = await connect()
    try:
        await ws.send(json.dumps({"type": "list_runs"}))
        m = await recv_until(ws, lambda m: m.get("type") == "runs_snapshot")
        return {r["run_id"]: r for r in m["runs"]}
    finally:
        await ws.close()


def stored(conversation_id):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/api/conversations/{conversation_id}",
        headers={"X-User-Email": USER})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


async def scenario(name, a_prompt, b_prompt, failing):
    ws = await connect()
    try:
        await ws.send(chat(a_prompt))
        a = await recv_until(ws, lambda m: m.get("type") == "run_started")
        await ws.send(chat(b_prompt))
        b = await recv_until(ws, lambda m: m.get("type") == "run_started")
        runs = {"A": a, "B": b}
        ids = {a["run_id"]: "A", b["run_id"]: "B"}
        live = {}
        while len(live) < 2:
            m = await recv_until(
                ws,
                lambda m: m.get("type") == "run_status" and m["run"]["run_id"] in ids
                and m["run"]["status"] in ("completed", "failed", "cancelled"),
                timeout=90)
            live[ids[m["run"]["run_id"]]] = m["run"]
    finally:
        await ws.close()
    # The failed turn is committed before its error frame; give the store a beat.
    await asyncio.sleep(1)
    snap = await snapshot()
    clean = "B" if failing == "A" else "A"
    for key in ("A", "B"):
        s = snap[runs[key]["run_id"]]
        print(f"  [{name}] run {key}: live={live[key]['status']}/{live[key].get('error')} "
              f"snapshot={s['status']}/{s.get('error')}")
    for source, rec in (("live", live), ("snapshot", {k: snap[runs[k]["run_id"]] for k in runs})):
        check(rec[failing]["status"] == "failed" and rec[failing].get("error") == "rate_limit",
              f"[{name}] {source}: rate-limited run {failing} is failed/rate_limit")
        check(rec[clean]["status"] == "completed" and not rec[clean].get("error"),
              f"[{name}] {source}: clean run {clean} is completed")
    conv = stored(runs[failing]["conversation_id"])
    messages = (conv or {}).get("messages") or []
    contents = [m.get("content") for m in messages]
    check(bool(messages) and contents[0] == (a_prompt if failing == "A" else b_prompt),
          f"[{name}] failed run's conversation is stored with its prompt")
    check(contents.count(FAILED_MARKER) == 1 and contents[-1] == FAILED_MARKER,
          f"[{name}] failed run's stored turn ends with the failure marker, once")
    check(any(m.get("message_type") == "tool_call" for m in messages),
          f"[{name}] failed run's completed tool call is stored")


async def main():
    # The older run fails after the newer one has started and finished.
    await scenario("older-fails-last",
                   "label=A sleep=3 steps=1 fail=429", "label=B sleep=1 steps=1 words=3", "A")
    # The newer run fails first; the older one finishes cleanly afterwards.
    await scenario("newer-fails-first",
                   "label=A sleep=8 steps=1 words=3", "label=B sleep=1 steps=1 fail=429", "B")


asyncio.run(main())
sys.exit(1 if FAILED else 0)
