"""Unit tests for the standalone tool approval audit sink."""

import json

from atlas.modules.mcp_tools.tool_audit_log import (
    hash_arguments,
    record_tool_decision,
    reset_path_cache_for_tests,
)


def test_record_tool_decision_is_append_only_and_redacts_payload(tmp_path, monkeypatch):
    path = tmp_path / "tool-audit.jsonl"
    monkeypatch.setenv("TOOL_CALL_AUDIT_PATH", str(path))
    reset_path_cache_for_tests()

    args = {"token": "secret-value", "nested": {"count": 2}}
    record_tool_decision(
        user_email="alice@example.com",
        tool_call_id="call-1",
        tool_name="example_tool",
        arguments=args,
        decision="approved",
    )
    record_tool_decision(
        user_email="alice@example.com",
        tool_call_id="call-2",
        tool_name="example_tool",
        arguments={"token": "different-secret"},
        decision="rejected",
        reason_present=True,
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["decision_args_sha256"] == hash_arguments(args)
    assert first["decision"] == "approved"
    assert second["decision"] == "rejected"
    assert second["reason_present"] is True

    raw = path.read_text(encoding="utf-8")
    assert "secret-value" not in raw
    assert "different-secret" not in raw

    reset_path_cache_for_tests()
