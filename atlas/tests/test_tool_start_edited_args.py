"""Regression: the ``tool_start`` event must carry the *executed* arguments,
not the pre-approval request, when the user edits arguments in the approval
dialog.

The approval-edit path recomputes ``filtered_args`` from the user's edited
input but previously left ``display_args`` (the UI-facing copy) stale, so the
``tool_start`` frame still carried the original request. UI elements that
target the executed args -- e.g. the ``atlas_agent_sleep`` progress clock added
in #838 -- would then show a total that no longer matched what the backend
actually ran. The hook-rewrite paths already refreshed ``display_args``; this
makes the user-edit path match them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.domain.messages.models import ToolResult


@pytest.mark.asyncio
async def test_tool_start_carries_edited_args_after_approval_edit():
    from atlas.application.chat.utilities import tool_executor as te
    from atlas.application.chat.utilities.tool_executor import execute_single_tool

    tool_call = MagicMock()
    tool_call.id = "call_edit"
    tool_call.function.name = "atlas_agent_sleep"
    tool_call.function.arguments = '{"seconds": 1200, "reason": "wait"}'

    tool_manager = MagicMock()
    tool_manager.get_tools_schema.return_value = [
        {
            "function": {
                "name": "atlas_agent_sleep",
                "parameters": {
                    "properties": {
                        "seconds": {},
                        "reason": {},
                    }
                },
            }
        }
    ]
    tool_manager.get_server_for_tool.return_value = "atlas_agent"
    tool_manager.execute_tool = AsyncMock(
        return_value=ToolResult(
            tool_call_id="call_edit",
            content="Slept for 5 seconds.",
            success=True,
        )
    )

    approval_manager = MagicMock()
    approval_request = MagicMock()
    approval_request.wait_for_response = AsyncMock(
        return_value={
            "approved": True,
            # User shortened the wait from 1200s to 5s in the approval dialog.
            "arguments": {"seconds": 5, "reason": "wait"},
        }
    )
    approval_manager.create_approval_request.return_value = approval_request
    approval_manager.cleanup_request = MagicMock()

    def _requires_approval(name, cfg):
        return (True, True, False)

    original_get_am = te.get_approval_manager
    original_requires_approval = te.requires_approval
    te.get_approval_manager = lambda: approval_manager
    te.requires_approval = _requires_approval

    captured: list[dict] = []

    async def _capture(payload):
        captured.append(payload)

    try:
        cfg = MagicMock()
        await execute_single_tool(
            tool_call=tool_call,
            session_context={"user_email": "u@x.com"},
            tool_manager=tool_manager,
            update_callback=_capture,
            config_manager=cfg,
        )
    finally:
        te.get_approval_manager = original_get_am
        te.requires_approval = original_requires_approval

    tool_start = next(p for p in captured if p.get("type") == "tool_start")
    assert tool_start["arguments"]["seconds"] == 5
    assert tool_start["tool_name"] == "atlas_agent_sleep"

    executed = tool_manager.execute_tool.await_args.args[0]
    assert executed.arguments == {"seconds": 5, "reason": "wait"}
