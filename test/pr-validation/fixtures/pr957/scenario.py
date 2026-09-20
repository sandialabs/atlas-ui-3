"""End-to-end scenario for PR #957 against a running backend.

The mid-stream reopen: a conversation whose run is streaming a long answer is
left and opened again. Drives the real WebSocket endpoint and REST API as one
user with two connections (the second standing in for another tab). Exits
non-zero on the first failed check; prints one PASSED/FAILED line per check.
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
LABEL = "PR957"
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
            if pred(m):
                return m
    except asyncio.TimeoutError:
        return None


def chat(content, conversation_id=None):
    frame = {
        "type": "chat", "content": content, "model": "scripted-mock",
        "selected_tools": ["atlas_sleep"], "agent_mode": True, "save_mode": "server",
    }
    if conversation_id:
        frame["conversation_id"] = conversation_id
    return json.dumps(frame)


def expected_answer():
    words = " ".join(f"{LABEL}-w{i}" for i in range(1, 41))
    return f"FINAL[{LABEL}] {words} END[{LABEL}]"


async def main():
    owner = await connect(OWNER)

    # A long, slowly streamed agent answer, exactly the shape the issue
    # describes: run admitted, one tool step, then several seconds of tokens.
    await owner.send(chat(f"label={LABEL} sleep=1 steps=1 words=40 stream_ms=80"))
    started = await recv_until(owner, lambda m: m.get("type") == "run_started")
    check(started is not None, "run arrives for the agent turn")
    run_id, conv_id = started["run_id"], started["conversation_id"]

    req = await recv_until(owner, lambda m: m.get("type") == "tool_approval_request")
    check(req is not None and req.get("run_id") == run_id, "run pauses on the tool approval")
    await owner.send(json.dumps({"type": "tool_approval_response", "tool_call_id": req["tool_call_id"], "approved": True, "run_id": run_id}))

    # The final answer streams now. Let a few tokens land so the reopen below
    # happens genuinely mid-stream.
    before = await recv_until(
        owner,
        lambda m: m.get("type") == "token_stream" and not m.get("is_last"),
        30,
    )
    check(before is not None, "the run streams its final answer")
    await asyncio.sleep(0.8)

    # 1. REST: the in-flight record carries the segment streamed so far.
    status, body = get(f"/api/conversations/{conv_id}", OWNER)
    check(
        status == 200 and bool(body) and body.get("in_flight") is True,
        "GET /api/conversations/{id} serves the in-flight view mid-stream",
    )
    first_snapshot = (body or {}).get("streaming_text") or ""
    check(
        bool(first_snapshot) and first_snapshot.startswith("FINAL[" + LABEL + "]"),
        "live record carries the open segment from its beginning as streaming_text",
    )
    check((body or {}).get("streaming_truncated") is False, "the segment is not reported truncated")

    await asyncio.sleep(0.6)
    status, body2 = get(f"/api/conversations/{conv_id}", OWNER)
    check(
        len((body2 or {}).get("streaming_text") or "") > len(first_snapshot),
        "streaming_text grows while the run keeps streaming",
    )

    # 2. A second connection of the same user ("another tab") reopens the
    #    conversation: the replay frame carries the segment, and no live
    #    stream follows it there.
    second = await connect(OWNER)
    await second.send(json.dumps({"type": "restore_conversation", "conversation_id": conv_id, "messages": []}))
    replay = await recv_until(second, lambda m: m.get("type") == "token_stream" and m.get("replay"), 10)
    check(replay is not None, "restore_conversation replays the open segment")
    check(
        bool(replay) and replay.get("token", "").startswith("FINAL[" + LABEL + "]"),
        "the replay carries the segment from its beginning",
    )
    check(
        bool(replay) and replay.get("run_id") == run_id and replay.get("conversation_id") == conv_id,
        "the replay frame is tagged with the run's ids",
    )
    stray = await recv_until(second, lambda m: m.get("type") == "token_stream" and not m.get("replay"), 1.0)
    check(stray is None, "a tab that is not the run's socket receives no live stream after the replay")

    # 3. The same connection reopens: replay first, then live tokens continue
    #    to the end of the answer without a gap.
    await owner.send(json.dumps({"type": "restore_conversation", "conversation_id": conv_id, "messages": []}))
    own_replay = await recv_until(owner, lambda m: m.get("type") == "token_stream" and m.get("replay"), 10)
    check(own_replay is not None, "the run's own connection is replayed on restore too")

    assembled = (own_replay or {}).get("token", "")
    terminal = None
    while terminal is None:
        m = await recv_until(owner, lambda m: m.get("type") in ("token_stream", "run_status"), 45)
        if m is None:
            break
        if m.get("type") == "token_stream" and not m.get("replay"):
            assembled += m.get("token", "")
        elif m.get("type") == "run_status" and m["run"]["status"] in ("completed", "failed", "cancelled"):
            terminal = m
    check(
        terminal is not None and terminal["run"]["status"] == "completed",
        "the run completes while the client is back on the conversation",
    )
    check(
        assembled == expected_answer(),
        "replay plus live continuation reassembles the complete answer",
    )

    # 4. Once the run has ended there is nothing left to replay, and the
    #    stored record is served.
    status, body3 = get(f"/api/conversations/{conv_id}", OWNER)
    check(status == 200 and body3 and not body3.get("in_flight"), "the stored record is served once the run ends")
    check(not (body3 or {}).get("streaming_text"), "no streaming_text remains after the run ends")
    check(
        bool(body3) and any("END[" + LABEL + "]" in (m.get("content") or "") for m in body3.get("messages", [])),
        "the stored transcript holds the complete answer",
    )
    await owner.send(json.dumps({"type": "restore_conversation", "conversation_id": conv_id, "messages": []}))
    late = await recv_until(owner, lambda m: m.get("type") in ("token_stream", "conversation_restored"), 10)
    check(
        late is not None and late.get("type") == "conversation_restored",
        "restore after the run ends sends no replay frame",
    )

    await owner.close()
    await second.close()
    sys.exit(1 if FAILED else 0)


asyncio.run(main())