"""End-to-end scenario for PR #959 against a running backend.

Covers the transcript contract the joined-conversation refresh depends on.
When a conversation is opened while its run is executing, the client shows
the run's live snapshot and refreshes from the store once the run ends. The
refresh appends only what the view is missing (issue #959); that works only
if the stored transcript still lines up with what the client saw in flight.

Drives the real WebSocket endpoint and REST API with the scripted mock model,
as one user over two connections (the tab that started the run, and the tab
that joins it mid-run). Exits non-zero on the first failed check; prints one
PASSED/FAILED line per check.
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
LABEL = "PR959"
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


def chat(content, conversation_id=None):
    frame = {
        "type": "chat", "content": content, "model": "scripted-mock",
        "selected_tools": ["atlas_sleep"], "agent_mode": True, "save_mode": "server",
    }
    if conversation_id:
        frame["conversation_id"] = conversation_id
    return json.dumps(frame)


# Mirror of ChatContext.jsx's PROSE_ROW_TYPES: agent narration is one row
# wearing two type names -- persisted as 'agent_intermediate', streamed as a
# plain assistant row -- so the pair must compare equal.
PROSE_ROW_TYPES = {"chat", "agent_intermediate"}

# Mirror of ChatContext.jsx's LIVE_ONLY_ROW_TYPES: rows that exist only in the
# live view and have no stored counterpart, skipped during alignment.
LIVE_ONLY_ROW_TYPES = {
    "agent_status", "agent_reason", "agent_observe", "agent_request_input",
    "agent_error", "tool_log", "warning", "iframe", "system", "canvas_error",
    # The backend writes only chat / tool_call / agent_intermediate to a run's
    # transcript, so the approval row the run paused on is view chrome with no
    # stored counterpart.
    "tool_approval_request",
}


def row_key_client(msg):
    """The matching rule ChatContext.refreshJoinedConversation applies.

    Always a (type, identity, extra) triple so two rows can only compare
    equal through the same branch: tool rows on their tool_call_id, prose
    rows on role and content. Prose types are collapsed
    to a single bucket, matching sameTranscriptRow's PROSE_ROW_TYPES.
    """
    mtype = (msg.get("metadata") or {}).get("message_type") or msg.get("message_type") or "chat"
    if mtype == "tool_call":
        tc = (msg.get("metadata") or {}).get("tool_call_id") or msg.get("tool_call_id")
        if tc:
            return (mtype, tc, "")
    bucket = "prose" if mtype in PROSE_ROW_TYPES else mtype
    return (bucket, msg.get("role") or "", msg.get("content") or "")


def aligns_prefix(view_rows, stored_rows):
    """The client's alignment walk: every live-only row is skipped, every
    remaining view row must match the stored rows one-to-one in order. Returns
    True when the view is a prefix of the stored transcript (the refresh
    appends only the tail) and False when it has diverged (the refresh
    refuses and the client falls back to the full reload)."""
    live_only = LIVE_ONLY_ROW_TYPES
    view = [r for r in view_rows if r.get("message_type", "chat") not in live_only
            and r.get("type", "chat") not in live_only]
    i = 0
    for stored in stored_rows:
        if i >= len(view):
            break
        if row_key_client(view[i]) != row_key_client(stored):
            return False
        i += 1
    return True


async def main():
    origin = await connect(OWNER)
    joined = await connect(OWNER)

    # 1. A tracked agent run starts; its approval pauses it mid-flight.
    prompt = "label=PR959 sleep=1 steps=1 words=3"
    await origin.send(chat(prompt))
    started, _ = await recv_until(origin, lambda m: m.get("type") == "run_started")
    check(started is not None, "run_started arrives for the new-chat agent turn")
    run_id, conv_id = started["run_id"], started["conversation_id"]
    approval, _ = await recv_until(origin, lambda m: m.get("type") == "tool_approval_request")
    check(approval is not None, "run pauses on a tool approval")

    # 2. While the run is in flight, the joining tab reads the snapshot and
    # opens the conversation the way the UI does (restore_conversation).
    status, snapshot = get(f"/api/conversations/{conv_id}", OWNER)
    check(status == 200 and snapshot and snapshot.get("in_flight") is True,
          "joining tab reads the in-flight transcript from the run session")
    check(snapshot and any(m["role"] == "user" and "PR959" in m["content"] for m in snapshot.get("messages", [])),
          "in-flight snapshot contains the user prompt")
    await joined.send(json.dumps({
        "type": "restore_conversation", "conversation_id": conv_id,
        "messages": [m for m in snapshot.get("messages", [])
                     if (m.get("message_type") or "chat") != "tool_call"],
    }))
    _, got = await recv_until(joined, lambda m: m.get("type") in ("conversation_restored", "error"), 10)
    check(any(m.get("type") == "conversation_restored" for m in got),
          "joining tab's restore_conversation is accepted while the run executes")

    # 3. The origin tab answers the approval; the run completes and persists.
    await origin.send(json.dumps({
        "type": "tool_approval_response",
        "tool_call_id": approval["tool_call_id"], "approved": True, "run_id": run_id,
    }))
    done, got = await recv_until(origin, lambda m: m.get("type") == "run_status" and m["run"]["status"] in ("completed", "failed", "cancelled"))
    check(done is not None and done["run"]["status"] == "completed", "run completes after the approval is answered")
    check(any(m.get("type") == "conversation_saved" and m.get("conversation_id") == conv_id for m in got),
          "conversation_saved names the run's conversation")

    # 4. The store now holds the final transcript. The refresh contract: it
    # extends what the joining tab saw, under the matching rule the client
    # uses, so the client appends the tail instead of replacing the list.
    status, final = get(f"/api/conversations/{conv_id}", OWNER)
    check(status == 200 and final and not final.get("in_flight"), "store record is served once the run ends")
    contents = [m.get("content", "") for m in final.get("messages", [])]
    check(any("END[PR959]" in c for c in contents), "store record holds the run's final answer")
    check(aligns_prefix(snapshot.get("messages", []), final.get("messages", [])),
          "stored transcript aligns with the in-flight snapshot the joining tab saw")
    tool_rows = [m for m in final.get("messages", []) if (m.get("metadata") or {}).get("message_type") == "tool_call"]
    check(tool_rows and all((m.get("metadata") or {}).get("tool_call_id") for m in tool_rows),
          "stored tool rows carry tool_call_id, the key the client matches them on")

    # 5. After the run ends the refresh re-seeds the session from the store.
    await joined.send(json.dumps({"type": "restore_conversation", "conversation_id": conv_id, "messages": []}))
    _, got = await recv_until(joined, lambda m: m.get("type") in ("conversation_restored", "error"), 10)
    check(any(m.get("type") == "conversation_restored" for m in got),
          "restore_conversation re-seeds the joining tab from the saved record after the run ends")

    await origin.close()
    await joined.close()
    sys.exit(1 if FAILED else 0)


asyncio.run(main())