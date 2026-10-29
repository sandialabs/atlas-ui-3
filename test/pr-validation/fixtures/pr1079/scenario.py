"""End-to-end scenario for follow-up workspace re-validation (#1079) against a
running backend.

The launching conversation sends follow-ups to sub-conversations while the
saved workspace changes underneath them. Everything goes through the real
WebSocket chat endpoint, the real workspaces REST API and the real
conversations API; the model is a puppet (``mock_llm.py``) only so the tool
calls can be driven in order.

Checks:

  1. A follow-up succeeds while the saved workspace still exists and is
     accessible to the caller.
  2. After the workspace is deleted (REST), a follow-up on a child launched
     under it is refused with the model-readable refusal, and the refused
     follow-up appends nothing to the child's transcript.
  3. After a workspace with the SAME name is recreated (a different id), the
     follow-up is still refused: the saved workspace is resolved by id, never
     by name, so the replacement is not substituted.
  4. A RENAMED workspace (same id) does not break the follow-up, and the
     follow-up handle carries the current name.

Exits non-zero if any check failed; prints one PASSED/FAILED line each.
"""
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

import websockets

PORT = os.environ["ATLAS_PORT"]
WS = f"ws://127.0.0.1:{PORT}/ws"
API = f"http://127.0.0.1:{PORT}"
USER = "owner@example.com"
TOOLS = ["atlas_discover_launch_options", "atlas_launch", "atlas_follow_up"]
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


def workspace_by_name(name):
    _, body = request("/api/workspaces", USER)
    for w in (body or {}).get("workspaces", []):
        if w.get("name") == name:
            return w
    return None


def ensure_workspace(name):
    w = workspace_by_name(name)
    if w:
        return w
    status, body = request("/api/workspaces", USER, "POST", {
        "name": name,
        "description": f"follow-up workspace re-validation workspace {name}",
        "config": {"selected_tools": ["atlas_sleep"], "selected_data_sources": []},
    })
    return (body or {}).get("workspace") if status == 200 else None


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
    """One parent turn; auto-approves tools; returns (tool_results, parent)."""
    await ws.send(chat(directives, conversation_id))
    tool_results = []
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
    return tool_results, my_run


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
    import time
    deadline = time.time() + timeout
    messages = []
    while time.time() < deadline:
        _, messages = stored_messages(conversation_id)
        if len(messages) >= count and messages[-1].get("role") == "assistant":
            return messages
        time.sleep(1)
    return messages


async def launch_child(ws, ws_name, task="CHILDTASK"):
    """One parent turn that launches a sub-conversation and waits for it."""
    results, parent = await run_turn(ws, f"plan=launch task={task} ws={ws_name}")
    handle = handle_from(result_text(result_for(results, "atlas_launch")))
    parent_conv = (parent or {}).get("conversation_id")
    return handle, parent_conv


async def follow_up(ws, run_id, conversation_id):
    results, _ = await run_turn(
        ws, f"plan=follow_up run={run_id}", conversation_id=conversation_id,
    )
    return result_for(results, "atlas_follow_up")


async def main():
    # ---------------------------------------------------------------- setup
    research = ensure_workspace("Research")
    check(bool(research and research.get("id")), "a workspace can be created over the REST API", research)
    beta = ensure_workspace("Beta")
    check(bool(beta and beta.get("id")), "a second workspace can be created", beta)

    ws = await websockets.connect(WS, additional_headers={"X-User-Email": USER})

    # ------------------------------- 1. accessible workspace: follow-up works
    launched, parent_conv = await launch_child(ws, "Research", task="KEEPWS")
    check(bool(launched.get("run_id")), "the parent launches a sub-conversation under Research", launched)
    child_conv = launched.get("conversation_id")
    messages = wait_for_messages(child_conv, 2)
    check(len(messages) == 2, "the sub-conversation answers and is saved", messages)

    frame = await follow_up(ws, launched["run_id"], parent_conv)
    check((frame or {}).get("success") is True, "a follow-up succeeds while the saved workspace is accessible", result_text(frame))
    followed = handle_from(result_text(frame))
    check(followed.get("conversation_id") == child_conv, "the follow-up continues the same sub-conversation", followed)
    wait_for_messages(child_conv, 4)

    # --------------------------------------- 2. deleted workspace: refused
    second, second_parent = await launch_child(ws, "Research", task="DELWS")
    check(bool(second.get("run_id")), "a second child is launched for the deletion scenario", second)
    second_conv = second.get("conversation_id")
    wait_for_messages(second_conv, 2)

    research_id = (workspace_by_name("Research") or {}).get("id")
    status, _ = request(f"/api/workspaces/{research_id}", USER, "DELETE")
    check(status == 200, "the saved workspace is deleted over the REST API", status)

    frame = await follow_up(ws, second["run_id"], second_parent)
    text = result_text(frame)
    check(
        (frame or {}).get("success") is False and "no longer available to you" in text,
        "a follow-up is refused after its workspace is deleted",
        text,
    )
    check("Launch a new sub-conversation" in text, "the refusal tells the model what to do instead", text)
    _, after = stored_messages(second_conv)
    check(len(after) == 2, "the refused follow-up appends nothing to the child's transcript", [m.get("content", "")[:60] for m in after])

    # ------------------- 3. same-name replacement (new id): still refused
    status, body = request("/api/workspaces", USER, "POST", {
        "name": "Research",
        "description": "same-name replacement created after the deletion",
        "config": {"selected_tools": ["atlas_sleep"], "selected_data_sources": []},
    })
    replacement = (body or {}).get("workspace") or {}
    check(
        status == 200 and replacement.get("id") and replacement.get("id") != research_id,
        "a same-name workspace with a different id is recreated",
        (status, replacement),
    )

    frame = await follow_up(ws, second["run_id"], second_parent)
    text = result_text(frame)
    check(
        (frame or {}).get("success") is False and "no longer available to you" in text,
        "the follow-up is still refused: the saved workspace is resolved by id, never by name",
        text,
    )

    # --------------------------------- 4. renamed workspace: follow-up works
    third, third_parent = await launch_child(ws, "Beta", task="RENWS")
    check(bool(third.get("run_id")), "a child is launched under the Beta workspace", third)
    third_conv = third.get("conversation_id")
    wait_for_messages(third_conv, 2)

    beta_id = (workspace_by_name("Beta") or {}).get("id")
    status, body = request(f"/api/workspaces/{beta_id}", USER, "PUT", {"name": "Beta Renamed"})
    check(status == 200, "the workspace is renamed over the REST API (same id)", status)

    frame = await follow_up(ws, third["run_id"], third_parent)
    text = result_text(frame)
    check((frame or {}).get("success") is True, "a follow-up keeps working after the workspace is renamed", text)
    renamed_handle = handle_from(text)
    check(
        renamed_handle.get("workspace") == "Beta Renamed",
        "the follow-up handle carries the workspace's current name",
        renamed_handle,
    )
    wait_for_messages(third_conv, 4)

    await ws.close()
    sys.exit(1 if FAILED else 0)


asyncio.run(main())