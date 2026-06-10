"""Admin telemetry routes backed by the OpenTelemetry span audit trail.

Exposes read-only rollups and drill-downs over the structured spans emitted by
``atlas.core.telemetry`` (see docs/telemetry/README.md and the span attribute
contract). All endpoints require admin authz.

The data source is pluggable via the ``SpanReader`` protocol. The default
``FileSpanReader`` backend streams ``logs/spans.jsonl`` (or whatever ``APP_LOG_DIR``
points at). Swapping in an OTLP/Jaeger/Tempo backend later is a matter of
implementing ``SpanReader`` without any UI changes.

Sensitive-data policy (enforced by the span writer, re-checked here): no raw
prompts, tool arguments, tool outputs, or RAG document text are ever returned.
The dashboard only sees what is already in the span attributes — hashes, sizes,
counts, model names, tool names, data source IDs, status codes, and durations.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query

from atlas.modules.config import config_manager
from atlas.routes.admin_routes import require_admin

logger = logging.getLogger(__name__)

telemetry_router = APIRouter(prefix="/admin/telemetry", tags=["admin", "telemetry"])


# ---------------------------------------------------------------------------
# Span schema (contract frozen in docs/telemetry/README.md)
# ---------------------------------------------------------------------------

SPAN_CHAT_TURN = "chat.turn"
SPAN_LLM_CALL = "llm.call"
SPAN_TOOL_CALL = "tool.call"
SPAN_RAG_QUERY = "rag.query"

# Attribute keys that are considered safe to echo to the dashboard. Other
# attributes are dropped from drill-down responses defensively. This is a
# belt-and-suspenders check: the span writer already enforces the policy.
_SAFE_ATTRIBUTE_KEYS = {
    # chat.turn
    "turn_id", "session_id", "user_hash", "prompt_hash", "prompt_chars",
    "prompt_tokens", "model", "agent_mode", "only_rag", "tool_choice_required",
    "selected_tools_count", "selected_prompts_count", "selected_data_sources_count",
    # llm.call
    "provider", "model_version", "temperature", "max_tokens", "streaming",
    "has_tools", "tool_choice", "tools_schema_count", "message_count",
    "input_tokens", "output_tokens", "total_tokens", "finish_reason",
    "tool_calls_count", "retry_count", "latency_ms", "chunk_count",
    "output_chars", "output_tokens_estimate", "error_type",
    # tool.call
    "tool_name", "tool_source", "tool_call_id", "args_hash", "args_size",
    "args_edited", "success", "duration_ms", "output_size", "output_sha256",
    # rag.query
    "data_source", "query_hash", "query_chars", "batch", "batch_size",
    "is_completion", "content_size", "num_results",
    "total_documents_searched", "retrieval_method",
    "query_processing_time_ms", "doc_ids", "doc_scores",
    "docs_used_in_context", "top_score",
}

# Preview fields are opt-in — the rollup endpoints never surface them. The
# turn drill-down surfaces them because an admin has explicitly named a
# specific ``turn_id`` (and the span writer already sanitizes the preview
# strings: CR/LF stripped, capped at 300/500 chars).
_SAFE_ATTRIBUTE_KEYS_WITH_PREVIEW = _SAFE_ATTRIBUTE_KEYS | {"output_preview", "error_message"}


# ---------------------------------------------------------------------------
# Pluggable span reader
# ---------------------------------------------------------------------------


class SpanReader(Protocol):
    """Read-only span source. Implementations may back on a file, an OTLP
    collector, or any other store. The dashboard consumes this interface only.
    """

    def read(
        self,
        *,
        since_ns: Optional[int] = None,
        until_ns: Optional[int] = None,
        names: Optional[Iterable[str]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Yield span records matching the filters, as plain JSON dicts."""
        ...

    def read_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Return every span in a given trace (for drill-down waterfalls)."""
        ...


class FileSpanReader:
    """``logs/spans.jsonl`` backend. One JSON line per span."""

    def __init__(self, path: Path):
        self.path = path

    def _open(self):
        if not self.path.exists():
            return None
        return self.path.open("r", encoding="utf-8", errors="replace")

    def _iter_lines(self) -> Iterator[Dict[str, Any]]:
        fh = self._open()
        if fh is None:
            return
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    # Partial writes or corrupted lines — skip rather than
                    # failing the whole query.
                    continue

    def read(
        self,
        *,
        since_ns: Optional[int] = None,
        until_ns: Optional[int] = None,
        names: Optional[Iterable[str]] = None,
    ) -> Iterator[Dict[str, Any]]:
        name_filter = set(names) if names else None
        for record in self._iter_lines():
            if name_filter and record.get("name") not in name_filter:
                continue
            start = record.get("start_time_ns")
            if start is None:
                continue
            if since_ns is not None and start < since_ns:
                continue
            if until_ns is not None and start > until_ns:
                continue
            yield record

    def read_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for record in self._iter_lines():
            if record.get("trace_id") == trace_id:
                out.append(record)
        return out


_reader_override: Optional[SpanReader] = None


def _log_base_dir() -> Path:
    # Mirror the resolution in otel_config.py / admin_routes.py.
    override = os.getenv("APP_LOG_DIR") or config_manager.app_settings.app_log_dir
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / "logs"


def get_span_reader() -> SpanReader:
    """Return the currently configured span reader.

    Tests override this via ``set_span_reader``; production reads
    ``logs/spans.jsonl`` via ``FileSpanReader``.
    """
    if _reader_override is not None:
        return _reader_override
    return FileSpanReader(_log_base_dir() / "spans.jsonl")


def set_span_reader(reader: Optional[SpanReader]) -> None:
    """Install or clear a custom span reader. Intended for tests and for
    wiring in an OTLP/Jaeger/Tempo backend at startup.
    """
    global _reader_override
    _reader_override = reader


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TIMERANGE_TO_SECONDS = {
    "1h": 60 * 60,
    "24h": 24 * 60 * 60,
    "7d": 7 * 24 * 60 * 60,
    "30d": 30 * 24 * 60 * 60,
}


def _time_window_ns(range_str: str) -> tuple[int, int]:
    seconds = _TIMERANGE_TO_SECONDS.get(range_str)
    if seconds is None:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid range '{range_str}'. Use one of: {', '.join(_TIMERANGE_TO_SECONDS)}",
        )
    now_ns = time.time_ns()
    return now_ns - seconds * 1_000_000_000, now_ns


def _percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    return _percentile_sorted(ordered, p)


def _percentile_sorted(ordered: List[float], p: float) -> Optional[float]:
    """Same as _percentile but operates on a pre-sorted list.

    Callers that compute several percentiles over the same dataset (p50, p95,
    p99) should sort once and then call this helper to avoid the O(n log n)
    work per quantile.
    """
    if not ordered:
        return None
    if len(ordered) == 1:
        return float(ordered[0])
    k = (len(ordered) - 1) * p
    f = int(k)
    c = min(f + 1, len(ordered) - 1)
    if f == c:
        return float(ordered[f])
    return float(ordered[f] + (ordered[c] - ordered[f]) * (k - f))


def _percentiles(values: List[float], ps: Iterable[float]) -> Dict[float, Optional[float]]:
    """Compute multiple percentiles from the same dataset with a single sort."""
    if not values:
        return {p: None for p in ps}
    ordered = sorted(values)
    return {p: _percentile_sorted(ordered, p) for p in ps}


def _attrs(span: Dict[str, Any]) -> Dict[str, Any]:
    attrs = span.get("attributes") or {}
    return attrs if isinstance(attrs, dict) else {}


def _safe_attrs(span: Dict[str, Any], *, include_previews: bool = False) -> Dict[str, Any]:
    allowed = _SAFE_ATTRIBUTE_KEYS_WITH_PREVIEW if include_previews else _SAFE_ATTRIBUTE_KEYS
    return {k: v for k, v in _attrs(span).items() if k in allowed}


def _group_key(value: Any) -> str:
    """Coerce an attribute value to a safe string dict key.

    Aggregation endpoints group by attributes like ``tool_name`` and
    ``model``. A malformed span with a non-string (and possibly non-hashable)
    value would otherwise raise on ``defaultdict`` access and 500 the
    endpoint. Fall back to ``<unknown>`` for anything that isn't a
    non-empty string.
    """
    return value if isinstance(value, str) and value else "<unknown>"


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


@dataclass
class _ToolStats:
    call_count: int = 0
    success_count: int = 0
    durations: List[float] = field(default_factory=list)
    last_failure_start_ns: Optional[int] = None
    last_failure_error_type: Optional[str] = None


@dataclass
class _ModelStats:
    call_count: int = 0
    latencies: List[float] = field(default_factory=list)
    input_tokens_total: int = 0
    output_tokens_total: int = 0
    total_tokens_total: int = 0
    retries: int = 0
    retry_calls: int = 0  # calls with retry_count > 0
    error_count: int = 0


@dataclass
class _RagStats:
    query_count: int = 0
    docs_retrieved: int = 0
    docs_used: int = 0
    top_scores: List[float] = field(default_factory=list)


def _collect(
    reader: SpanReader, since_ns: int, until_ns: int, names: Iterable[str]
) -> List[Dict[str, Any]]:
    """Materialize filtered spans once per request so callers don't re-scan."""
    return list(reader.read(since_ns=since_ns, until_ns=until_ns, names=names))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@telemetry_router.get("/status")
