"""End-to-end scenario for ``atlas_follow_up`` against a running backend.

The launching conversation sends a follow-up message to a sub-conversation it
launched. Everything goes through the real WebSocket chat endpoint and the
real conversations/workspaces REST API, with launch enabled. The model is a
puppet (``mock_llm.py``) only so the tool calls can be driven in order.

Exits non-zero if any check failed; prints one PASSED/FAILED line each.
"""
import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request

import websockets

PORT = os.environ["ATLAS_PORT"]
WS = f"ws://127.0.0.1:{PORT}/ws"
API = f"http://127.0.0.1:{PORT}"
USER = "owner@example.com"
OTHER = "intruder@example.com"
TOOLS = ["atlas_discover_launch_options", "atlas_launch", "atlas_follow_up", "atlas_result"]
FAILED = 0


def check(ok, label, detail=""):
    global FAILED
    print(("PASSED: " if ok else "FAILED: ") + label, flush=True)
    if not ok:
        FAILED += 1
        if detail:
            print("        " + str(detail)[:800], flush=True)


def request(path, user, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={"X-User-Email": user, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None


def chat(content, conversation_id=None):
    msg = {
        "type": "chat",
        "content": content,
        "model": "scripted-mock",
        "save_mode": "server",
        "selected_tools": TOOLS,
        "agent_mode": True,
    }
    if conversation_id:
        msg["conversation_id"] = conversation_id
    return json.dumps(msg)


async def run_turn(ws, directives, conversation_id=None, timeout=90):
    """One parent turn; auto-approves tools; returns (tool_results, parent_run, frames).

    Child runs stream their own frames down the same socket, so the turn is
    bound to the first parentless ``run_started`` after the send.
    """
    await ws.send(chat(directives, conversation_id))
    frames, tool_results = [], []
    my_run = None
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), deadline - loop.time()))
        except asyncio.TimeoutError:
            break
        kind = m.get("type")
        if kind == "token_stream":
            continue
        frames.append(m)
        if kind == "run_started" and my_run is None and not m.get("parent_run_id"):
            my_run = m
            continue
        run_id = m.get("run_id")
        if kind == "tool_approval_request":
            await ws.send(json.dumps({
                "type": "tool_approval_response", "tool_call_id": m["tool_call_id"],
                "approved": True, "run_id": run_id,
            }))
            continue
        if kind == "tool_complete":
            if my_run is None or run_id in (None, my_run["run_id"]):
                tool_results.append(m)
            continue
        if kind == "run_status":
            run = m.get("run") or {}
            if my_run and run.get("run_id") == my_run["run_id"] and run.get("status") in (
                "completed", "failed", "cancelled",
            ):
                break
            continue
        if kind == "error":
            break
    return tool_results, my_run, frames


def result_for(tool_results, name):
    matches = [r for r in tool_results if r.get("tool_name") == name]
    return matches[-1] if matches else None


def result_text(frame):
    value = (frame or {}).get("result")
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value)


def handle_from(text):
    """The JSON handle is the last line of the launch / follow-up summary."""
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {}


def stored_messages(conversation_id, user=USER):
    status, body = request(f"/api/conversations/{conversation_id}", user)
    return status, (body or {}).get("messages") or []


def wait_for_messages(conversation_id, count, timeout=60):
    deadline = time.time() + timeout
    messages = []
    while time.time() < deadline:
        _, messages = stored_messages(conversation_id)
        if len(messages) >= count and messages[-1].get("role") == "assistant":
            return messages
        time.sleep(1)
    return messages


