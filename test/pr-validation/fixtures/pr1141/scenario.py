"""End-to-end scenario for PR #1141 against a running backend.

PR #1141 renames the built-in subagent tools (``atlas_launch`` ->
``atlas_launch_subagent`` and its companions) and keeps the old names as
deprecated aliases.

Everything goes through the real ``/api/config`` endpoint, the real workspaces
REST API and the real WebSocket chat endpoint, with sub-conversations enabled.
The model is a puppet (``mock_llm.py``) only so the scenario decides which
*name* each tool is called by -- nothing on the path under test is stubbed.

Exits non-zero if any check fails; prints one PASSED/FAILED line each.
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

# role -> (new name, deprecated alias)
NAMES = {
    "discover": ("atlas_discover_subagent_options", "atlas_discover_launch_options"),
    "launch": ("atlas_launch_subagent", "atlas_launch"),
    "runs": ("atlas_get_subagent_runs", "atlas_get_runs"),
    "result": ("atlas_get_subagent_result", "atlas_result"),
    "follow_up": ("atlas_follow_up_subagent", "atlas_follow_up"),
}
NEW = {role: pair[0] for role, pair in NAMES.items()}
OLD = {role: pair[1] for role, pair in NAMES.items()}


def check(ok, label, detail=""):
    global FAILED
    print(("PASSED: " if ok else "FAILED: ") + label, flush=True)
    if not ok:
        FAILED += 1
        if detail != "":
            print("        " + str(detail)[:800], flush=True)


def request(url, user=USER, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"X-User-Email": user, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None


async def run_turn(ws, plan, selected_tools, timeout=90):
    """Send one agent-mode turn; return this turn's tool_complete frames."""
    await ws.send(json.dumps({
        "type": "chat",
        "content": f"plan={plan} ws=Research model=scripted-mock",
        "model": "scripted-mock",
        "save_mode": "server",
        "selected_tools": selected_tools,
        "agent_mode": True,
    }))
    tool_results, status = [], None
    # A launched sub-conversation streams its own frames down this socket;
    # bind to this turn's run (the first run_started with no parent).
    my_run = None
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        try:
            m = json.loads(await asyncio.wait_for(ws.recv(), deadline - loop.time()))
        except asyncio.TimeoutError:
            break
        kind = m.get("type")
        run_id = m.get("run_id")
        if kind == "run_started" and my_run is None and not m.get("parent_run_id"):
            my_run = run_id
        elif kind == "tool_approval_request":
            await ws.send(json.dumps({
                "type": "tool_approval_response",
                "tool_call_id": m["tool_call_id"],
                "approved": True,
                "run_id": run_id,
            }))
        elif kind == "tool_complete":
            if my_run is None or run_id in (None, my_run):
                tool_results.append(m)
        elif kind == "run_status":
            run = m.get("run") or {}
            if run.get("run_id") == my_run and run.get("status") in ("completed", "failed"):
                status = run.get("status")
                break
        elif kind == "error":
            status = "error: " + str(m.get("message"))
            break
    return tool_results, status


def text_of(frame):
    value = (frame or {}).get("result")
    if value is None:
        return ""
    return value if isinstance(value, str) else json.dumps(value)


def by_name(results, name):
    matches = [r for r in results if r.get("tool_name") == name]
    return matches[-1] if matches else None


def offered_since_reset():
    _, log = request(MOCK + "/test/log")
    names = set()
    for entry in log or []:
        # Only the parent turns: a child runs under its own workspace's tools.
        if "plan=" in (entry.get("user") or ""):
            names.update(entry.get("tools") or [])
    return names


