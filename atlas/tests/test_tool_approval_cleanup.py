"""Pending tool-approval requests must not leak when a run is cancelled (#884).

The approval gate parks the turn on a future until the user answers. Cleanup
used to live on the success path and in the `asyncio.TimeoutError` branch only,
and a cancellation -- a user pressing Stop, or the run wall-clock sweeper
reaping a detached run -- unwinds through neither. The request, and the
`filtered_args` it holds, then stayed in the manager for the life of the
process. With `TOOL_APPROVAL_TIMEOUT_SECONDS=0` (an indefinite wait, which is
what lets an approval survive a closed browser) cancellation is the *only* way
out, so it was the only path that could ever clean up and it did not.
"""

import asyncio
from types import SimpleNamespace

import pytest

from atlas.application.chat.approval_manager import get_approval_manager
from atlas.application.chat.utilities.tool_executor import execute_single_tool

TOOL_NAME = "approval_gated_tool"
TOOL_CALL_ID = "call_leak_1"


class _FakeToolManager:
    """Minimal tool manager: the gate is reached before execution matters."""

    def get_tool_schema(self, tool_name):
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
        }

    def get_tools_schema(self, *args, **kwargs):
        return []

    async def execute_tool(self, tool_call_obj, context=None):
        return SimpleNamespace(content="unreachable", success=True, error=None)


def _tool_call():
    return SimpleNamespace(
        id=TOOL_CALL_ID,
        function=SimpleNamespace(name=TOOL_NAME, arguments='{"value": "x"}'),
    )


@pytest.fixture
def approval_manager():
    manager = get_approval_manager()
    manager.cleanup_request(TOOL_CALL_ID)
    yield manager
    manager.cleanup_request(TOOL_CALL_ID)


@pytest.fixture
def config_manager():
    """A config whose every tool requires approval and never times out.

    Timeout 0 is the indefinite wait, so the test exercises the branch where
    cancellation is the only exit.
    """
    return SimpleNamespace(
        app_settings=SimpleNamespace(
            tool_approval_timeout_seconds=0,
            tool_approval_required_default=True,
        ),
        get_tool_approvals_config=lambda: {
            "default_requires_approval": True,
            "tools": {},
        },
    )


@pytest.mark.asyncio
async def test_cancelling_the_approval_wait_cleans_up_the_request(
    approval_manager, config_manager, monkeypatch
):
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.requires_approval",
        lambda tool_name, cm: (True, False, True),
    )
    monkeypatch.setattr(
        "atlas.application.chat.utilities.tool_executor.resolve_approval_timeout",
        lambda: 0,
    )

    requested = asyncio.Event()

    async def update_callback(message):
        if message.get("type") == "tool_approval_request":
            requested.set()

    task = asyncio.create_task(
        execute_single_tool(
            _tool_call(),
            {"user_email": "owner@example.com", "session_id": "s1"},
            _FakeToolManager(),
            update_callback=update_callback,
            config_manager=config_manager,
        )
    )

    await asyncio.wait_for(requested.wait(), timeout=5)
    # The gate is open and waiting: the request is registered.
    assert TOOL_CALL_ID in approval_manager.get_pending_requests()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The regression: this used to still hold the request (and its arguments).
    assert TOOL_CALL_ID not in approval_manager.get_pending_requests()
