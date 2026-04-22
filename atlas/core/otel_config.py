"""Unified logging & OpenTelemetry setup.

Provides:
- Structured JSON logging with optional trace/span identifiers
- Environment or config-derived log level
- Standard file output (project_root/logs/app.jsonl) with APP_LOG_DIR override
- FastAPI & HTTPX instrumentation hooks
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, SERVICE_VERSION, Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult


class JSONFormatter(logging.Formatter):
    """Format log records as JSON lines."""

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        span = trace.get_current_span()
        trace_id = span_id = None
        if span and span.is_recording():
            sc = span.get_span_context()
            if sc.is_valid:
                trace_id = f"{sc.trace_id:032x}"
                span_id = f"{sc.span_id:016x}"

        entry: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "process_id": os.getpid(),
            "thread_id": record.thread,
            "thread_name": record.threadName,
        }
        if trace_id:
            entry["trace_id"] = trace_id
        if span_id:
            entry["span_id"] = span_id
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)

        excluded = {
            "name","msg","args","levelname","levelno","pathname","filename","module","lineno",
            "funcName","created","msecs","relativeCreated","thread","threadName","processName","process",
            "exc_info","exc_text","stack_info","getMessage"
        }
        for k, v in record.__dict__.items():
            if k not in excluded:
                entry[f"extra_{k}"] = v
        return json.dumps(entry, default=str)


class JSONLSpanExporter(SpanExporter):
    """Append one JSON line per finished span to a file.

    Fields emitted are stable and form the public contract documented in
    ``docs/telemetry/README.md``. Downstream analyzers rely on the exact
    attribute names defined in ``atlas/core/telemetry.py`` call sites.

    The exporter holds a single long-lived file handle guarded by a lock so
    batched exports don't pay per-call ``open``/``close`` and concurrent
    exports don't interleave partial JSON lines. ``force_flush`` issues an
    ``fsync`` so tests and graceful shutdown can observe durable writes;
    ``shutdown`` closes the handle.
    """

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._shutdown = False
        # Create the file up-front with restrictive perms so the first span
        # export doesn't race against a world-readable file on disk.
        try:
            fd = os.open(
                str(self.file_path),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                0o600,
            )
            self._fh = os.fdopen(fd, "a", encoding="utf-8")
        except OSError:
            # Fall back to plain open for platforms without os.open perms.
            self._fh = self.file_path.open("a", encoding="utf-8")
        try:
            os.chmod(self.file_path, 0o600)
        except OSError:
            pass

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:  # noqa: D401
        with self._lock:
            if self._shutdown or self._fh is None or self._fh.closed:
                return SpanExportResult.FAILURE
            try:
                for span in spans:
                    ctx = span.get_span_context()
                    parent = span.parent
                    record: Dict[str, Any] = {
                        "name": span.name,
                        "trace_id": f"{ctx.trace_id:032x}",
                        "span_id": f"{ctx.span_id:016x}",
                        "parent_span_id": f"{parent.span_id:016x}" if parent else None,
                        "start_time_ns": span.start_time,
                        "end_time_ns": span.end_time,
                        "duration_ns": (
                            span.end_time - span.start_time
                            if span.start_time and span.end_time
                            else None
                        ),
                        "status": span.status.status_code.name if span.status else None,
                        "kind": span.kind.name if span.kind else None,
                        "attributes": dict(span.attributes or {}),
                    }
                    self._fh.write(json.dumps(record, default=str) + "\n")
                self._fh.flush()
                return SpanExportResult.SUCCESS
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).error("JSONL span export failed: %s", e)
                return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            if self._fh is not None and not self._fh.closed:
                try:
                    self._fh.flush()
                    try:
                        os.fsync(self._fh.fileno())
                    except (OSError, ValueError):
                        pass
                    self._fh.close()
                except Exception as e:  # noqa: BLE001
                    logging.getLogger(__name__).debug(
                        "JSONLSpanExporter shutdown close failed: %s", e
                    )

    def force_flush(self, timeout_millis: int = 30000) -> bool:  # noqa: ARG002
        with self._lock:
            if self._fh is None or self._fh.closed:
                return False
            try:
                self._fh.flush()
                try:
                    os.fsync(self._fh.fileno())
                except (OSError, ValueError):
                    # fsync unsupported on some file types (pipes, etc.)
                    pass
                return True
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).debug(
                    "JSONLSpanExporter force_flush failed: %s", e
                )
                return False


class OpenTelemetryConfig:
    """Configure OpenTelemetry + structured logging."""

    def __init__(self, service_name: str = "atlas-ui-3-backend", service_version: str = "1.0.0") -> None:
        self.service_name = service_name
        self.service_version = service_version
        self.is_development = self._is_development()
        self.log_level = self._get_log_level()
        # Resolve logs directory robustly: use config manager
        self.logs_dir = self._get_logs_dir()
        self.log_file = self.logs_dir / "app.jsonl"
        self.spans_file = self.logs_dir / "spans.jsonl"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._span_processor = None
        self._otlp_processor = None
        self._setup_telemetry()
        self._setup_logging()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _get_logs_dir(self) -> Path:
        """Get logs directory from config manager or default to project_root/logs."""
        try:
            from atlas.modules.config import config_manager
            if config_manager.app_settings.app_log_dir:
                return Path(config_manager.app_settings.app_log_dir)
        except Exception:
            # Config manager may not be initialized during early startup or tests.
            # Fall back to default logs directory without logging (avoid circular deps).
            pass
        # Fallback: project_root/logs
        project_root = Path(__file__).resolve().parents[2]
        return project_root / "logs"

    def _is_development(self) -> bool:
        try:
            from atlas.modules.config import config_manager
            settings = config_manager.app_settings
            return (
                settings.debug_mode
                or settings.environment.lower() in {"dev", "development"}
            )
        except Exception:
            # Fallback to environment variables if config not available
            return (
                os.getenv("DEBUG_MODE", "false").lower() == "true"
                or os.getenv("ENVIRONMENT", "production").lower() in {"dev", "development"}
            )

    def _get_log_level(self) -> int:
        try:
            from atlas.modules.config import config_manager
            level_name = config_manager.app_settings.log_level.upper()
        except Exception:
            # Fallback to environment variable if config not available
            level_name = os.getenv("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, None)
        return level if isinstance(level, int) else logging.INFO

    def _setup_telemetry(self) -> None:
        resource = Resource.create(
            {
                SERVICE_NAME: self.service_name,
                SERVICE_VERSION: self.service_version,
                "environment": "development" if self.is_development else "production",
            }
        )
        provider = TracerProvider(resource=resource)

        # File-based JSONL exporter — always on; forms the audit trail
        # consumed by docs/telemetry/analysis_example.py and downstream
        # dashboards.
        jsonl_exporter = JSONLSpanExporter(self.spans_file)
        self._span_processor = BatchSpanProcessor(jsonl_exporter)
        provider.add_span_processor(self._span_processor)

        # Optional OTLP exporter — only when a collector endpoint is configured.
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
                otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                self._otlp_processor = BatchSpanProcessor(otlp_exporter)
                provider.add_span_processor(self._otlp_processor)
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).warning(
                    "OTLP exporter setup failed (endpoint=%s): %s", otlp_endpoint, e
                )

        trace.set_tracer_provider(provider)

    def _setup_logging(self) -> None:
        root = logging.getLogger()
        for h in root.handlers[:]:
            root.removeHandler(h)

        json_formatter = JSONFormatter()
        file_handler = logging.FileHandler(self.log_file, encoding="utf-8")
        file_handler.setFormatter(json_formatter)
        file_handler.setLevel(self.log_level)
        # Restrict app log file perms — structured logs can contain user
        # identifiers, sanitized previews, and error context that shouldn't
        # be world-readable on shared hosts. Best-effort; fails silently on
        # filesystems without POSIX modes.
        try:
            os.chmod(self.log_file, 0o600)
        except OSError:
            pass
        root.addHandler(file_handler)
        root.setLevel(self.log_level)

        # Reduce noise from third-party libraries at INFO.
        # We still want their warnings/errors, and their debug output remains available
        # when LOG_LEVEL=DEBUG.
        if self.log_level > logging.DEBUG:
            for noisy in (
                "httpx",
                "httpcore",
                "LiteLLM",
                "litellm",
            ):
                logging.getLogger(noisy).setLevel(logging.WARNING)

        if self.is_development:
            console = logging.StreamHandler()
            console.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
            console.setLevel(logging.WARNING)
            root.addHandler(console)
            for noisy in (
                "httpx",
                "urllib3.connectionpool",
                "auth_utils",
                "message_processor",
                "session",
                "callbacks",
                "utils",
                "banner_client",
                "middleware",
                "mcp_client",
            ):
                logging.getLogger(noisy).setLevel(logging.DEBUG)

        LoggingInstrumentor().instrument(set_logging_format=False)

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------
    def instrument_fastapi(self, app) -> None:  # noqa: ANN001
        FastAPIInstrumentor.instrument_app(app)

    def instrument_httpx(self) -> None:
        HTTPXClientInstrumentor().instrument()

    def get_log_file_path(self) -> Path:
        return self.log_file

    def get_spans_file_path(self) -> Path:
        return self.spans_file

    def flush_spans(self, timeout_millis: int = 30000) -> bool:
        """Force-flush pending spans to disk/OTLP. Used by tests and shutdown.

        Triggers the BatchSpanProcessor to drain, which in turn calls
        ``JSONLSpanExporter.force_flush`` (fsync) and the OTLP exporter's
        flush. Returns True only when every configured processor reported
        success within ``timeout_millis``.
        """
        ok = True
        if self._span_processor is not None:
            ok = self._span_processor.force_flush(timeout_millis) and ok
        if self._otlp_processor is not None:
            ok = self._otlp_processor.force_flush(timeout_millis) and ok
        return ok

    def shutdown(self, timeout_millis: int = 30000) -> None:
        """Flush and tear down span processors + exporters.

        Safe to call multiple times. Intended for application shutdown hooks
        and test teardown so in-flight spans aren't lost and file handles
        get closed cleanly.
        """
        try:
            self.flush_spans(timeout_millis)
        except Exception:  # noqa: BLE001
            pass
        for proc in (self._span_processor, self._otlp_processor):
            if proc is None:
                continue
            try:
                proc.shutdown()
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).debug(
                    "Span processor shutdown failed: %s", e
                )

    def read_logs(self, lines: int = 100) -> list[Dict[str, Any]]:
        if not self.log_file.exists():
            return []
        out: list[Dict[str, Any]] = []
        try:
            with self.log_file.open("r", encoding="utf-8") as f:
                data = f.readlines()[-lines:]
            for ln in data:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    out.append(json.loads(ln))
                except json.JSONDecodeError:
                    continue
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).error(f"Error reading logs: {e}")
        return out

    def get_log_stats(self) -> Dict[str, Any]:
        if not self.log_file.exists():
            return {"file_exists": False, "file_size": 0, "line_count": 0, "last_modified": None}
        try:
            stat = self.log_file.stat()
            with self.log_file.open("r", encoding="utf-8") as f:
                line_count = sum(1 for _ in f)
            return {
                "file_exists": True,
                "file_size": stat.st_size,
                "line_count": line_count,
                "last_modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "file_path": str(self.log_file),
            }
        except Exception as e:  # noqa: BLE001
            logging.getLogger(__name__).error(f"Error getting log stats: {e}")
            return {"file_exists": True, "error": str(e)}


# Global instance
otel_config: Optional[OpenTelemetryConfig] = None


def setup_opentelemetry(service_name: str = "atlas-ui-3-backend", service_version: str = "1.0.0") -> OpenTelemetryConfig:
    global otel_config
    otel_config = OpenTelemetryConfig(service_name, service_version)
    return otel_config


def get_otel_config() -> Optional[OpenTelemetryConfig]:
    return otel_config
