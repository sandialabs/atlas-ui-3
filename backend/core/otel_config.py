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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor


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


class OpenTelemetryConfig:
    """Configure OpenTelemetry + structured logging."""

    def __init__(self, service_name: str = "atlas-ui-3-backend", service_version: str = "1.0.0") -> None:
        self.service_name = service_name
        self.service_version = service_version
        self.is_development = self._is_development()
        self.log_level = self._get_log_level()
        # Resolve logs directory robustly: explicit env override else project_root/logs
        if os.getenv("APP_LOG_DIR"):
            self.logs_dir = Path(os.getenv("APP_LOG_DIR"))
        else:
            # This file: backend/core/otel_config.py -> project root is 2 levels up
            project_root = Path(__file__).resolve().parents[2]
            self.logs_dir = project_root / "logs"
        self.log_file = self.logs_dir / "app.jsonl"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self._setup_telemetry()
        self._setup_logging()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _is_development(self) -> bool:
        return (
            os.getenv("DEBUG_MODE", "false").lower() == "true"
            or os.getenv("ENVIRONMENT", "production").lower() in {"dev", "development"}
        )

    def _get_log_level(self) -> int:
        try:
            from config import config_manager  # type: ignore  # local import to avoid circular

            level_name = getattr(config_manager.app_settings, "log_level", "INFO").upper()
        except Exception:  # noqa: BLE001
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
        trace.set_tracer_provider(TracerProvider(resource=resource))

    def _setup_logging(self) -> None:
        root = logging.getLogger()
        for h in root.handlers[:]:
            root.removeHandler(h)

        json_formatter = JSONFormatter()
        file_handler = logging.FileHandler(self.log_file, encoding="utf-8")
        file_handler.setFormatter(json_formatter)
        file_handler.setLevel(self.log_level)
        root.addHandler(file_handler)
        root.setLevel(self.log_level)

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