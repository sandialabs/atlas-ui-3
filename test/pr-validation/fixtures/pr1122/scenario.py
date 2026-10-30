"""Acceptance scenario for issue #1122.

Two stores share one Redis. A mid-step write on one is the read on the other.
An approval timeout settles the process-local run and the shared projection.
No server is required: the projection is the feature a replica serves.
"""

import asyncio
import json
import os
import sys

# The alignment mirror reads this at import. This scenario does not open a port.
os.environ.setdefault("ATLAS_PORT", "0")

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _prefer_this_checkout():
    """Point an editable install at this tree when it is mapped somewhere else.

    A worktree shares the primary checkout's virtualenv. That env's finder
    loads ``atlas`` from the primary tree, ahead of ``sys.path``.
    """
    here = os.path.join(ROOT, "atlas")
    for finder in sys.meta_path:
        module = sys.modules.get(getattr(finder, "__module__", ""))
        mapping = getattr(module, "MAPPING", None)
        if not isinstance(mapping, dict) or "atlas" not in mapping:
            continue
        mapped = mapping["atlas"]
        if os.path.abspath(mapped) == os.path.abspath(here):
            return
        mapping["atlas"] = here
        namespaces = getattr(module, "NAMESPACES", None)
        if isinstance(namespaces, dict):
            for name, paths in list(namespaces.items()):
                namespaces[name] = [
                    os.path.join(here, os.path.relpath(path, mapped)) if str(path).startswith(mapped) else path
                    for path in paths
                ]
        return


_prefer_this_checkout()

import fakeredis

from atlas.application.chat.runs.execution_state import (
    RedisExecutionStore,
    invocation_id,
    public_projection,
    reset_execution_store,
    set_execution_store,
)
from atlas.application.chat.runs.registry import RunRegistry, RunStatus
from atlas.infrastructure.redis import RedisClientManager, RedisSettings

FAILED = 0
EMAIL = "owner-1122@example.com"
CONV = "conv-pr1122"


def check(ok, label):
    global FAILED
    print(("PASSED: " if ok else "FAILED: ") + label, flush=True)
    if not ok:
        FAILED += 1


def _store(server):
    manager = RedisClientManager(
        RedisSettings(url="redis://fake/0"),
        client=fakeredis.FakeAsyncRedis(server=server),
    )
    return RedisExecutionStore(manager)


def _event(index, **overrides):
    event = {
        "type": "tool_start",
        "run_id": "run-pr1122",
        "conversation_id": CONV,
        "user_email": EMAIL,
        "step": 0,
        "tool_call_id": f"call_{index}",
        "tool_name": f"tool_{index}",
        "invocation_id": invocation_id("run-pr1122", 0, f"call_{index}"),
    }
    event.update(overrides)
    return event


async def _parallel_replicas():
    server = fakeredis.FakeServer()
    first = _store(server)
    second = _store(server)
    await asyncio.gather(*(first.apply_event(_event(i)) for i in range(5)))
    await asyncio.gather(*(
        second.apply_event(_event(i, type="tool_complete", success=True, result="ok"))
        for i in range(5, 10)
    ))
    left = public_projection(await first.get(CONV, EMAIL))
    right = public_projection(await second.get(CONV, EMAIL))
    check(left["revision"] == right["revision"] == 10, "both replicas see revision 10")
    ids = [row["tool_call_id"] for row in right["invocations"]]
    check(ids == [f"call_{i}" for i in range(10)], "mid-step fetch lists all 10 invocations")
    check(right["invocations"][0]["status"] == "in_progress", "an unfinished tool stays in progress")
    check(right["invocations"][7]["status"] == "completed", "a finished sibling is completed")
    check(await first.get(CONV, "other@example.com") is None, "another user cannot read the projection")


async def _timeout_settles():
    from types import SimpleNamespace

    from atlas.application.chat.runs.context import clear_current_run, set_current_run
    from atlas.application.chat.utilities import tool_executor

    store = _store(fakeredis.FakeServer())
    set_execution_store(store)
    registry = RunRegistry()
    record = registry.start(conversation_id=CONV, user_email=EMAIL)
    registry.set_status(record.run_id, RunStatus.RUNNING)
    frames = []

    async def update_callback(message):
        frames.append(message)
        registry.note_event(record.run_id, message)

    tool_executor.requires_approval = lambda tool_name, cm: (True, True, False)
    tool_executor.resolve_approval_timeout = lambda: 0.05
    set_current_run(record.run_id, CONV)
    try:
        result = await tool_executor.execute_single_tool(
            SimpleNamespace(
                id="call_0",
                function=SimpleNamespace(name="approval_gated_tool", arguments='{"value": "x"}'),
            ),
            {
                "user_email": EMAIL,
                "conversation_id": CONV,
                "session_id": "s-pr1122",
                "agent_step": 0,
                "run_id": record.run_id,
            },
            SimpleNamespace(
                get_tool_schema=lambda name: {"type": "object", "properties": {}},
                get_tools_schema=lambda *args, **kwargs: [],
                execute_tool=None,
            ),
            update_callback=update_callback,
            config_manager=SimpleNamespace(
                app_settings=SimpleNamespace(tool_approval_timeout_seconds=1),
                get_tool_approvals_config=lambda: {"default_requires_approval": True, "tools": {}},
            ),
        )
    finally:
        clear_current_run()
        reset_execution_store()

    check(result.success is False and result.error == "Approval timeout", "timeout returns the approval-timeout result")
    check(
        any(frame.get("type") == "tool_error" and frame.get("status") == "expired" for frame in frames),
        "timeout emits an expired settle frame",
    )
    check(registry.get(record.run_id).status == RunStatus.RUNNING, "settled run is no longer waiting for input")
    projection = public_projection(await store.get(CONV, EMAIL))
    check(projection["pending_requests"] == [], "settled projection has no pending approval")
    check(
        projection["invocations"] and projection["invocations"][0]["status"] == "expired",
        "settled projection keeps an expired row",
    )


def _alignment_failures():
    """Run the #959 mirror without importing it.

    That module starts a live server scenario at import time.
    """
    path = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "pr959", "scenario.py"))
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    start = source.index("PROSE_ROW_TYPES =")
    end = source.index("\nasync def main")
    namespace = {"os": os, "json": json, "__file__": path}
    exec(source[start:end], namespace)
    return namespace["check_shared_alignment_cases"]()


def main():
    print("atlas:", __import__("atlas").__file__, flush=True)
    asyncio.run(_parallel_replicas())
    asyncio.run(_timeout_settles())
    failures = _alignment_failures()
    check(not failures, "shared alignment cases, including invocation identity")
    if failures:
        for line in failures:
            print("  " + line, flush=True)
    return FAILED


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
