"""Telemetry helpers for emitting OpenTelemetry spans with a T&E-friendly
audit trail.

Exposes a thin wrapper around ``opentelemetry.trace`` plus sanitization helpers
so every call site uses the same field-naming and sensitive-data policy.

Sensitive-data policy (enforced here):
- Raw prompts, tool arguments, tool outputs, and RAG document text MUST NOT
  appear in span attributes. Callers pass content to ``hash_short``,
  ``preview``, and ``size_bytes`` before attaching attributes.
- Full tool outputs are only written to ``logs/tool_outputs/{span_id}.txt``
  when ``ATLAS_LOG_TOOL_OUTPUTS=true``.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from atlas.core.log_sanitizer import sanitize_for_logging

logger = logging.getLogger(__name__)

TRACER_NAME = "atlas.telemetry"
PREVIEW_MAX_CHARS = 500
HASH_LENGTH = 16


def _tracer():
    return trace.get_tracer(TRACER_NAME)


def hash_short(value: Any) -> Optional[str]:
    """Return a truncated SHA-256 hex digest (16 chars) of ``value``.

    Returns None for falsy input so empty attributes are omitted.
    """
    if value is None:
        return None
    if not isinstance(value, (str, bytes)):
        value = str(value)
    if isinstance(value, str):
        value = value.encode("utf-8", errors="replace")
    if not value:
        return None
    return hashlib.sha256(value).hexdigest()[:HASH_LENGTH]


def sha256_full(value: Any) -> Optional[str]:
    """Return the full SHA-256 hex digest of ``value`` or None when empty."""
    if value is None:
        return None
    if not isinstance(value, (str, bytes)):
        value = str(value)
    if isinstance(value, str):
        value = value.encode("utf-8", errors="replace")
    if not value:
        return None
    return hashlib.sha256(value).hexdigest()


def size_bytes(value: Any) -> int:
    """Return the UTF-8 byte size of ``value``. Non-strings are stringified."""
    if value is None:
        return 0
    if isinstance(value, bytes):
        return len(value)
    if not isinstance(value, str):
        value = str(value)
    return len(value.encode("utf-8", errors="replace"))


def preview(value: Any, max_chars: int = PREVIEW_MAX_CHARS) -> Optional[str]:
    """Return a sanitized, length-capped preview of ``value``.

    Runs through ``sanitize_for_logging`` to strip CR/LF and control chars that
    could otherwise forge log lines or leak prompt-injection payloads.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    if not value:
        return None
    truncated = value[:max_chars]
    sanitized = sanitize_for_logging(truncated)
    if len(value) > max_chars:
        sanitized = f"{sanitized}...[truncated {len(value) - max_chars} chars]"
    return sanitized


def _coerce_attr(value: Any) -> Any:
    """Coerce ``value`` into an OTel-safe attribute type.

    OTel attribute values must be str, bool, int, float, or a homogeneous
    sequence of those. Anything else gets stringified.
    """
    if value is None:
        return None
    if isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, (list, tuple)):
        if not value:
            return []
        scalars = []
        for item in value:
            if isinstance(item, (str, bool, int, float)):
                scalars.append(item)
            else:
                scalars.append(str(item))
        return scalars
    return str(value)


def set_attrs(span: Optional[Span], attrs: Mapping[str, Any]) -> None:
    """Attach a dict of attributes to ``span``, skipping ``None`` values.

    Empty lists ARE preserved so list-typed contract fields like ``doc_ids``
    appear as explicit ``[]`` rather than disappearing — downstream analyzers
    rely on that to distinguish "present but empty" from "missing".

    No-op when ``span`` is not recording (e.g. when OTel is not initialized).
    """
    if span is None or not span.is_recording():
        return
    for key, value in attrs.items():
        coerced = _coerce_attr(value)
        if coerced is None:
            continue
        try:
            span.set_attribute(key, coerced)
        except Exception as e:  # noqa: BLE001
            # Sanitize the exception string before logging — attribute values
            # may have originated from tool output / user content in edge
            # cases, and set_attribute errors can echo them back.
            logger.debug(
                "Failed to set span attribute %s: %s",
                sanitize_for_logging(str(key)),
                sanitize_for_logging(str(e)),
            )


@contextmanager
def start_span(
    name: str,
    attrs: Optional[Mapping[str, Any]] = None,
    kind: trace.SpanKind = trace.SpanKind.INTERNAL,
) -> Iterator[Span]:
    """Start an OTel span, set initial attributes, and auto-close.

    On exception the span is marked with StatusCode.ERROR and the exception is
    recorded before re-raising.
    """
    tracer = _tracer()
    with tracer.start_as_current_span(name, kind=kind) as span:
        if attrs:
            set_attrs(span, attrs)
        try:
            yield span
        except Exception as exc:
            # Record ERROR status and the exception *class name* only.
            # Deliberately do not call ``span.record_exception`` — that
            # attaches the full exception message and stack trace as a span
            # event, which is forwarded verbatim by OTLP exporters and can
            # include user content, prompts, or tool output that happened to
            # land in an exception message.
            try:
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                span.set_attribute("error_type", type(exc).__name__)
            except Exception:  # noqa: BLE001 – never let telemetry break the app
                pass
            raise


def current_span_id() -> Optional[str]:
    """Return the active span's span_id as a 16-char hex string, if any."""
    span = trace.get_current_span()
    if not span or not span.is_recording():
        return None
    ctx = span.get_span_context()
    if not ctx.is_valid:
        return None
    return f"{ctx.span_id:016x}"


def _tool_output_dir() -> Path:
    """Return the directory where opt-in full tool outputs are stored."""
    override = os.getenv("APP_LOG_DIR")
    if override:
        base = Path(override)
    else:
        base = Path(__file__).resolve().parents[2] / "logs"
    return base / "tool_outputs"


def tool_outputs_enabled() -> bool:
    """True when ATLAS_LOG_TOOL_OUTPUTS is set to a truthy value."""
    return os.getenv("ATLAS_LOG_TOOL_OUTPUTS", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def write_tool_output_sidecar(content: Any) -> Optional[str]:
    """When opted in, persist full tool output keyed by current span_id.

    Returns the file path written, or ``None`` when the feature is disabled or
    no active span is available.
    """
    if not tool_outputs_enabled():
        return None
    span_id = current_span_id()
    if not span_id:
        return None
    if content is None:
        return None
    if not isinstance(content, str):
        content = str(content)
    try:
        out_dir = _tool_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{span_id}.txt"
        with out_path.open("w", encoding="utf-8") as f:
            f.write(content)
        return str(out_path)
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to write tool output sidecar: %s", e)
        return None


def safe_set_attrs(attrs: Mapping[str, Any]) -> None:
    """Attach attributes to the currently-active span, if any.

    Convenience helper for call sites that aren't the span creator.
    """
    span = trace.get_current_span()
    if span is None:
        return
    set_attrs(span, attrs)


@contextlib.contextmanager
def noop_span() -> Iterator[None]:
    """Yield without creating a span. Used when OTel is not desired."""
    yield None