async def telemetry_status(
    _admin: str = Depends(require_admin),  # noqa: ARG001
):
    """Lightweight status endpoint — is the spans file present and non-empty?"""
    reader = get_span_reader()
    info: Dict[str, Any] = {"backend": type(reader).__name__}
    if isinstance(reader, FileSpanReader):
        info["path"] = str(reader.path)
        info["available"] = reader.path.exists()
        if info["available"]:
            try:
                stat = reader.path.stat()
                info["size_bytes"] = stat.st_size
                info["last_modified"] = stat.st_mtime
            except OSError as e:
                info["error"] = str(e)
    else:
        info["available"] = True
    return info


@telemetry_router.get("/overview")
async def telemetry_overview(
    range: str = Query("24h", description="Time window: 1h, 24h, 7d, 30d"),
    _admin: str = Depends(require_admin),  # noqa: ARG001
):
    """Top-line rollup: turns, tool calls, tool success rate, LLM latency percentiles, RAG query count."""
    since_ns, until_ns = _time_window_ns(range)
    reader = get_span_reader()
    spans = _collect(
        reader,
        since_ns,
        until_ns,
        (SPAN_CHAT_TURN, SPAN_LLM_CALL, SPAN_TOOL_CALL, SPAN_RAG_QUERY),
    )

    turns = 0
    session_ids: set[str] = set()
    llm_calls = 0
    llm_latencies: List[float] = []
    tool_calls = 0
    tool_success = 0
    rag_queries = 0
    retries_total = 0

    for span in spans:
        name = span.get("name")
        attrs = _attrs(span)
        if name == SPAN_CHAT_TURN:
            turns += 1
            sid = attrs.get("session_id")
            if isinstance(sid, str) and sid:
                session_ids.add(sid)
        elif name == SPAN_LLM_CALL:
            llm_calls += 1
            latency = attrs.get("latency_ms")
            if isinstance(latency, (int, float)):
                llm_latencies.append(float(latency))
            rc = attrs.get("retry_count")
            if isinstance(rc, (int, float)):
                retries_total += int(rc)
        elif name == SPAN_TOOL_CALL:
            tool_calls += 1
            if attrs.get("success") is True:
                tool_success += 1
        elif name == SPAN_RAG_QUERY:
            rag_queries += 1

    return {
        "range": range,
        "since_ns": since_ns,
        "until_ns": until_ns,
        "turns": turns,
        "sessions": len(session_ids),
        "tool_calls": tool_calls,
        "tool_success_rate": (tool_success / tool_calls) if tool_calls else None,
        "llm_calls": llm_calls,
        "llm_latency_p50_ms": _percentile(llm_latencies, 0.5),
        "llm_latency_p95_ms": _percentile(llm_latencies, 0.95),
        "llm_retries_total": retries_total,
        "rag_queries": rag_queries,
    }


