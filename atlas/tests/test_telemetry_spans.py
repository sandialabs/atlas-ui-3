"""Tests for the OpenTelemetry audit-trail span emission.

Covers issue #545: every high-value event (chat turn, LLM call, tool call, RAG
query) emits a span with the documented attribute contract, and raw prompts,
tool arguments, tool outputs, and RAG document text never leak into span
attributes.
"""

from __future__ import annotations

import json
from typing import List
from unittest.mock import AsyncMock, MagicMock

import pytest
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from atlas.core import telemetry

# ---------------------------------------------------------------------------
# Tracer fixture — pipes all spans into an in-memory exporter per test.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _module_provider():
    """Install a single TracerProvider for the test module.

    OpenTelemetry refuses to replace a TracerProvider once set, so we install
    one at module scope and attach/detach per-test exporters against it.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": "test"}))
    # set_tracer_provider is a no-op if already set — that's fine, we'll add
    # our processor to whichever provider is active.
    trace.set_tracer_provider(provider)
    active = trace.get_tracer_provider()
    return active


@pytest.fixture
def span_exporter(_module_provider):
    """Yield a fresh in-memory exporter attached for the duration of the test."""
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    _module_provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()
        exporter.clear()


def _by_name(spans: List[ReadableSpan], name: str) -> ReadableSpan:
    matching = [s for s in spans if s.name == name]
    assert matching, f"No span named {name!r} in {[s.name for s in spans]}"
    return matching[-1]


# ---------------------------------------------------------------------------
# Helper-level unit tests
# ---------------------------------------------------------------------------


def test_hash_short_is_deterministic_and_length_16():
    a = telemetry.hash_short("hello world")
    b = telemetry.hash_short("hello world")
    assert a == b
    assert len(a) == 16
    assert all(c in "0123456789abcdef" for c in a)


def test_hash_short_none_and_empty():
    assert telemetry.hash_short(None) is None
    assert telemetry.hash_short("") is None


def test_hash_short_uses_hmac_keying(monkeypatch):
    """hash_short must not equal truncated plain SHA-256 of the same value.

    Regression guard: if someone reverts the HMAC wiring back to plain
    hashlib.sha256, hashes become reversible via a generic rainbow table
    of common emails and short prompts.
    """
    import hashlib

    monkeypatch.setenv("ATLAS_TELEMETRY_HMAC_SECRET", "unit-test-secret-xyz")
    value = "alice@example.com"
    plain = hashlib.sha256(value.encode()).hexdigest()[:16]
    keyed = telemetry.hash_short(value)
    assert keyed != plain, "hash_short should be keyed, not plain SHA-256"


def test_hash_short_depends_on_secret(monkeypatch):
    """Changing the HMAC secret must change the resulting hash."""
    value = "user@example.com"
    monkeypatch.setenv("ATLAS_TELEMETRY_HMAC_SECRET", "secret-a")
    h_a = telemetry.hash_short(value)
    monkeypatch.setenv("ATLAS_TELEMETRY_HMAC_SECRET", "secret-b")
    h_b = telemetry.hash_short(value)
    assert h_a != h_b


def test_safe_label_sanitizes_and_caps():
    assert telemetry.safe_label(None) == ""
    assert telemetry.safe_label("") == ""
    # Control chars stripped
    assert "\n" not in telemetry.safe_label("a\nb")
    assert "\r" not in telemetry.safe_label("a\rb")
    # Hard-capped
    long_label = "x" * (telemetry.LABEL_MAX_CHARS + 50)
    out = telemetry.safe_label(long_label)
    assert len(out) == telemetry.LABEL_MAX_CHARS


def test_coerce_attr_caps_long_strings():
    """Defense-in-depth: an unbounded string must never reach span.set_attribute."""
    huge = "y" * (telemetry.ATTR_STR_HARD_CAP + 1000)
    coerced = telemetry._coerce_attr(huge)
    assert isinstance(coerced, str)
    assert len(coerced) <= telemetry.ATTR_STR_HARD_CAP + 64  # +truncation marker
    # List elements also capped
    coerced_list = telemetry._coerce_attr([huge, "short"])
    assert isinstance(coerced_list, list)
    assert len(coerced_list[0]) <= telemetry.ATTR_STR_HARD_CAP + 64
    assert coerced_list[1] == "short"


def test_sha256_full_is_full_hex_and_stable():
    h = telemetry.sha256_full("abc")
    assert len(h) == 64
    assert h == telemetry.sha256_full("abc")


def test_preview_sanitizes_and_truncates():
    long_text = "secret " + ("x" * 1000)
    out = telemetry.preview(long_text, max_chars=100)
    assert out is not None
    assert "truncated" in out
    # Should not contain the full original beyond preview length
    assert len(out) < len(long_text)


def test_preview_strips_control_chars():
    injected = "line1\nline2\rline3"
    out = telemetry.preview(injected)
    assert "\n" not in out
    assert "\r" not in out


def test_size_bytes_counts_utf8():
    # 3 ASCII chars + one 3-byte UTF-8 char
    assert telemetry.size_bytes("abc€") == 6
    assert telemetry.size_bytes(None) == 0


# ---------------------------------------------------------------------------
# start_span behavior
# ---------------------------------------------------------------------------


def test_start_span_emits_with_attributes(span_exporter):
    with telemetry.start_span("unit.test", {"k": "v", "n": 3}):
        pass
    spans = span_exporter.get_finished_spans()
    span = _by_name(spans, "unit.test")
    assert span.attributes.get("k") == "v"
    assert span.attributes.get("n") == 3


def test_start_span_records_exception(span_exporter):
    raised = False
    try:
        with telemetry.start_span("unit.err"):
            raise ValueError("boom")
    except ValueError:
        raised = True
    assert raised
    spans = span_exporter.get_finished_spans()
    span = _by_name(spans, "unit.err")
    assert span.status.status_code.name == "ERROR"
    assert span.attributes.get("error_type") == "ValueError"


def test_set_attrs_drops_none_values(span_exporter):
    with telemetry.start_span("unit.attrs"):
        telemetry.safe_set_attrs({"present": "yes", "absent": None})
    span = _by_name(span_exporter.get_finished_spans(), "unit.attrs")
    assert span.attributes.get("present") == "yes"
    assert "absent" not in span.attributes


# ---------------------------------------------------------------------------
# tool.call span — via execute_single_tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_call_span_attributes(span_exporter):
    from atlas.application.chat.utilities.tool_executor import execute_single_tool
    from atlas.domain.messages.models import ToolResult

    tool_call = MagicMock()
    tool_call.id = "call_abc"
    tool_call.function.name = "calculator_add"
    tool_call.function.arguments = json.dumps({"a": 1, "b": 2})

    tool_manager = MagicMock()
    tool_manager.get_tools_schema.return_value = [
        {"function": {"name": "calculator_add", "parameters": {"properties": {"a": {}, "b": {}}}}}
    ]
    tool_manager.get_server_for_tool.return_value = "calculator"
    tool_manager.execute_tool = AsyncMock(
        return_value=ToolResult(
            tool_call_id="call_abc",
            content="result=3",
            success=True,
        )
    )

    result = await execute_single_tool(
        tool_call=tool_call,
        session_context={"session_id": "sess_1", "user_email": "u@x.com"},
        tool_manager=tool_manager,
        skip_approval=True,
    )
    assert result.success

    span = _by_name(span_exporter.get_finished_spans(), "tool.call")
    assert span.attributes["tool_name"] == "calculator_add"
    assert span.attributes["tool_source"] == "calculator"
    assert span.attributes["success"] is True
    assert span.attributes["output_size"] == len("result=3")
    # SHA-256 of "result=3" (UTF-8)
    assert len(span.attributes["output_sha256"]) == 64
    # output_preview must be present but must not contain raw tool args
    assert "result=3" in span.attributes["output_preview"]
    # args should be represented by hash only — never raw
    assert "args_hash" in span.attributes
    assert span.attributes["args_hash"] != json.dumps({"a": 1, "b": 2})


@pytest.mark.asyncio
async def test_tool_call_span_marks_failure(span_exporter):
    from atlas.application.chat.utilities.tool_executor import execute_single_tool

    tool_call = MagicMock()
    tool_call.id = "call_err"
    tool_call.function.name = "foo_bar"
    tool_call.function.arguments = "{}"

    tool_manager = MagicMock()
    tool_manager.get_tools_schema.return_value = []
    tool_manager.get_server_for_tool.return_value = "foo"
    tool_manager.execute_tool = AsyncMock(side_effect=RuntimeError("kaboom"))

    result = await execute_single_tool(
        tool_call=tool_call,
        session_context={"session_id": "s"},
        tool_manager=tool_manager,
        skip_approval=True,
    )
    assert not result.success

    span = _by_name(span_exporter.get_finished_spans(), "tool.call")
    assert span.attributes["success"] is False
    assert "kaboom" in span.attributes["error_message"]


@pytest.mark.asyncio
async def test_tool_call_span_sanitizes_and_caps_error_message(span_exporter):
    """Regression: exception strings can embed caller args / URLs / PII.

    The span's error_message attribute must be sanitized (control chars
    removed) and length-capped, never carrying a raw upstream exception.
    """
    from atlas.application.chat.utilities.tool_executor import execute_single_tool
    from atlas.core.telemetry import ERROR_MESSAGE_MAX_CHARS

    tool_call = MagicMock()
    tool_call.id = "call_leaky"
    tool_call.function.name = "db_run_query"
    tool_call.function.arguments = "{}"

    # Simulate a realistic upstream error that embeds user content +
    # control characters + way more data than should hit telemetry.
    leaky = (
        "OperationalError: syntax error near 'SECRET_TOKEN_abc123'\n"
        "at line 42\r\n"
        + ("PAYLOAD" * 500)  # 3500 chars of padding
    )

    tool_manager = MagicMock()
    tool_manager.get_tools_schema.return_value = []
    tool_manager.get_server_for_tool.return_value = "db"
    tool_manager.execute_tool = AsyncMock(side_effect=RuntimeError(leaky))

    result = await execute_single_tool(
        tool_call=tool_call,
        session_context={"session_id": "s"},
        tool_manager=tool_manager,
        skip_approval=True,
    )
    assert not result.success

    span = _by_name(span_exporter.get_finished_spans(), "tool.call")
    emsg = span.attributes["error_message"]
    # Sanitized: no CR/LF/control chars
    assert "\n" not in emsg
    assert "\r" not in emsg
    # Capped: significantly shorter than the raw exception
    assert len(emsg) < len(leaky)
    # Truncation marker present when capped
    assert "truncated" in emsg
    # Cap respected (bounded by configured maximum + truncation suffix)
    assert len(emsg) <= ERROR_MESSAGE_MAX_CHARS + 64


@pytest.mark.asyncio
async def test_tool_source_uses_manager_index_not_string_split(span_exporter):
    """Regression: tool_source must come from the manager's authoritative
    index, not from splitting the tool name on the first underscore.

    Server names can contain underscores (``pptx_generator``) and tool names
    can contain underscores (``create_form_demo``) — string splitting
    produces the wrong answer in both cases.
    """
    from atlas.application.chat.utilities.tool_executor import execute_single_tool
    from atlas.domain.messages.models import ToolResult

    tool_call = MagicMock()
    tool_call.id = "call_pptx"
    tool_call.function.name = "pptx_generator_create_slide"
    tool_call.function.arguments = "{}"

    tool_manager = MagicMock()
    tool_manager.get_tools_schema.return_value = []
    tool_manager.get_server_for_tool.return_value = "pptx_generator"
    tool_manager.execute_tool = AsyncMock(
        return_value=ToolResult(tool_call_id="call_pptx", content="ok", success=True)
    )

    await execute_single_tool(
        tool_call=tool_call,
        session_context={},
        tool_manager=tool_manager,
        skip_approval=True,
    )

    span = _by_name(span_exporter.get_finished_spans(), "tool.call")
    # The naive split-on-first-underscore heuristic would have produced "pptx".
    assert span.attributes["tool_source"] == "pptx_generator"


@pytest.mark.asyncio
async def test_tool_call_span_does_not_leak_edit_note_args(span_exporter):
    """Regression: when arguments are edited, the LLM-facing edit_note that
    ends up in ``result.content`` embeds the executed args. Those args must
    NOT appear in ``output_preview`` — telemetry reads the pre-edit content.
    """
    from atlas.application.chat.utilities.tool_executor import execute_single_tool
    from atlas.domain.messages.models import ToolResult

    tool_call = MagicMock()
    tool_call.id = "call_edit"
    tool_call.function.name = "db_run_query"
    tool_call.function.arguments = '{"query": "SELECT 1"}'

    tool_manager = MagicMock()
    tool_manager.get_tools_schema.return_value = [
        {"function": {"name": "db_run_query",
                       "parameters": {"properties": {"query": {}}}}}
    ]
    tool_manager.get_server_for_tool.return_value = "db"
    tool_manager.execute_tool = AsyncMock(
        return_value=ToolResult(
            tool_call_id="call_edit",
            content="rows=[1]",
            success=True,
        )
    )

    # Force the approval-edited path so edit_note gets prepended to result.content.
    approval_manager = MagicMock()
    approval_request = MagicMock()
    approval_request.wait_for_response = AsyncMock(
        return_value={
            "approved": True,
            "arguments": {"query": "DROP TABLE SECRETS_LEAKED_VIA_EDIT"},
        }
    )
    approval_manager.create_approval_request.return_value = approval_request
    approval_manager.cleanup_request = MagicMock()

    def _requires_approval(name, cfg):
        return (True, True, False)  # needs_approval, allow_edit, admin_required

    import atlas.application.chat.utilities.tool_executor as te

    monkey_get_am = lambda: approval_manager  # noqa: E731
    original_get_am = te.get_approval_manager
    original_requires_approval = te.requires_approval
    te.get_approval_manager = monkey_get_am
    te.requires_approval = _requires_approval
    try:
        cfg = MagicMock()
        result = await execute_single_tool(
            tool_call=tool_call,
            session_context={"user_email": "u@x.com"},
            tool_manager=tool_manager,
            config_manager=cfg,
        )
    finally:
        te.get_approval_manager = original_get_am
        te.requires_approval = original_requires_approval

    # The edit_note should now be in result.content (LLM-facing contract).
    assert "SECRETS_LEAKED_VIA_EDIT" in result.content

    # But telemetry must have captured the PRE-edit output.
    span = _by_name(span_exporter.get_finished_spans(), "tool.call")
    assert span.attributes["output_preview"] == "rows=[1]"
    assert "SECRETS_LEAKED_VIA_EDIT" not in span.attributes["output_preview"]
    assert span.attributes["args_edited"] is True


# ---------------------------------------------------------------------------
# rag.query span
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rag_query_span_extracts_docs(span_exporter):
    from atlas.domain.unified_rag_service import UnifiedRAGService
    from atlas.modules.rag.client import DocumentMetadata, RAGMetadata, RAGResponse

    svc = UnifiedRAGService.__new__(UnifiedRAGService)
    svc.config_manager = MagicMock()
    svc.mcp_manager = None
    svc.auth_check_func = None
    svc.rag_mcp_service = None
    svc._http_clients = {}

    fake_response = RAGResponse(
        content="answer",
        metadata=RAGMetadata(
            query_processing_time_ms=42,
            total_documents_searched=5,
            documents_found=[
                DocumentMetadata(
                    source="src",
                    content_type="text",
                    confidence_score=0.9,
                    chunk_id="c1",
                    title="Doc One",
                ),
                DocumentMetadata(
                    source="src",
                    content_type="text",
                    confidence_score=0.3,
                    chunk_id="c2",
                    title="Doc Two",
                ),
            ],
            data_source_name="ds",
            retrieval_method="hybrid",
        ),
    )

    async def fake_impl(username, qualified, messages):
        return fake_response

    svc._query_rag_impl = fake_impl

    messages = [{"role": "user", "content": "what is X"}]
    out = await svc.query_rag("alice@x.com", "atlas_rag:docs", messages)
    assert out.content == "answer"

    span = _by_name(span_exporter.get_finished_spans(), "rag.query")
    assert span.attributes["data_source"] == "atlas_rag:docs"
    assert span.attributes["num_results"] == 2
    assert list(span.attributes["doc_ids"]) == ["c1", "c2"]
    assert span.attributes["top_score"] == pytest.approx(0.9)
    # docs_used_in_context mirrors doc_ids in current implementation
    assert list(span.attributes["docs_used_in_context"]) == ["c1", "c2"]
    # query text itself must not appear on the span
    attr_values = [str(v) for v in span.attributes.values()]
    assert "what is X" not in "|".join(attr_values)


@pytest.mark.asyncio
async def test_rag_doc_ids_are_sanitized_when_falling_back_to_title(span_exporter):
    """Regression: doc.title/source come from untrusted RAG backends.

    When chunk_id is missing, the fallback string must be sanitized (no
    control chars) and length-capped before landing on the span.
    """
    from atlas.core.telemetry import LABEL_MAX_CHARS
    from atlas.domain.unified_rag_service import UnifiedRAGService
    from atlas.modules.rag.client import DocumentMetadata, RAGMetadata, RAGResponse

    svc = UnifiedRAGService.__new__(UnifiedRAGService)
    svc.config_manager = MagicMock()
    svc.mcp_manager = None
    svc.auth_check_func = None
    svc.rag_mcp_service = None
    svc._http_clients = {}

    evil_title = "Leaky\nTitle\rWith Controls " + ("Z" * 500)

    fake_response = RAGResponse(
        content="answer",
        metadata=RAGMetadata(
            query_processing_time_ms=1,
            total_documents_searched=1,
            documents_found=[
                # No chunk_id → service falls back to title
                DocumentMetadata(
                    source="src",
                    content_type="text",
                    confidence_score=0.5,
                    title=evil_title,
                ),
            ],
            data_source_name="ds",
            retrieval_method="hybrid",
        ),
    )

    async def fake_impl(username, qualified, messages):
        return fake_response

    svc._query_rag_impl = fake_impl

    await svc.query_rag("alice@x.com", "atlas_rag:docs", [{"role": "user", "content": "q"}])

    span = _by_name(span_exporter.get_finished_spans(), "rag.query")
    (only_id,) = list(span.attributes["doc_ids"])
    assert "\n" not in only_id
    assert "\r" not in only_id
    assert len(only_id) <= LABEL_MAX_CHARS


# ---------------------------------------------------------------------------
# chat.turn span — via ChatService
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_turn_span_attributes(span_exporter):
    from uuid import uuid4

    from atlas.application.chat.service import ChatService

    svc = ChatService.__new__(ChatService)
    svc.session_repository = MagicMock()
    svc.session_repository.get = AsyncMock(return_value=None)
    svc.create_session = AsyncMock(return_value=MagicMock(context={}))
    svc._incognito_sessions = set()
    svc.conversation_repository = None
    svc._get_orchestrator = MagicMock(
        return_value=MagicMock(execute=AsyncMock(return_value={"ok": True}))
    )

    session_id = uuid4()
    prompt = "please compute 2+2"
    result = await svc.handle_chat_message(
        session_id=session_id,
        content=prompt,
        model="gpt-4o",
        user_email="alice@example.com",
    )
    assert result == {"ok": True}

    span = _by_name(span_exporter.get_finished_spans(), "chat.turn")
    assert span.attributes["session_id"] == str(session_id)
    assert span.attributes["model"] == "gpt-4o"
    assert len(span.attributes["prompt_hash"]) == 16
    assert len(span.attributes["user_hash"]) == 16
    # raw prompt must not leak into attributes
    attr_values = [str(v) for v in span.attributes.values()]
    assert prompt not in "|".join(attr_values)
    # raw email must not leak either
    assert "alice@example.com" not in "|".join(attr_values)


# ---------------------------------------------------------------------------
# llm.call span — via _acompletion_with_retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_llm_call_span_records_token_usage(monkeypatch, span_exporter):
    from atlas.modules.llm import litellm_caller

    caller = litellm_caller.LiteLLMCaller.__new__(litellm_caller.LiteLLMCaller)

    usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    message = MagicMock(content="hi", tool_calls=None)
    choice = MagicMock(finish_reason="stop", message=message)
    response = MagicMock(usage=usage, choices=[choice])

    fake_acompletion = AsyncMock(return_value=response)
    monkeypatch.setattr(litellm_caller, "acompletion", fake_acompletion)

    out = await caller._acompletion_with_retry(
        model="openai/gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.2,
    )
    assert out is response

    span = _by_name(span_exporter.get_finished_spans(), "llm.call")
    assert span.attributes["model"] == "openai/gpt-4o"
    assert span.attributes["provider"] == "openai"
    assert span.attributes["model_version"] == "gpt-4o"
    assert span.attributes["input_tokens"] == 10
    assert span.attributes["output_tokens"] == 5
    assert span.attributes["finish_reason"] == "stop"
    assert span.attributes["retry_count"] == 0
    assert span.attributes["temperature"] == pytest.approx(0.2)
    assert span.attributes["latency_ms"] >= 0


def test_jsonl_exporter_writes_one_line_per_span(tmp_path):
    """The file exporter writes one JSON record per span with the documented fields."""
    from atlas.core.otel_config import JSONLSpanExporter

    out_file = tmp_path / "spans.jsonl"
    exporter = JSONLSpanExporter(out_file)

    attach_processor = SimpleSpanProcessor(exporter)
    provider = trace.get_tracer_provider()
    provider.add_span_processor(attach_processor)
    try:
        with telemetry.start_span("jsonl.test", {"foo": "bar"}):
            pass
    finally:
        attach_processor.shutdown()

    lines = [line for line in out_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["name"] == "jsonl.test"
    assert record["attributes"]["foo"] == "bar"
    assert len(record["trace_id"]) == 32
    assert len(record["span_id"]) == 16
    assert record["duration_ns"] is not None


def test_tool_output_sidecar_respects_flag(monkeypatch, tmp_path):
    """Full tool outputs are only written when ATLAS_LOG_TOOL_OUTPUTS is truthy."""
    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path))

    # Flag off → no sidecar written
    monkeypatch.delenv("ATLAS_LOG_TOOL_OUTPUTS", raising=False)
    with telemetry.start_span("sidecar.off"):
        assert telemetry.write_tool_output_sidecar("secret payload") is None

    # Flag on → sidecar written with span_id filename
    monkeypatch.setenv("ATLAS_LOG_TOOL_OUTPUTS", "true")
    with telemetry.start_span("sidecar.on"):
        path = telemetry.write_tool_output_sidecar("secret payload")
        assert path is not None
        from pathlib import Path as _Path
        assert _Path(path).read_text() == "secret payload"
        assert _Path(path).parent.name == "tool_outputs"


def test_tool_output_sidecar_sets_restrictive_permissions(monkeypatch, tmp_path):
    """Sidecar files must not be world/group-readable on POSIX filesystems.

    The tool output is raw content by design when the flag is on, so disk
    permissions are the only thing preventing a second local user from
    reading another user's captures.
    """
    import os as _os
    import stat as _stat
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("POSIX mode bits not enforced on Windows")

    monkeypatch.setenv("APP_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("ATLAS_LOG_TOOL_OUTPUTS", "true")
    with telemetry.start_span("sidecar.perms"):
        path = telemetry.write_tool_output_sidecar("payload")
        assert path is not None
        mode = _stat.S_IMODE(_os.stat(path).st_mode)
        # No group/other permissions
        assert mode & 0o077 == 0, f"sidecar file mode {oct(mode)} is too permissive"
        # Directory also restrictive
        parent_mode = _stat.S_IMODE(_os.stat(_os.path.dirname(path)).st_mode)
        assert parent_mode & 0o077 == 0, (
            f"tool_outputs dir mode {oct(parent_mode)} is too permissive"
        )


def test_jsonl_exporter_force_flush_and_shutdown(tmp_path):
    """force_flush must fsync the file; shutdown must close the handle.

    Reliability regression: the initial implementation returned True from
    force_flush without touching disk and did nothing on shutdown, which
    made it impossible to trust that spans were durable on crash.
    """
    from atlas.core.otel_config import JSONLSpanExporter

    out_file = tmp_path / "spans.jsonl"
    exporter = JSONLSpanExporter(out_file)

    attach_processor = SimpleSpanProcessor(exporter)
    provider = trace.get_tracer_provider()
    provider.add_span_processor(attach_processor)
    try:
        with telemetry.start_span("flush.test", {"k": "v"}):
            pass
    finally:
        attach_processor.shutdown()

    # force_flush returns True when handle is still open (SimpleSpanProcessor
    # already flushed before shutdown, but exporter handle should accept a
    # redundant flush). After shutdown it must return False.
    assert exporter.force_flush() is False, (
        "force_flush must report False after shutdown (file handle closed)"
    )

    # Shutdown is idempotent
    exporter.shutdown()
    exporter.shutdown()

    # Content was written
    lines = [ln for ln in out_file.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1


def test_jsonl_exporter_file_mode_restrictive(tmp_path):
    """spans.jsonl must be created with mode 0600 on POSIX filesystems."""
    import os as _os
    import stat as _stat
    import sys

    if sys.platform.startswith("win"):
        pytest.skip("POSIX mode bits not enforced on Windows")

    from atlas.core.otel_config import JSONLSpanExporter

    out_file = tmp_path / "spans.jsonl"
    exporter = JSONLSpanExporter(out_file)
    try:
        mode = _stat.S_IMODE(_os.stat(out_file).st_mode)
        assert mode & 0o077 == 0, f"spans.jsonl mode {oct(mode)} is too permissive"
    finally:
        exporter.shutdown()


@pytest.mark.asyncio
async def test_llm_call_span_records_error(monkeypatch, span_exporter):
    from atlas.modules.llm import litellm_caller

    caller = litellm_caller.LiteLLMCaller.__new__(litellm_caller.LiteLLMCaller)

    class FakeAuthErr(Exception):
        pass

    fake_acompletion = AsyncMock(side_effect=FakeAuthErr("invalid api key"))
    monkeypatch.setattr(litellm_caller, "acompletion", fake_acompletion)

    with pytest.raises(FakeAuthErr):
        await caller._acompletion_with_retry(
            model="openai/gpt-4o",
            messages=[{"role": "user", "content": "hi"}],
        )

    span = _by_name(span_exporter.get_finished_spans(), "llm.call")
    assert span.status.status_code.name == "ERROR"
    assert span.attributes.get("error_type") == "FakeAuthErr"
