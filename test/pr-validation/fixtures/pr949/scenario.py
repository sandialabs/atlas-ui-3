"""End-to-end scenario for PR #949 against a running backend.

PR #949: ``atlas_discover_launch_options`` publishes the caller's authorized
workspaces and models, and a successful discovery is a hard precondition for
``atlas_launch``.

Everything here goes through the real WebSocket chat endpoint and the real
workspaces REST API, against a real backend with launch enabled. The model is
a puppet (``mock_llm.py``) only so the *order* of the two tool calls can be
driven deliberately -- nothing on the path under test is stubbed.

Exits non-zero on the first failed check; prints one PASSED/FAILED line each.
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
LAUNCH_TOOLS = ["atlas_launch"]
FAILED = 0


def check(ok, label, detail=""):
    global FAILED
    print(("PASSED: " if ok else "FAILED: ") + label, flush=True)
    if not ok:
        FAILED += 1
        if detail:
            print("        " + str(detail)[:600], flush=True)


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


def chat(content):
    return json.dumps({
        "type": "chat",
        "content": content,
        "model": "scripted-mock",
        "save_mode": "server",
        "selected_tools": LAUNCH_TOOLS,
        "agent_mode": True,
    })


async def run_turn(ws, directives, timeout=90):
    """Send one turn, auto-approve every tool, and return what the tools said.

    Agent mode emits no ``chat_response``: the answer arrives as
    ``token_stream`` tokens and the turn ends on a terminal ``run_status``.
    Returns (tool_results, answer_text, frames) where tool_results is the list
    of ``tool_complete`` frames -- the direct evidence of what each tool
    actually returned, rather than the model's retelling of it.
    """
    await ws.send(chat(directives))
    frames, tokens, tool_results = [], [], []
    # A launched sub-conversation streams its OWN run_started/run_status and
    # tool frames down this same socket. Bind to this turn's run id -- the
    # first run_started with no parent -- or a child's completion ends the
    # loop before the parent turn has finished.
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
            if my_run is None or m.get("run_id") in (None, my_run):
                tokens.append(m.get("content") or m.get("token") or "")
            continue
        frames.append(m)
        if kind == "run_started" and my_run is None and not m.get("parent_run_id"):
            my_run = m.get("run_id")
            continue
        run_id = m.get("run_id")
        if kind == "tool_approval_request":
            await ws.send(json.dumps({
                "type": "tool_approval_response",
                "tool_call_id": m["tool_call_id"],
                "approved": True,
                "run_id": run_id,
            }))
            continue
        if kind == "tool_complete":
            # Only this turn's tools; a child runs tools of its own.
            if my_run is None or run_id in (None, my_run):
                tool_results.append(m)
            continue
        if kind == "run_status":
            run = m.get("run") or {}
            if run.get("run_id") == my_run and run.get("status") in ("completed", "failed"):
                break
            continue
        if kind == "error":
            break
    return tool_results, "".join(tokens), frames


def result_for(tool_results, name):
    """The last tool_complete frame for `name`, or None."""
    matches = [r for r in tool_results if r.get("tool_name") == name]
    return matches[-1] if matches else None


def result_text(frame):
    """`result` is a dict for discovery and a string for launch; want text."""
    value = (frame or {}).get("result")
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value)


def tool_said(answer):
    """The mock echoes the tool messages it was handed as TOOLSAID<<<...>>>."""
    if not answer or "TOOLSAID<<<" not in answer:
        return ""
    return answer.split("TOOLSAID<<<", 1)[1].rsplit(">>>", 1)[0]


async def main():
    # ---------------------------------------------------------------- setup
    status, body = request("/api/workspaces", USER)
    existing = [w for w in (body or {}).get("workspaces", []) if w.get("name") == "Research"]
    if existing:
        workspace_id = existing[0].get("id")
        check(True, "a saved workspace named Research exists")
    else:
        status, body = request("/api/workspaces", USER, "POST", {
            "name": "Research",
            "description": "PR949 validation workspace",
            "config": {"selected_tools": ["atlas_sleep"], "selected_data_sources": []},
        })
        check(status == 200, "a saved workspace can be created over the REST API", status)
        workspace_id = (body or {}).get("workspace", {}).get("id")
    check(bool(workspace_id), "the saved workspace has an id")

    status, body = request("/api/config", USER)
    # /api/config -> {"tools": [ {"server": "atlas", "tools": ["canvas", ...]}, ... ]}
    # Names are listed with the server prefix stripped.
    atlas_tools = []
    for server in (body or {}).get("tools") or []:
        if isinstance(server, dict) and server.get("server") == "atlas":
            atlas_tools = server.get("tools") or []
    check(
        "discover_launch_options" in atlas_tools,
        "the discovery tool is advertised by /api/config when launch is enabled",
        atlas_tools,
    )
    check(
        "launch" in atlas_tools,
        "atlas_launch is advertised alongside it",
        atlas_tools,
    )

    ws = await connect(USER)
    LAUNCH, DISCOVER = "atlas_launch", "atlas_discover_launch_options"

    # ------------------------------------ 1. launch without discovery first
    results, answer, frames = await run_turn(ws, "plan=launch_only")
    launched = result_for(results, LAUNCH)
    check(launched is not None, "the launch tool ran", [r.get("tool_name") for r in results])
    text = result_text(launched)
    check(
        (launched or {}).get("success") is False,
        "atlas_launch with no prior discovery is refused",
        text,
    )
    check(
        "discover_launch_options" in text,
        "the refusal names the discovery tool the model must run first",
        text,
    )
    check(
        "blocked" in text.lower(),
        "the refusal says the launch is blocked, not that it failed obscurely",
        text,
    )
    check(
        result_for(results, DISCOVER) is None,
        "nothing ran discovery behind the model's back",
    )
    check(
        not any(f.get("type") == "run_started" and f.get("parent_run_id") for f in frames),
        "no sub-conversation run was started by the refused launch",
    )
    check(
        "discover_launch_options" in answer,
        "the refusal reached the model, not just the transcript",
        answer,
    )

    # ------------------------------------------- 2. discovery, then launch
    results, _, frames = await run_turn(ws, "plan=discover_launch ws=Research model=scripted-mock")
    discovered = result_for(results, DISCOVER)
    launched = result_for(results, LAUNCH)
    check((discovered or {}).get("success") is True, "discovery succeeds", discovered)
    check(
        (launched or {}).get("success") is True,
        "atlas_launch after discovery starts the sub-conversation",
        result_text(launched),
    )
    check(
        "Research" in result_text(launched),
        "the sub-conversation runs under the discovered workspace",
        result_text(launched),
    )
    check(
        "run_id" in result_text(launched),
        "the launch hands back the child's run id",
        result_text(launched),
    )

    # ------------------------------- 3. both calls in one step, launch first
    results, _, _ = await run_turn(ws, "plan=same_step ws=Research model=scripted-mock")
    launched = result_for(results, LAUNCH)
    check(
        (launched or {}).get("success") is True,
        "a single step holding [launch, discovery] still discovers first, then launches",
        result_text(launched),
    )

    # ------------------------------------ 4. shape of what discovery returns
    results, _, _ = await run_turn(ws, "plan=discover_only")
    payload = result_text(result_for(results, DISCOVER))
    check("Research" in payload, "discovery reports the user's saved workspace", payload)
    check("scripted-mock" in payload, "discovery reports an authorized model name", payload)
    check(
        "provider" not in payload.lower(),
        "discovery reports bare model names, exactly as the tool description promises",
        payload,
    )

    # -------------------------------- 5. a workspace outside the discovered set
    results, _, _ = await run_turn(ws, "plan=discover_launch ws=NotADiscoveredWorkspace model=scripted-mock")
    launched = result_for(results, LAUNCH)
    check(
        (launched or {}).get("success") is False,
        "a launch naming an undiscovered workspace is refused",
        result_text(launched),
    )

    # --------------------------------- 6. a model outside the discovered set
    results, _, _ = await run_turn(ws, "plan=discover_launch ws=Research model=not-a-real-model")
    launched = result_for(results, LAUNCH)
    check(
        (launched or {}).get("success") is False,
        "a launch naming an undiscovered model is refused",
        result_text(launched),
    )

    await ws.close()
    sys.exit(1 if FAILED else 0)


asyncio.run(main())