@telemetry_router.get("/tools")
async def telemetry_tools(
    range: str = Query("24h"),
    _admin: str = Depends(require_admin),  # noqa: ARG001
):
    """Tool health table: per-tool call count, success rate, p95 duration, last failure."""
    since_ns, until_ns = _time_window_ns(range)
    reader = get_span_reader()
    stats: Dict[str, _ToolStats] = defaultdict(_ToolStats)
    for span in reader.read(since_ns=since_ns, until_ns=until_ns, names=(SPAN_TOOL_CALL,)):
        attrs = _attrs(span)
        name = _group_key(attrs.get("tool_name"))
        s = stats[name]
        s.call_count += 1
        if attrs.get("success") is True:
            s.success_count += 1
        dur = attrs.get("duration_ms")
        if isinstance(dur, (int, float)):
            s.durations.append(float(dur))
        if attrs.get("success") is False:
            start_ns = span.get("start_time_ns")
            if isinstance(start_ns, int) and (
                s.last_failure_start_ns is None or start_ns > s.last_failure_start_ns
            ):
                s.last_failure_start_ns = start_ns
                s.last_failure_error_type = attrs.get("error_type") or span.get("status")

    tools = []
    for name, s in stats.items():
        pcts = _percentiles(s.durations, (0.5, 0.95))
        tools.append({
            "tool_name": name,
            "call_count": s.call_count,
            "success_rate": (s.success_count / s.call_count) if s.call_count else None,
            "failure_count": s.call_count - s.success_count,
            "duration_p50_ms": pcts[0.5],
            "duration_p95_ms": pcts[0.95],
            "last_failure_start_ns": s.last_failure_start_ns,
            "last_failure_error_type": s.last_failure_error_type,
        })
    tools.sort(key=lambda t: t["call_count"], reverse=True)
    return {"range": range, "tools": tools}


