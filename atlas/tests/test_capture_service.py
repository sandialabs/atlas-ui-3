"""Tests for the fine-tune capture service and context (issue #622).

Covers the two-flag gating, per-turn context activation, derivation of the
chosen/rejected trajectories from accumulated LLM I/O, tool-call normalization,
and ContextVar isolation between turns.
"""

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from atlas.application.chat.capture.capture_context import (
    capture_turn,
    current_capture_context,
    normalize_tool_calls,
    record_llm_call,
)
from atlas.application.chat.capture.capture_service import CaptureService
from atlas.application.chat.capture.capture_store import CaptureStore


def _config(system_enabled=True):
    return SimpleNamespace(
        app_settings=SimpleNamespace(
            feature_finetune_capture_enabled=system_enabled,
            runtime_capture_dir=None,
            capture_user_salt="svc-test-salt",
            admin_group="admin",
        )
    )


@pytest.fixture
def service():
    with tempfile.TemporaryDirectory() as tmp:
        store = CaptureStore(Path(tmp), user_salt="svc-test-salt")
        yield CaptureService(_config(), store=store)


def _fake_tool_call(name, arguments='{}'):
    return SimpleNamespace(
        id="1", type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class TestNormalizeToolCalls:
    def test_namespace_arguments_decoded_to_dict(self):
        out = normalize_tool_calls([_fake_tool_call("search", '{"q": "x"}')])
        assert out == [{"name": "search", "arguments": {"q": "x"}}]

    def test_dict_form_supported(self):
        raw = [{"function": {"name": "fetch", "arguments": '{"u": 1}'}}]
        assert normalize_tool_calls(raw) == [{"name": "fetch", "arguments": {"u": 1}}]

    def test_invalid_json_arguments_preserved_as_string(self):
        out = normalize_tool_calls([_fake_tool_call("x", "not-json")])
        assert out == [{"name": "x", "arguments": "not-json"}]

    def test_empty_input(self):
        assert normalize_tool_calls(None) == []
        assert normalize_tool_calls([]) == []


class TestGating:
    def test_disabled_system_blocks_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CaptureStore(Path(tmp), user_salt="s")
            svc = CaptureService(_config(system_enabled=False), store=store)
            store.set_consent("a@b.c", True)
            assert svc.is_enabled_for("a@b.c") is False

    def test_requires_user_consent(self, service):
        assert service.is_enabled_for("a@b.c") is False
        service.set_consent("a@b.c", True)
        assert service.is_enabled_for("a@b.c") is True

    def test_consent_state_shape(self, service):
        state = service.consent_state("a@b.c")
        assert state["system_enabled"] is True
        assert state["user_enabled"] is False
        assert "current_consent_version" in state

    def test_implied_consent_bypasses_user_record(self, service):
        # CLI path: system flag on, no stored consent -> still enabled when
        # consent is implied (the operator who set the flag is the consenter).
        assert service.is_enabled_for("a@b.c") is False
        assert service.is_enabled_for("a@b.c", require_consent=False) is True

    def test_implied_consent_still_gated_by_system_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CaptureStore(Path(tmp), user_salt="s")
            svc = CaptureService(_config(system_enabled=False), store=store)
            # Even implied consent cannot override the system flag being off.
            assert svc.is_enabled_for("a@b.c", require_consent=False) is False

    def test_consent_source_recorded_in_context(self, service):
        default_ctx = service.build_context(
            user_email="a@b.c", conversation_id="c", model="m", temperature=0.5
        )
        assert default_ctx.consent["source"] == "user_optin"
        cli_ctx = service.build_context(
            user_email="a@b.c", conversation_id="c", model="m", temperature=0.5,
            consent_source="system_flag",
        )
        assert cli_ctx.consent["source"] == "system_flag"


class TestContextVarIsolation:
    def test_context_not_leaked_outside_block(self, service):
        ctx = service.build_context(
            user_email="a@b.c", conversation_id="c", model="m", temperature=0.5
        )
        assert current_capture_context() is None
        with capture_turn(ctx):
            assert current_capture_context() is ctx
        assert current_capture_context() is None

    def test_record_outside_context_is_noop(self):
        # Must not raise when no context is active.
        record_llm_call([{"role": "user", "content": "hi"}], [], "", [])


class TestRecording:
    def _run_turn(self, service, correction=None):
        ctx = service.build_context(
            user_email="a@b.c", conversation_id="c", model="m",
            temperature=0.5, correction=correction,
        )
        messages = [
            {"role": "system", "content": "you are atlas"},
            {"role": "user", "content": "what's the weather"},
        ]
        tools = [{"type": "function", "function": {
            "name": "search", "description": "web search", "parameters": {"type": "object"}}}]
        with capture_turn(ctx):
            record_llm_call(messages, tools, "", [_fake_tool_call("search", '{"q": "weather"}')])
        service.finish_turn(ctx)
        return ctx

    def test_normal_turn_records_sft_example(self, service):
        service.set_consent("a@b.c", True)
        self._run_turn(service)
        records = list(service.iter_records())
        assert len(records) == 1
        rec = records[0]
        assert rec["kind"] == "turn"
        assert rec["rejected"] is None
        assert rec["chosen"]["tool_calls"][0]["name"] == "search"
        assert rec["context"]["system_prompt"] == "you are atlas"
        # System prompt must not also appear in the prefix.
        assert all(m["role"] != "system" for m in rec["context"]["messages_prefix"])
        assert rec["context"]["available_tools"][0]["name"] == "search"

    def test_rollback_records_preference_pair(self, service):
        service.set_consent("a@b.c", True)
        correction = {
            "rejected_turn_id": "old-turn",
            "note": "should have searched",
            "rejected": {"assistant_message": "",
                         "tool_calls": [{"name": "fetch", "arguments": {"url": "x"}}]},
        }
        self._run_turn(service, correction=correction)
        rec = list(service.iter_records())[0]
        assert rec["kind"] == "pair"
        assert rec["parent_turn_id"] == "old-turn"
        assert rec["label"]["source"] == "rollback"
        assert rec["rejected"]["tool_calls"][0]["name"] == "fetch"
        assert rec["chosen"]["tool_calls"][0]["name"] == "search"

    def test_turn_without_llm_calls_is_not_written(self, service):
        service.set_consent("a@b.c", True)
        ctx = service.build_context(
            user_email="a@b.c", conversation_id="c", model="m", temperature=0.5
        )
        with capture_turn(ctx):
            pass  # plain chat: no tool-capable LLM call recorded
        assert service.finish_turn(ctx) is None
        assert list(service.iter_records()) == []
