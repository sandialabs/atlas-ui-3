"""Tests for the admin telemetry routes (issue #546).

Covers:
- Admin-gating on every endpoint
- Overview / tool / LLM / RAG rollups produce correct aggregates on synthetic spans
- Session search and turn drill-down reconstruct the span tree from parent/child links
- No raw prompts, tool outputs, or RAG document text can leak into responses
- The pluggable SpanReader protocol works (swap in an in-memory reader for tests)
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, Iterator, List, Optional

import pytest
from main import app
from atlas.modules.config import config_manager
from starlette.testclient import TestClient

from atlas.routes import telemetry_routes

SEC_NS = 1_000_000_000


@pytest.fixture
def client():
    return TestClient(app)


class _MemoryReader:
    def __init__(self, spans: List[Dict[str, Any]]):
        self.spans = spans

    def read(
        self,
        *,
        since_ns: Optional[int] = None,
        until_ns: Optional[int] = None,
        names: Optional[Iterable[str]] = None,
    ) -> Iterator[Dict[str, Any]]:
        name_filter = set(names) if names else None
        for span in self.spans:
            if name_filter and span.get("name") not in name_filter:
                continue
            start = span.get("start_time_ns")
            if start is None:
                continue
            if since_ns is not None and start < since_ns:
                continue
            if until_ns is not None and start > until_ns:
                continue
            yield span

    def read_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        return [s for s in self.spans if s.get("trace_id") == trace_id]


def _build_synthetic_spans(now_ns: Optional[int] = None) -> List[Dict[str, Any]]:
    now_ns = now_ns or time.time_ns()
    recent = now_ns - 60 * SEC_NS  # 1 min ago
    # One complete turn: chat.turn root + one llm.call + one successful tool
    # call + one failed tool call + one rag.query.
    return [
        {
            "name": "chat.turn",
            "trace_id": "t1",
            "span_id": "root1",
            "parent_span_id": None,
            "start_time_ns": recent,
            "end_time_ns": recent + 500 * 1_000_000,
            "duration_ns": 500 * 1_000_000,
            "status": "OK",
            "kind": "INTERNAL",
            "attributes": {
                "turn_id": "turn-abc",
                "session_id": "session-xyz",
                "user_hash": "deadbeefcafebabe",
                "prompt_hash": "1111222233334444",
                "prompt_chars": 42,
                "model": "gpt-4o",
                "agent_mode": False,
                "selected_tools_count": 2,
                "selected_data_sources_count": 1,
            },
        },
        {
            "name": "llm.call",
            "trace_id": "t1",
            "span_id": "llm1",
            "parent_span_id": "root1",
            "start_time_ns": recent + 10_000_000,
            "end_time_ns": recent + 210_000_000,
            "duration_ns": 200_000_000,
            "status": "OK",
            "kind": "INTERNAL",
            "attributes": {
                "model": "gpt-4o",
                "provider": "openai",
                "latency_ms": 200,
                "input_tokens": 100,
                "output_tokens": 50,
                "total_tokens": 150,
                "retry_count": 1,
            },
        },
        {
            "name": "tool.call",
            "trace_id": "t1",
            "span_id": "tool1",
            "parent_span_id": "root1",
            "start_time_ns": recent + 220_000_000,
            "end_time_ns": recent + 320_000_000,
            "duration_ns": 100_000_000,
            "status": "OK",
            "kind": "INTERNAL",
            "attributes": {
                "tool_name": "calculator_add",
                "tool_source": "calculator",
                "success": True,
                "duration_ms": 100,
                "args_hash": "abcd1234abcd1234",
                "args_size": 32,
                "output_size": 8,
            },
        },
        {
            "name": "tool.call",
            "trace_id": "t1",
            "span_id": "tool2",
            "parent_span_id": "root1",
            "start_time_ns": recent + 330_000_000,
            "end_time_ns": recent + 380_000_000,
            "duration_ns": 50_000_000,
            "status": "ERROR",
            "kind": "INTERNAL",
            "attributes": {
                "tool_name": "calculator_divide",
                "tool_source": "calculator",
                "success": False,
                "duration_ms": 50,
                "error_type": "ZeroDivisionError",
                "error_message": "division by zero",
            },
        },
        {
            "name": "rag.query",
            "trace_id": "t1",
            "span_id": "rag1",
            "parent_span_id": "root1",
            "start_time_ns": recent + 390_000_000,
            "end_time_ns": recent + 410_000_000,
            "duration_ns": 20_000_000,
            "status": "OK",
            "kind": "INTERNAL",
            "attributes": {
                "data_source": "docs:handbook",
                "num_results": 3,
                "doc_ids": ["d1", "d2", "d3"],
                "docs_used_in_context": ["d1", "d2"],
                "top_score": 0.87,
            },
        },
    ]


@pytest.fixture
def memory_reader(request):
    reader = _MemoryReader(_build_synthetic_spans())
    telemetry_routes.set_span_reader(reader)
    yield reader
    telemetry_routes.set_span_reader(None)


def _admin(path: str, client: TestClient, **params: Any):
    return client.get(
        path,
        params=params,
        headers={"X-User-Email": config_manager.app_settings.admin_test_user},
    )


def _user(path: str, client: TestClient, **params: Any):
    return client.get(
        path,
        params=params,
        headers={"X-User-Email": "user@example.com"},
    )


@pytest.mark.parametrize(
    "path",
    [
        "/admin/telemetry/status",
        "/admin/telemetry/overview",
        "/admin/telemetry/tools",
        "/admin/telemetry/llm",
        "/admin/telemetry/rag",
        "/admin/telemetry/tools/calculator_divide/failures",
        "/admin/telemetry/sessions/search",
        "/admin/telemetry/turn/turn-abc",
    ],
)
def test_endpoints_require_admin(client, memory_reader, path):
    r = _user(path, client)
    assert r.status_code in (302, 403), f"{path} allowed non-admin access: {r.status_code}"


def test_overview_rollup(client, memory_reader):
    r = _admin("/admin/telemetry/overview", client, range="24h")
    assert r.status_code == 200
    data = r.json()
    assert data["turns"] == 1
    assert data["tool_calls"] == 2
    assert data["tool_success_rate"] == pytest.approx(0.5)
    assert data["llm_calls"] == 1
    assert data["llm_latency_p50_ms"] == pytest.approx(200.0)
    assert data["llm_retries_total"] == 1
    assert data["rag_queries"] == 1


def test_overview_invalid_range(client, memory_reader):
    r = _admin("/admin/telemetry/overview", client, range="bogus")
    assert r.status_code == 400


def test_tools_view(client, memory_reader):
    r = _admin("/admin/telemetry/tools", client, range="24h")
    assert r.status_code == 200
    tools = {t["tool_name"]: t for t in r.json()["tools"]}
    assert tools["calculator_add"]["success_rate"] == 1.0
    assert tools["calculator_add"]["duration_p95_ms"] == pytest.approx(100.0)
    assert tools["calculator_divide"]["success_rate"] == 0.0
    assert tools["calculator_divide"]["last_failure_error_type"] == "ZeroDivisionError"


def test_tool_failures_list(client, memory_reader):
    r = _admin(
        "/admin/telemetry/tools/calculator_divide/failures",
        client,
        range="7d",
    )
    assert r.status_code == 200
    data = r.json()
    assert data["tool_name"] == "calculator_divide"
    assert len(data["failures"]) == 1
    f = data["failures"][0]
    assert f["error_type"] == "ZeroDivisionError"
    assert f["args_hash"] is None or isinstance(f["args_hash"], str)


def test_llm_per_model(client, memory_reader):
    r = _admin("/admin/telemetry/llm", client, range="24h")
    assert r.status_code == 200
    models = r.json()["models"]
    assert len(models) == 1
    m = models[0]
    assert m["model"] == "gpt-4o"
    assert m["call_count"] == 1
    assert m["input_tokens_total"] == 100
    assert m["output_tokens_total"] == 50
    assert m["total_tokens_total"] == 150
    assert m["retry_count_total"] == 1
    assert m["retry_rate"] == 1.0
    assert m["latency_p50_ms"] == pytest.approx(200.0)


def test_rag_per_source(client, memory_reader):
    r = _admin("/admin/telemetry/rag", client, range="24h")
    assert r.status_code == 200
    sources = r.json()["sources"]
    assert len(sources) == 1
    s = sources[0]
    assert s["data_source"] == "docs:handbook"
    assert s["query_count"] == 1
    assert s["docs_retrieved_total"] == 3
    assert s["docs_used_total"] == 2
    assert s["retrieval_to_use_ratio"] == pytest.approx(2 / 3)
    assert s["top_score_max"] == pytest.approx(0.87)


def test_session_search_by_session_id(client, memory_reader):
    r = _admin("/admin/telemetry/sessions/search", client, session_id="session-xyz")
    assert r.status_code == 200
    turns = r.json()["turns"]
    assert len(turns) == 1
    assert turns[0]["turn_id"] == "turn-abc"
    assert turns[0]["trace_id"] == "t1"


def test_session_search_requires_identifier(client, memory_reader):
    r = _admin("/admin/telemetry/sessions/search", client)
    assert r.status_code == 400


def test_session_search_rejects_invalid_id(client, memory_reader):
    r = _admin(
        "/admin/telemetry/sessions/search",
        client,
        session_id="../../etc/passwd",
    )
    assert r.status_code == 400


def test_turn_drilldown_reconstructs_tree(client, memory_reader):
    r = _admin("/admin/telemetry/turn/turn-abc", client)
    assert r.status_code == 200
    body = r.json()
    assert body["turn_id"] == "turn-abc"
    assert body["trace_id"] == "t1"
    assert body["span_count"] == 5

    # Root is the chat.turn with 3 children (1 llm, 2 tools, 1 rag = 4 total).
    tree = body["tree"]
    assert tree["name"] == "chat.turn"
    assert len(tree["children"]) == 4
    child_names = sorted(c["name"] for c in tree["children"])
    assert child_names == ["llm.call", "rag.query", "tool.call", "tool.call"]

    # Waterfall is a flat pre-order list with depths.
    waterfall = body["waterfall"]
    assert waterfall[0]["depth"] == 0
    assert waterfall[0]["name"] == "chat.turn"
    assert all(w["depth"] >= 1 for w in waterfall[1:])


def test_turn_drilldown_404(client, memory_reader):
    r = _admin("/admin/telemetry/turn/turn-does-not-exist", client)
    assert r.status_code == 404


def test_turn_drilldown_rejects_invalid_id(client, memory_reader):
    r = _admin("/admin/telemetry/turn/..%2Fsecrets", client)
    # Path traversal chars never pass the _ID_RE regex
    # (^[A-Za-z0-9_-]{1,128}$). FastAPI path decoding may also 404 the
    # traversal form before our handler runs.
    assert r.status_code in (400, 404)


def test_responses_never_leak_raw_content(client, memory_reader):
    """Defensive check: even if a malformed span smuggled a raw prompt, the
    response attribute whitelist must drop it."""
    reader = _MemoryReader(
        [
            {
                "name": "chat.turn",
                "trace_id": "t9",
                "span_id": "r9",
                "parent_span_id": None,
                "start_time_ns": time.time_ns() - 10 * SEC_NS,
                "end_time_ns": time.time_ns(),
                "duration_ns": 10 * SEC_NS,
                "status": "OK",
                "kind": "INTERNAL",
                "attributes": {
                    "turn_id": "turn-leak",
                    "session_id": "session-leak",
                    "prompt": "SECRET_PROMPT_SHOULD_NOT_APPEAR",
                    "tool_output": "SECRET_OUTPUT_SHOULD_NOT_APPEAR",
                    "rag_document": "SECRET_RAG_SHOULD_NOT_APPEAR",
                },
            }
        ]
    )
    telemetry_routes.set_span_reader(reader)
    try:
        r = _admin("/admin/telemetry/turn/turn-leak", client)
        assert r.status_code == 200
        body = json.dumps(r.json())
        assert "SECRET_PROMPT_SHOULD_NOT_APPEAR" not in body
        assert "SECRET_OUTPUT_SHOULD_NOT_APPEAR" not in body
        assert "SECRET_RAG_SHOULD_NOT_APPEAR" not in body
    finally:
        telemetry_routes.set_span_reader(None)


def test_status_reports_file_backend(client, memory_reader):
    # memory_reader overrides; the status endpoint reports the backend type.
    r = _admin("/admin/telemetry/status", client)
    assert r.status_code == 200
    data = r.json()
    assert data["backend"] == "_MemoryReader"


def test_overview_counts_llm_spans_not_latencies(client):
    """Overview ``llm_calls`` must count ``llm.call`` spans directly, not
    derive from spans that happen to carry a numeric ``latency_ms``."""
    now_ns = time.time_ns()
    recent = now_ns - 30 * SEC_NS
    reader = _MemoryReader(
        [
            {
                "name": "llm.call",
                "trace_id": "t1",
                "span_id": "llm1",
                "start_time_ns": recent,
                "duration_ns": 10_000_000,
                "status": "OK",
                "attributes": {"model": "gpt-4o", "latency_ms": 150},
            },
            {
                # Span without latency_ms (e.g. hit an exception before
                # latency was recorded). Must still count toward llm_calls.
                "name": "llm.call",
                "trace_id": "t2",
                "span_id": "llm2",
                "start_time_ns": recent,
                "duration_ns": 10_000_000,
                "status": "ERROR",
                "attributes": {"model": "gpt-4o", "error_type": "Timeout"},
            },
        ]
    )
    telemetry_routes.set_span_reader(reader)
    try:
        r = _admin("/admin/telemetry/overview", client, range="24h")
        assert r.status_code == 200
        data = r.json()
        assert data["llm_calls"] == 2
        assert data["llm_latency_p50_ms"] == pytest.approx(150.0)

        # /llm counts the same way — keep Overview in agreement with it.
        r2 = _admin("/admin/telemetry/llm", client, range="24h")
        assert r2.status_code == 200
        total = sum(m["call_count"] for m in r2.json()["models"])
        assert total == data["llm_calls"]
    finally:
        telemetry_routes.set_span_reader(None)


def test_tool_failures_rejects_invalid_tool_name(client, memory_reader):
    # Slash is the FastAPI path separator — keep a check for a whitespace
    # form that survives URL decoding to exercise our own regex.
    r = _admin(
        "/admin/telemetry/tools/ bad name /failures",
        client,
        range="24h",
    )
    assert r.status_code in (400, 404)


def test_tool_failures_rejects_invalid_tool_name_explicit(client, memory_reader):
    # Use a character explicitly outside the _ID_RE charset.
    r = _admin(
        "/admin/telemetry/tools/bad.name/failures",
        client,
        range="24h",
    )
    assert r.status_code == 400


def test_malformed_non_string_tool_name_does_not_500(client):
    """A corrupt span with a non-string ``tool_name`` must not 500 the
    aggregation endpoint."""
    now_ns = time.time_ns()
    recent = now_ns - 30 * SEC_NS
    reader = _MemoryReader(
        [
            {
                "name": "tool.call",
                "trace_id": "t1",
                "span_id": "tool1",
                "start_time_ns": recent,
                "duration_ns": 1_000_000,
                "status": "OK",
                "attributes": {
                    # List isn't hashable; the endpoint must coerce to
                    # ``<unknown>`` instead of raising TypeError.
                    "tool_name": ["malformed", "list"],
                    "success": True,
                    "duration_ms": 1,
                },
            }
        ]
    )
    telemetry_routes.set_span_reader(reader)
    try:
        r = _admin("/admin/telemetry/tools", client, range="24h")
        assert r.status_code == 200
        tools = r.json()["tools"]
        assert any(t["tool_name"] == "<unknown>" for t in tools)
    finally:
        telemetry_routes.set_span_reader(None)


def test_malformed_non_string_model_does_not_500(client):
    now_ns = time.time_ns()
    recent = now_ns - 30 * SEC_NS
    reader = _MemoryReader(
        [
            {
                "name": "llm.call",
                "trace_id": "t1",
                "span_id": "llm1",
                "start_time_ns": recent,
                "duration_ns": 1_000_000,
                "status": "OK",
                "attributes": {
                    "model": {"bogus": "dict"},
                    "latency_ms": 10,
                },
            }
        ]
    )
    telemetry_routes.set_span_reader(reader)
    try:
        r = _admin("/admin/telemetry/llm", client, range="24h")
        assert r.status_code == 200
        models = r.json()["models"]
        assert any(m["model"] == "<unknown>" for m in models)
    finally:
        telemetry_routes.set_span_reader(None)


def test_turn_drilldown_missing_children_does_not_500(client):
    """If the root span disappears between the root lookup and the trace
    scan (e.g. log rotation), the endpoint should still return a response
    instead of raising ``KeyError``."""

    now_ns = time.time_ns()
    recent = now_ns - 30 * SEC_NS
    root_span = {
        "name": "chat.turn",
        "trace_id": "tX",
        "span_id": "rootX",
        "parent_span_id": None,
        "start_time_ns": recent,
        "end_time_ns": recent + 1_000_000,
        "duration_ns": 1_000_000,
        "status": "OK",
        "attributes": {"turn_id": "turn-x"},
    }

    class _RotatingReader:
        def __init__(self):
            self._calls = 0

        def read(self, *, since_ns=None, until_ns=None, names=None):
            # First call: finds the root span.
            # All further calls: file got truncated, nothing to return.
            self._calls += 1
            if self._calls == 1:
                yield root_span

        def read_trace(self, trace_id):
            # Trace disappeared post-root-lookup.
            return []

    telemetry_routes.set_span_reader(_RotatingReader())
    try:
        r = _admin("/admin/telemetry/turn/turn-x", client)
        assert r.status_code == 200
        body = r.json()
        assert body["turn_id"] == "turn-x"
        assert body["trace_id"] == "tX"
        assert body["tree"]["name"] == "chat.turn"
    finally:
        telemetry_routes.set_span_reader(None)


def test_file_span_reader_reads_jsonl(tmp_path):
    """FileSpanReader streams synthetic spans.jsonl, handles bad lines, and filters."""
    spans_file = tmp_path / "spans.jsonl"
    now_ns = time.time_ns()
    records = [
        {"name": "chat.turn", "trace_id": "a", "span_id": "1", "start_time_ns": now_ns, "attributes": {}},
        "not valid json",
        {"name": "llm.call", "trace_id": "a", "span_id": "2", "start_time_ns": now_ns, "attributes": {}},
        {"name": "chat.turn", "trace_id": "b", "span_id": "3", "start_time_ns": now_ns - 10**12, "attributes": {}},
    ]
    with spans_file.open("w") as f:
        for r in records:
            if isinstance(r, str):
                f.write(r + "\n")
            else:
                f.write(json.dumps(r) + "\n")
    reader = telemetry_routes.FileSpanReader(spans_file)

    # Only chat.turn spans within the last minute.
    window = list(reader.read(since_ns=now_ns - 60 * SEC_NS, names=("chat.turn",)))
    assert [s["span_id"] for s in window] == ["1"]

    # read_trace returns every span in the trace regardless of time.
    trace_a = reader.read_trace("a")
    assert {s["span_id"] for s in trace_a} == {"1", "2"}
