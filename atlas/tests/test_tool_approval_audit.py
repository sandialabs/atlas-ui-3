"""Regression tests for tool approval audit evidence."""

import asyncio
import json

import pytest

from atlas.application.chat.approval_manager import ToolApprovalManager, ToolApprovalRequest
from atlas.modules.mcp_tools.tool_audit_log import hash_arguments


@pytest.fixture
def audit_path(tmp_path, monkeypatch):
    path = tmp_path / "tool-audit.jsonl"
    monkeypatch.setenv("TOOL_CALL_AUDIT_PATH", str(path))
    return path


def _read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_approved_decision_records_hash_without_raw_arguments(audit_path):
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

    raw_line = audit_path.read_text(encoding="utf-8")
    assert "/sensitive/input" not in raw_line
    assert "/safe/input" not in raw_line


def test_display_baseline_is_not_reported_as_an_edit(audit_path):
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


def test_allow_edit_false_ignores_client_arguments_for_execution_and_audit(audit_path):
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


def test_empty_dict_is_a_real_edit(audit_path):
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


def test_rejected_decision_records_reason_presence_not_reason(audit_path):
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


def test_cross_user_response_is_audited_and_still_rejected(audit_path):
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


def test_unserializable_client_edit_does_not_break_approval(audit_path):
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


def test_duplicate_response_does_not_append_contradictory_decision(audit_path):
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