async def subagent_round_trip(ws, label, called, selected):
    """discover -> launch -> list -> result, calling each tool as `called[...]`."""
    request(MOCK + "/test/reset", method="POST")
    plan = ",".join([called["discover"], called["launch"], called["runs"], called["result"]])
    results, status = await run_turn(ws, plan, selected)
    check(status == "completed", f"[{label}] the turn completes", status)

    discovered = by_name(results, called["discover"])
    check((discovered or {}).get("success") is True,
          f"[{label}] {called['discover']} returns the launch options", text_of(discovered))
    check("Research" in text_of(discovered),
          f"[{label}] discovery reports the saved workspace", text_of(discovered))

    launched = by_name(results, called["launch"])
    check((launched or {}).get("success") is True,
          f"[{label}] {called['launch']} starts a subagent", text_of(launched))
    handle = {}
    try:
        handle = json.loads(text_of(launched).splitlines()[-1])
    except (ValueError, IndexError):
        pass
    check(bool(handle.get("run_id")) and bool(handle.get("conversation_id")),
          f"[{label}] the launch returns the same handle schema (run_id, conversation_id)",
          text_of(launched))
    check(handle.get("workspace") == "Research",
          f"[{label}] the handle names the workspace it runs under", handle)

    listed = by_name(results, called["runs"])
    check((listed or {}).get("success") is True and str(handle.get("run_id")) in text_of(listed),
          f"[{label}] {called['runs']} lists the launched subagent", text_of(listed))

    fetched = by_name(results, called["result"])
    check((fetched or {}).get("success") is True and "result_status" in text_of(fetched),
          f"[{label}] {called['result']} reads the subagent's result", text_of(fetched))

    offered = offered_since_reset()
    check(set(NEW[r] for r in ("discover", "launch", "runs", "result")) <= offered,
          f"[{label}] the model is offered the new names", sorted(offered))
    check(not (set(OLD.values()) & offered),
          f"[{label}] no deprecated name is advertised to the model", sorted(offered))
    return handle


async def main():
    status, body = request(API + "/api/workspaces", method="POST", payload={
        "name": "Research",
        "description": "PR1141 validation workspace",
        "config": {"selected_tools": ["atlas_sleep"], "selected_data_sources": []},
    })
    check(status == 200, "a saved workspace can be created over the REST API", status)

    # ------------------------------------------------ what the UI is told
    status, body = request(API + "/api/config")
    atlas = {}
    for server in (body or {}).get("tools") or []:
        if isinstance(server, dict) and server.get("server") == "atlas":
            atlas = server
    listed = [f"atlas_{name}" for name in atlas.get("tools") or []]
    check(set(NEW.values()) <= set(listed),
          "/api/config lists every subagent tool under its new name", listed)
    check(not (set(OLD.values()) & set(listed)),
          "/api/config lists no deprecated name", listed)
    builtin = set(((body or {}).get("features") or {}).get("atlas_builtin_tools") or [])
    check(set(NEW.values()) <= builtin and not (set(OLD.values()) & builtin),
          "features.atlas_builtin_tools carries the new names only", sorted(builtin))
    launch_detail = next(
        (t for t in atlas.get("tools_detailed") or [] if t.get("name") == "launch_subagent"), {}
    )
    check("subagent" in (launch_detail.get("description") or "").lower(),
          "the launch tool is described as launching a subagent",
          launch_detail.get("description"))
    check(
        list((launch_detail.get("inputSchema") or {}).get("properties") or {})
        == ["workspace", "model", "prompt"],
        "the launch parameters are unchanged (workspace, model, prompt)",
        launch_detail.get("inputSchema"),
    )

    async with websockets.connect(WS, additional_headers={"X-User-Email": USER}) as ws:
        # 1. The new names, end to end.
        await subagent_round_trip(ws, "new names", NEW, list(NEW.values()))

        # 2. An existing workflow: an old selection, and a model that still
        #    calls the old names (a saved prompt, a replayed conversation).
        await subagent_round_trip(ws, "deprecated aliases", OLD, list(OLD.values()))

        # 3. A new selection, but the model was told the old name by a prompt.
        mixed = dict(NEW, launch=OLD["launch"], result=OLD["result"])
        await subagent_round_trip(ws, "new selection, old call", mixed, list(NEW.values()))

    sys.exit(1 if FAILED else 0)


asyncio.run(main())
