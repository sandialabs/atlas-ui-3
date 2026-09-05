"""Regression tests for tool approval audit evidence."""

import ast
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.application.chat.approval_manager import ToolApprovalManager, ToolApprovalRequest
from atlas.domain.messages.models import ToolResult
from atlas.modules.mcp_tools.tool_audit_log import hash_arguments


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    path = tmp_path / "tool-audit.jsonl"
    monkeypatch.setenv("TOOL_CALL_AUDIT_PATH", str(path))
    return path


def _read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_approval_manager_does_not_import_tool_executor():
    from atlas.application.chat import approval_manager as am

    tree = ast.parse(Path(am.__file__).read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not any("tool_executor" in name for name in imported)


@pytest.mark.asyncio
async def test_omitted_display_arguments_are_not_reconstructed():
    manager = ToolApprovalManager()
    internal = {"filename": "https://files.example/report.pdf?token=secret"}
    request = manager.create_approval_request(
        "call-legacy",
        "file_tool",
        internal,
        user_email="alice@example.com",
    )
    assert request.display_arguments == internal
    assert request.display_arguments is internal


@pytest.mark.asyncio
async def test_non_mapping_arguments_do_not_break_request_creation():
    manager = ToolApprovalManager()
    request = manager.create_approval_request(
        "call-list",
        "file_tool",
        ["not", "a", "mapping"],  # type: ignore[arg-type]
        user_email="alice@example.com",
    )
    assert request.arguments == ["not", "a", "mapping"]
    assert manager.handle_approval_response(
        "call-list",
        approved=True,
        user_email="alice@example.com",
    ) is True
    assert request.future.result()["approved"] is True


@pytest.mark.asyncio
async def test_approved_decision_records_hash_without_raw_arguments(audit_path):
    manager = ToolApprovalManager()
    original = {"command": "cat /sensitive/input"}
    edited = {"command": "cat /safe/input"}
    manager.create_approval_request(
        "call-1",
        "shell_bash",
        original,
        user_email="alice@example.com",
    )

    assert manager.handle_approval_response(
        "call-1",
        approved=True,
        arguments=edited,
        user_email="alice@example.com",
    ) is True

    records = _read_records(audit_path)
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "tool_approval_decision"
    assert record["decision"] == "approved"
    assert record["decision_origin"] == "approval_response"
    assert record["arguments_edited"] is True
    assert record["decision_args_sha256"] == hash_arguments(edited)
    assert record["user"] == "alice@example.com"
    assert record["request_owner"] == "alice@example.com"

    raw_line = audit_path.read_text(encoding="utf-8")
    assert "/sensitive/input" not in raw_line
    assert "/safe/input" not in raw_line


@pytest.mark.asyncio
async def test_display_baseline_is_not_reported_as_an_edit(audit_path):
    manager = ToolApprovalManager()
    internal = {"filename": "https://files.example/report.pdf?token=secret"}
    display = {"filename": "report.pdf"}
    manager.create_approval_request(
        "call-display",
        "file_tool",
        internal,
        user_email="alice@example.com",
        display_arguments=display,
    )

    assert manager.handle_approval_response(
        "call-display",
        approved=True,
        arguments=display,
        user_email="alice@example.com",
    ) is True

    record = _read_records(audit_path)[0]
    assert record["arguments_edited"] is False
    assert record["decision_args_sha256"] == hash_arguments(display)
    assert "token=secret" not in audit_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_allow_edit_false_ignores_client_arguments_for_execution_and_audit(audit_path):
    manager = ToolApprovalManager()
    request = manager.create_approval_request(
        "call-no-edit",
        "fixed_tool",
        {"value": 1},
        allow_edit=False,
        user_email="alice@example.com",
        display_arguments={"value": 1},
    )

    assert manager.handle_approval_response(
        "call-no-edit",
        approved=True,
        arguments={"value": 999},
        user_email="alice@example.com",
    ) is True

    record = _read_records(audit_path)[0]
    assert record["arguments_edited"] is False
    assert record["decision_args_sha256"] == hash_arguments({"value": 1})
    assert request.future.result()["arguments"] == {"value": 1}


@pytest.mark.asyncio
async def test_empty_dict_is_a_real_edit(audit_path):
    manager = ToolApprovalManager()
    request = manager.create_approval_request(
        "call-empty",
        "editable_tool",
        {"value": 1},
        user_email="alice@example.com",
        display_arguments={"value": 1},
    )

    assert manager.handle_approval_response(
        "call-empty",
        approved=True,
        arguments={},
        user_email="alice@example.com",
    ) is True

    record = _read_records(audit_path)[0]
    assert record["arguments_edited"] is True
    assert record["decision_args_sha256"] == hash_arguments({})
    assert request.future.result()["arguments"] == {}


@pytest.mark.asyncio
async def test_rejected_decision_records_reason_presence_not_reason(audit_path):
    manager = ToolApprovalManager()
    manager.create_approval_request(
        "call-2",
        "dangerous_tool",
        {"target": "prod"},
        user_email="alice@example.com",
    )

    manager.handle_approval_response(
        "call-2",
        approved=False,
        reason="contains sensitive justification",
        user_email="alice@example.com",
    )

    record = _read_records(audit_path)[0]
    assert record["decision"] == "rejected"
    assert record["reason_present"] is True
    assert "contains sensitive justification" not in audit_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_timeout_records_audit_decision(audit_path):
    request = ToolApprovalRequest(
        "call-3",
        "slow_tool",
        {"value": 1},
        user_email="alice@example.com",
        display_arguments={"value": 1},
    )

    with pytest.raises(asyncio.TimeoutError):
        await request.wait_for_response(timeout=0.01)

    record = _read_records(audit_path)[0]
    assert record["decision"] == "timeout"
    assert record["decision_origin"] == "approval_timeout"
    assert record["tool_call_id"] == "call-3"
    assert record["decision_args_sha256"] == hash_arguments({"value": 1})
    assert record["request_owner"] == "alice@example.com"


@pytest.mark.asyncio
async def test_unserializable_timeout_payload_does_not_replace_timeout(audit_path):
    circular = {}
    circular["self"] = circular
    request = ToolApprovalRequest(
        "call-timeout-bad",
        "slow_tool",
        circular,
        user_email="alice@example.com",
        display_arguments=circular,
    )

    with pytest.raises(asyncio.TimeoutError):
        await request.wait_for_response(timeout=0.01)

    assert not audit_path.exists()


@pytest.mark.asyncio
async def test_cross_user_response_is_audited_and_still_rejected(audit_path):
    manager = ToolApprovalManager()
    manager.create_approval_request(
        "call-4",
        "protected_tool",
        {"action": "write"},
        user_email="alice@example.com",
    )

    assert manager.handle_approval_response(
        "call-4",
        approved=True,
        user_email="mallory@example.com",
    ) is False

    record = _read_records(audit_path)[0]
    assert record["decision"] == "invalid_responder"
    assert record["decision_origin"] == "ownership_check"
    assert record["user"] == "mallory@example.com"
    assert record["request_owner"] == "alice@example.com"
    raw = audit_path.read_text(encoding="utf-8")
    assert "action" not in raw
    assert '"write"' not in raw


@pytest.mark.asyncio
async def test_unserializable_client_edit_does_not_break_approval(audit_path):
    manager = ToolApprovalManager()
    request = manager.create_approval_request(
        "call-bad-edit",
        "editable_tool",
        {"value": 1},
        user_email="alice@example.com",
        display_arguments={"value": 1},
    )
    circular = {}
    circular["self"] = circular

    assert manager.handle_approval_response(
        "call-bad-edit",
        approved=True,
        arguments=circular,
        user_email="alice@example.com",
    ) is True
    assert request.future.done()
    assert request.future.result()["approved"] is True
    assert not audit_path.exists()


@pytest.mark.asyncio
async def test_duplicate_response_does_not_append_contradictory_decision(audit_path):
    manager = ToolApprovalManager()
    request = manager.create_approval_request(
        "call-5",
        "write_tool",
        {"target": "one"},
        user_email="alice@example.com",
    )

    assert manager.handle_approval_response(
        "call-5",
        approved=True,
        user_email="alice@example.com",
    ) is True
    assert manager.handle_approval_response(
        "call-5",
        approved=False,
        reason="late duplicate",
        user_email="alice@example.com",
    ) is True

    assert request.future.result()["approved"] is True
    records = _read_records(audit_path)
    assert len(records) == 1
    assert records[0]["decision"] == "approved"
    assert "late duplicate" not in audit_path.read_text(encoding="utf-8")


def _file_tool(tool_call_id: str, raw_args: str):
    tool_call = MagicMock()
    tool_call.id = tool_call_id
    tool_call.function.name = "file_tool"
    tool_call.function.arguments = raw_args

    tool_manager = MagicMock()
    tool_manager.get_tools_schema.return_value = [
        {
            "function": {
                "name": "file_tool",
                "parameters": {
                    "properties": {
                        "filename": {},
                        "value": {},
                        "_atlas_user": {},
                    }
                },
            }
        }
    ]
    tool_manager.get_server_for_tool.return_value = "files"
    tool_manager.execute_tool = AsyncMock(
        return_value=ToolResult(
            tool_call_id=tool_call_id,
            content="ok",
            success=True,
        )
    )
    return tool_call, tool_manager


@pytest.mark.asyncio
async def test_executor_empty_edit_is_executed_and_matches_presented_call(audit_path):
    """Empty-dict approval must change what actually runs, while the UI
    emission and ToolApprovalRequest share one PresentedCall snapshot."""
    from atlas.application.chat.utilities import tool_executor as te
    from atlas.application.chat.utilities.tool_executor import execute_single_tool

    manager = ToolApprovalManager()
    tool_call, tool_manager = _file_tool(
        "call-empty-exec",
        '{"filename": "https://files.example/report.pdf?token=secret", "value": 1}',
    )
    captured: list[dict] = []
    snapshots: dict[str, dict] = {}

    async def _capture(payload):
        captured.append(payload)

    async def _approve_when_ready():
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2
        while loop.time() < deadline:
            pending = manager.get_pending_requests()
            if tool_call.id in pending:
                request = pending[tool_call.id]
                frontend = next(p for p in captured if p.get("type") == "tool_approval_request")
                snapshots["presented"] = dict(request.display_arguments)
                snapshots["internal"] = dict(request.arguments)
                snapshots["frontend"] = dict(frontend["arguments"])
                assert request.display_arguments == frontend["arguments"]
                assert manager.handle_approval_response(
                    tool_call.id,
                    approved=True,
                    arguments={},
                    user_email="alice@example.com",
                ) is True
                return
            await asyncio.sleep(0.01)
        raise AssertionError("timed out waiting for approval request")

    original_get_am = te.get_approval_manager
    original_requires_approval = te.requires_approval
    te.get_approval_manager = lambda: manager
    te.requires_approval = lambda name, cfg: (True, True, False)
    execute_task = asyncio.create_task(
        execute_single_tool(
            tool_call=tool_call,
            session_context={"user_email": "alice@example.com"},
            tool_manager=tool_manager,
            update_callback=_capture,
            config_manager=MagicMock(),
        )
    )
    approve_task = asyncio.create_task(_approve_when_ready())
    try:
        await approve_task
        result = await asyncio.wait_for(execute_task, timeout=2)
    finally:
        if not execute_task.done():
            execute_task.cancel()
        await asyncio.gather(execute_task, return_exceptions=True)
        te.get_approval_manager = original_get_am
        te.requires_approval = original_requires_approval

    assert result.success is True
    assert snapshots["frontend"] == snapshots["presented"]
    assert snapshots["presented"]["filename"] == "report.pdf"
    assert snapshots["presented"]["value"] == 1
    assert snapshots["presented"]["_atlas_user"] == "alice@example.com"
    assert "token=secret" in snapshots["internal"]["filename"]

    executed = tool_manager.execute_tool.await_args.args[0]
    assert executed.arguments == {"_atlas_user": "alice@example.com"}
    assert executed.arguments != snapshots["internal"]

    records = _read_records(audit_path)
    assert len(records) == 1
    assert records[0]["arguments_edited"] is True
    assert records[0]["decision_args_sha256"] == hash_arguments({})
    assert "token=secret" not in audit_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_executor_allow_edit_false_ignores_client_replacement_arguments(audit_path):
    from atlas.application.chat.utilities import tool_executor as te
    from atlas.application.chat.utilities.tool_executor import execute_single_tool

    manager = ToolApprovalManager()
    tool_call, tool_manager = _file_tool(
        "call-no-edit-exec",
        '{"value": 1}',
    )

    async def _capture(_payload):
        return None

    async def _approve_when_ready():
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2
        while loop.time() < deadline:
            if tool_call.id in manager.get_pending_requests():
                assert manager.handle_approval_response(
                    tool_call.id,
                    approved=True,
                    arguments={"value": 999},
                    user_email="alice@example.com",
                ) is True
                return
            await asyncio.sleep(0.01)
        raise AssertionError("timed out waiting for approval request")

    original_get_am = te.get_approval_manager
    original_requires_approval = te.requires_approval
    te.get_approval_manager = lambda: manager
    te.requires_approval = lambda name, cfg: (True, False, False)
    execute_task = asyncio.create_task(
        execute_single_tool(
            tool_call=tool_call,
            session_context={"user_email": "alice@example.com"},
            tool_manager=tool_manager,
            update_callback=_capture,
            config_manager=MagicMock(),
        )
    )
    approve_task = asyncio.create_task(_approve_when_ready())
    try:
        await approve_task
        result = await asyncio.wait_for(execute_task, timeout=2)
    finally:
        if not execute_task.done():
            execute_task.cancel()
        await asyncio.gather(execute_task, return_exceptions=True)
        te.get_approval_manager = original_get_am
        te.requires_approval = original_requires_approval

    assert result.success is True
    executed = tool_manager.execute_tool.await_args.args[0]
    assert executed.arguments["value"] == 1
    assert executed.arguments["_atlas_user"] == "alice@example.com"

    record = _read_records(audit_path)[0]
    assert record["arguments_edited"] is False
    assert record["decision_args_sha256"] == hash_arguments(
        {"value": 1, "_atlas_user": "alice@example.com"}
    )