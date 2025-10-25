"""Simple in-memory rate limit middleware.

Fixed-window counter per client IP (and optionally per-path) to throttle requests.
This is a lightweight safeguard suitable for single-process deployments and tests.

Configuration is sourced from ConfigManager (AppSettings) with optional env overrides:
    - app_settings.rate_limit_rpm            (env: RATE_LIMIT_RPM, default: 600)
    - app_settings.rate_limit_window_seconds (env: RATE_LIMIT_WINDOW_SECONDS, default: 60)
    - app_settings.rate_limit_per_path       (env: RATE_LIMIT_PER_PATH, default: false)
"""

import time
import typing as t

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response
from modules.config import config_manager


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app) -> None:
        super().__init__(app)
        settings = config_manager.app_settings
        # Pull from centralized config with sane defaults
        self.window_seconds = int(getattr(settings, "rate_limit_window_seconds", 60))
        self.max_requests = int(getattr(settings, "rate_limit_rpm", 600))
        self.per_path = bool(getattr(settings, "rate_limit_per_path", False))
        # state: key -> (window_start_epoch, count)
        self._buckets: dict[str, t.Tuple[int, int]] = {}

    def _key_for(self, request: Request) -> str:
        client_ip = getattr(request.client, "host", "unknown") if request.client else "unknown"
        if self.per_path:
            return f"{client_ip}:{request.url.path}"
        return client_ip

    async def dispatch(self, request: Request, call_next) -> Response:
        now = int(time.time())
        key = self._key_for(request)
        win = self.window_seconds
        start, count = self._buckets.get(key, (now, 0))

        # Move window if expired
        if now - start >= win:
            start, count = now, 0

        count += 1
        self._buckets[key] = (start, count)

        if count > self.max_requests:
            retry_after = max(1, win - (now - start))
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Please try again later.",
                    "limit": self.max_requests,
                    "window_seconds": self.window_seconds,
                },
                headers={"Retry-After": str(retry_after)},
            )

        return await call_next(request)