@telemetry_router.get("/tools/{tool_name}/failures")
async def telemetry_tool_failures(
    tool_name: str,
    range: str = Query("7d"),
    limit: int = Query(25, ge=1, le=200),
    _admin: str = Depends(require_admin),  # noqa: ARG001
):
    """Recent failures for a given tool — used for click-through from the tool table."""
    if not _ID_RE.match(tool_name):
        raise HTTPException(status_code=400, detail="Invalid tool_name")
    since_ns, until_ns = _time_window_ns(range)
    reader = get_span_reader()
    failures: List[Dict[str, Any]] = []
    for span in reader.read(since_ns=since_ns, until_ns=until_ns, names=(SPAN_TOOL_CALL,)):
        attrs = _attrs(span)
        if attrs.get("tool_name") != tool_name:
            continue
        if attrs.get("success") is not False:
            continue
        failures.append({
            "start_time_ns": span.get("start_time_ns"),
            "duration_ms": attrs.get("duration_ms"),
            "trace_id": span.get("trace_id"),
            "span_id": span.get("span_id"),
            "error_type": attrs.get("error_type") or span.get("status"),
            "error_message": attrs.get("error_message"),
            "args_hash": attrs.get("args_hash"),
            "args_size": attrs.get("args_size"),
            "tool_source": attrs.get("tool_source"),
        })
    failures.sort(key=lambda f: f.get("start_time_ns") or 0, reverse=True)
    return {"tool_name": tool_name, "range": range, "failures": failures[:limit]}


@telemetry_router.get("/llm")
async def telemetry_llm(
    range: str = Query("24h"),
    _admin: str = Depends(require_admin),  # noqa: ARG001
):
    """Per-model LLM performance: latency percentiles, token usage, retry rate."""
    since_ns, until_ns = _time_window_ns(range)
    reader = get_span_reader()
    stats: Dict[str, _ModelStats] = defaultdict(_ModelStats)
    for span in reader.read(since_ns=since_ns, until_ns=until_ns, names=(SPAN_LLM_CALL,)):
        attrs = _attrs(span)
        model = _group_key(attrs.get("model"))
        s = stats[model]
        s.call_count += 1
        latency = attrs.get("latency_ms")
        if isinstance(latency, (int, float)):
            s.latencies.append(float(latency))
        for token_attr, field_name in (
            ("input_tokens", "input_tokens_total"),
            ("output_tokens", "output_tokens_total"),
            ("total_tokens", "total_tokens_total"),
        ):
            v = attrs.get(token_attr)
            if isinstance(v, (int, float)):
                setattr(s, field_name, getattr(s, field_name) + int(v))
        rc = attrs.get("retry_count")
        if isinstance(rc, (int, float)) and rc > 0:
            s.retries += int(rc)
            s.retry_calls += 1
        if attrs.get("error_type"):
            s.error_count += 1

    models = []
    for model, s in stats.items():
        pcts = _percentiles(s.latencies, (0.5, 0.95, 0.99))
        models.append({
            "model": model,
            "call_count": s.call_count,
            "latency_p50_ms": pcts[0.5],
            "latency_p95_ms": pcts[0.95],
            "latency_p99_ms": pcts[0.99],
            "input_tokens_total": s.input_tokens_total,
            "output_tokens_total": s.output_tokens_total,
            "total_tokens_total": s.total_tokens_total,
            "retry_count_total": s.retries,
            "retry_rate": (s.retry_calls / s.call_count) if s.call_count else None,
            "error_count": s.error_count,
        })
    models.sort(key=lambda m: m["call_count"], reverse=True)
    return {"range": range, "models": models}