async def main():
    # ---------------------------------------------------------------- setup
    status, body = request("/api/workspaces", USER)
    if not [w for w in (body or {}).get("workspaces", []) if w.get("name") == "Research"]:
        status, body = request("/api/workspaces", USER, "POST", {
            "name": "Research",
            "description": "atlas_follow_up validation workspace",
            "config": {"selected_tools": ["atlas_sleep"], "selected_data_sources": []},
        })
        check(status == 200, "a saved workspace can be created over the REST API", status)

    status, body = request("/api/config", USER)
    atlas_tools = []
    for server in (body or {}).get("tools") or []:
        if isinstance(server, dict) and server.get("server") == "atlas":
            atlas_tools = server.get("tools") or []
    check("follow_up" in atlas_tools, "/api/config advertises atlas_follow_up", atlas_tools)

    ws = await websockets.connect(WS, additional_headers={"X-User-Email": USER})

    # --------------------------------------------------- 1. launch a child
    results, parent, _ = await run_turn(ws, "plan=launch")
    launched = handle_from(result_text(result_for(results, "atlas_launch")))
    check(bool(launched.get("run_id")), "the parent launches a sub-conversation", launched)
    child_conv = launched.get("conversation_id")
    parent_conv = (parent or {}).get("conversation_id")
    messages = wait_for_messages(child_conv, 2)
    check(
        len(messages) == 2 and messages[1]["content"].startswith("ECHO: CHILDTASK alpha"),
        "the sub-conversation answers its launch prompt and is saved",
        messages,
    )

    # ------------------------------ 2. follow up from a later parent turn
    results, parent2, frames = await run_turn(
        ws, f"plan=follow_up run={launched['run_id']}", conversation_id=parent_conv,
    )
    frame = result_for(results, "atlas_follow_up")
    followed = handle_from(result_text(frame))
    check((frame or {}).get("success") is True, "atlas_follow_up succeeds", result_text(frame))
    check(
        followed.get("conversation_id") == child_conv,
        "the follow-up continues the SAME sub-conversation",
        followed,
    )
    check(
        followed.get("run_id") and followed.get("run_id") != launched["run_id"],
        "the follow-up runs as a new run with its own run id",
        followed,
    )
    check(
        followed.get("previous_run_id") == launched["run_id"],
        "the handle names the run it followed",
        followed,
    )
    check(
        followed.get("parent_run_id") == (parent2 or {}).get("run_id"),
        "the follow-up hangs off the turn that sent it (so stopping that turn stops it)",
        (followed, parent2),
    )
    check(
        any(f.get("type") == "run_started" and f.get("run_id") == followed.get("run_id")
            and f.get("parent_run_id") for f in frames)
        or any((f.get("run") or {}).get("run_id") == followed.get("run_id") for f in frames),
        "the client is told about the follow-up run over the same socket",
    )

    messages = wait_for_messages(child_conv, 4)
    check(len(messages) == 4, "the follow-up and its reply are saved into the child's transcript",
          [m.get("content", "")[:80] for m in messages])
    if len(messages) == 4:
        reply = messages[3]["content"]
        check(messages[2]["content"] == "FOLLOWUPMSG beta",
              "the follow-up is the child's next user turn", messages[2])
        check(
            "user: CHILDTASK alpha" in reply and "assistant: ECHO: CHILDTASK alpha" in reply,
            "the child saw its earlier transcript when answering the follow-up",
            reply,
        )

    # ------------------------------------- 3. read the reply with atlas_result
    results, _, _ = await run_turn(
        ws, f"plan=result run={followed.get('run_id')}", conversation_id=parent_conv,
    )
    text = result_text(result_for(results, "atlas_result"))
    check("FOLLOWUPMSG beta" in text and '"result_status": "available"' in text,
          "atlas_result on the new run id returns the follow-up's reply", text)

    # ----------------------------------- 4. a second follow-up chains on
    results, _, _ = await run_turn(
        ws, f"plan=follow_up run={followed.get('run_id')}", conversation_id=parent_conv,
    )
    again = handle_from(result_text(result_for(results, "atlas_follow_up")))
    check(again.get("conversation_id") == child_conv,
          "a follow-up addressed by the previous follow-up's run id reaches the same child", again)
    messages = wait_for_messages(child_conv, 6)
    check(len(messages) == 6, "the second follow-up is appended, nothing is overwritten",
          [m.get("content", "")[:60] for m in messages])

    # -------------------------------- 5. refused while the child is running
    results, slow_parent, _ = await run_turn(ws, "plan=launch task=SLOWCHILD")
    slow = handle_from(result_text(result_for(results, "atlas_launch")))
    results, _, _ = await run_turn(
        ws, f"plan=follow_up run={slow.get('run_id')}",
        conversation_id=(slow_parent or {}).get("conversation_id"),
    )
    frame = result_for(results, "atlas_follow_up")
    check(
        (frame or {}).get("success") is False and "still working" in result_text(frame),
        "a follow-up to a sub-conversation that is still running is refused",
        result_text(frame),
    )

    # -------------------------------------------- 6. foreign / bogus run ids
    results, _, _ = await run_turn(ws, "plan=follow_up run=not-a-real-run")
    frame = result_for(results, "atlas_follow_up")
    check(
        (frame or {}).get("success") is False and "not a sub-conversation" in result_text(frame),
        "an unknown run id is refused",
        result_text(frame),
    )
    # A fresh parent conversation cannot reach the first parent's child.
    results, _, _ = await run_turn(ws, f"plan=follow_up run={launched['run_id']}")
    frame = result_for(results, "atlas_follow_up")
    check(
        (frame or {}).get("success") is False and "not a sub-conversation" in result_text(frame),
        "another conversation cannot follow up on a child it did not launch",
        result_text(frame),
    )
    await ws.close()

    intruder = await websockets.connect(WS, additional_headers={"X-User-Email": OTHER})
    results, _, _ = await run_turn(intruder, f"plan=follow_up run={launched['run_id']}")
    frame = result_for(results, "atlas_follow_up")
    check(
        (frame or {}).get("success") is not True,
        "another user cannot follow up on someone else's sub-conversation",
        result_text(frame),
    )
    status, _ = stored_messages(child_conv, OTHER)
    check(status in (403, 404), "and cannot read its transcript either", status)
    await intruder.close()

    sys.exit(1 if FAILED else 0)


asyncio.run(main())