@telemetry_router.get("/rag")
async def telemetry_rag(
    range: str = Query("24h"),
    _admin: str = Depends(require_admin),  # noqa: ARG001
):
    """Per-data-source RAG effectiveness: query count, docs retrieved vs used, top score distribution."""
    since_ns, until_ns = _time_window_ns(range)
    reader = get_span_reader()
    stats: Dict[str, _RagStats] = defaultdict(_RagStats)
    for span in reader.read(since_ns=since_ns, until_ns=until_ns, names=(SPAN_RAG_QUERY,)):
        attrs = _attrs(span)
        source = _group_key(attrs.get("data_source"))
        s = stats[source]
        s.query_count += 1
        doc_ids = attrs.get("doc_ids")
        if isinstance(doc_ids, (list, tuple)):
            s.docs_retrieved += len(doc_ids)
        used = attrs.get("docs_used_in_context")
        if isinstance(used, (list, tuple)):
            s.docs_used += len(used)
        top = attrs.get("top_score")
        if isinstance(top, (int, float)):
            s.top_scores.append(float(top))

    sources = []
    for source, s in stats.items():
        pcts = _percentiles(s.top_scores, (0.5, 0.95))
        sources.append({
            "data_source": source,
            "query_count": s.query_count,
            "docs_retrieved_total": s.docs_retrieved,
            "docs_used_total": s.docs_used,
            "retrieval_to_use_ratio": (
                (s.docs_used / s.docs_retrieved) if s.docs_retrieved else None
            ),
            "top_score_p50": pcts[0.5],
            "top_score_p95": pcts[0.95],
            "top_score_max": max(s.top_scores) if s.top_scores else None,
        })
    sources.sort(key=lambda r: r["query_count"], reverse=True)
    return {"range": range, "sources": sources}


# Drill-down identifiers come from the span writer as UUIDs. We accept a
# broader alphanumeric/dash/underscore form defensively (session IDs are
# sometimes caller-supplied strings) while still rejecting path traversal,
# whitespace, slashes, and any character that could alter downstream log
# lines or file paths.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@telemetry_router.get("/sessions/search")
async def telemetry_session_search(
    session_id: Optional[str] = Query(None),
    turn_id: Optional[str] = Query(None),
    range: str = Query("7d"),
    limit: int = Query(50, ge=1, le=200),
    _admin: str = Depends(require_admin),  # noqa: ARG001
):
    """Look up recent chat turns matching ``session_id`` or ``turn_id``.

    Returns a summary list of matching turns; the caller then fetches the
    full span tree via ``/admin/telemetry/turn/{turn_id}``.
    """
    if not session_id and not turn_id:
        raise HTTPException(status_code=400, detail="Provide session_id or turn_id")
    for label, value in (("session_id", session_id), ("turn_id", turn_id)):
        if value is not None and not _ID_RE.match(value):
            raise HTTPException(status_code=400, detail=f"Invalid {label}")
    since_ns, until_ns = _time_window_ns(range)
    reader = get_span_reader()
    turns = []
    for span in reader.read(since_ns=since_ns, until_ns=until_ns, names=(SPAN_CHAT_TURN,)):
        attrs = _attrs(span)
        if session_id and attrs.get("session_id") != session_id:
            continue
        if turn_id and attrs.get("turn_id") != turn_id:
            continue
        turns.append({
            "turn_id": attrs.get("turn_id"),
            "session_id": attrs.get("session_id"),
            "user_hash": attrs.get("user_hash"),
            "model": attrs.get("model"),
            "start_time_ns": span.get("start_time_ns"),
            "duration_ns": span.get("duration_ns"),
            "trace_id": span.get("trace_id"),
            "span_id": span.get("span_id"),
            "status": span.get("status"),
            "prompt_chars": attrs.get("prompt_chars"),
            "selected_tools_count": attrs.get("selected_tools_count"),
            "selected_data_sources_count": attrs.get("selected_data_sources_count"),
        })
    turns.sort(key=lambda t: t.get("start_time_ns") or 0, reverse=True)
    return {"turns": turns[:limit], "range": range}


@telemetry_router.get("/turn/{turn_id}")
async def telemetry_turn(
    turn_id: str,
    _admin: str = Depends(require_admin),  # noqa: ARG001
):
    """Reconstruct the full span tree for a single chat turn.

    Finds the ``chat.turn`` span by ``turn_id``, then reads every span sharing
    the same ``trace_id`` and returns them as a parent/child tree with a
    timing waterfall suitable for rendering.
    """
    if not _ID_RE.match(turn_id):
        raise HTTPException(status_code=400, detail="Invalid turn_id")
    reader = get_span_reader()

    # The chat.turn span uniquely identifies the trace; without a time window
    # here we lean on the fact that turn_id -> single root span -> single
    # trace_id, so finding the root is cheap.
    root: Optional[Dict[str, Any]] = None
    for span in reader.read(names=(SPAN_CHAT_TURN,)):
        if _attrs(span).get("turn_id") == turn_id:
            root = span
            break
    if root is None:
        raise HTTPException(status_code=404, detail="turn_id not found")

    trace_id = root.get("trace_id")
    if not trace_id:
        raise HTTPException(status_code=500, detail="Root span missing trace_id")
    all_spans = reader.read_trace(trace_id)

    # Build parent -> children index.
    by_id: Dict[str, Dict[str, Any]] = {}
    children: Dict[Optional[str], List[str]] = defaultdict(list)
    for span in all_spans:
        sid = span.get("span_id")
        if not sid:
            continue
        by_id[sid] = span
        children[span.get("parent_span_id")].append(sid)

    root_start = root.get("start_time_ns") or 0
    root_duration = root.get("duration_ns") or 0

    def _serialize(span: Dict[str, Any]) -> Dict[str, Any]:
        start_ns = span.get("start_time_ns") or 0
        return {
            "name": span.get("name"),
            "span_id": span.get("span_id"),
            "parent_span_id": span.get("parent_span_id"),
            "trace_id": span.get("trace_id"),
            "start_time_ns": start_ns,
            "duration_ns": span.get("duration_ns"),
            "duration_ms": (
                (span.get("duration_ns") or 0) / 1_000_000 if span.get("duration_ns") else None
            ),
            "relative_start_ns": start_ns - root_start if root_start else None,
            "status": span.get("status"),
            "kind": span.get("kind"),
            "attributes": _safe_attrs(span, include_previews=True),
            "children": [],
        }

    def _walk(span_id: str, visited: set) -> Dict[str, Any]:
        span = by_id.get(span_id)
        if span is None:
            # The source may have rotated/truncated between the root lookup
            # and the full-trace scan; fall back to the cached root.
            return _serialize(root)
        if span_id in visited:
            # Defensive guard against malformed data with self-referencing
            # parents — should never happen with OTel-emitted spans.
            return _serialize(span)
        visited.add(span_id)
        node = _serialize(span)
        child_ids = sorted(
            children.get(span_id, []),
            key=lambda cid: by_id[cid].get("start_time_ns") or 0,
        )
        for cid in child_ids:
            node["children"].append(_walk(cid, visited))
        return node

    root_id = root.get("span_id")
    tree = _walk(root_id, set()) if root_id else _serialize(root)

    # Flat waterfall list (pre-order) for easy rendering.
    flat: List[Dict[str, Any]] = []

    def _flatten(node: Dict[str, Any], depth: int) -> None:
        entry = {k: v for k, v in node.items() if k != "children"}
        entry["depth"] = depth
        flat.append(entry)
        for child in node.get("children", []):
            _flatten(child, depth + 1)

    _flatten(tree, 0)

    return {
        "turn_id": turn_id,
        "trace_id": trace_id,
        "root_start_ns": root_start,
        "root_duration_ns": root_duration,
        "span_count": len(by_id),
        "tree": tree,
        "waterfall": flat,
    }


__all__ = [
    "telemetry_router",
    "SpanReader",
    "FileSpanReader",
    "get_span_reader",
    "set_span_reader",
]
