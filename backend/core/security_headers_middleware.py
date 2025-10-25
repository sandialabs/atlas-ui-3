"""Security headers middleware with ConfigManager-based toggles.

Sets common security headers:
 - Content-Security-Policy (CSP)
 - X-Frame-Options (XFO)
 - X-Content-Type-Options: nosniff
 - Referrer-Policy

Each header is individually togglable via AppSettings. HSTS is intentionally omitted.
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
from starlette.responses import Response
from fastapi import Request

from modules.config import config_manager


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        self.settings = config_manager.app_settings

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)

        # X-Content-Type-Options
        if getattr(self.settings, "security_nosniff_enabled", True):
            if "X-Content-Type-Options" not in response.headers:
                response.headers["X-Content-Type-Options"] = "nosniff"

        # X-Frame-Options
        if getattr(self.settings, "security_xfo_enabled", True):
            xfo_value = getattr(self.settings, "security_xfo_value", "SAMEORIGIN")
            if "X-Frame-Options" not in response.headers:
                response.headers["X-Frame-Options"] = xfo_value

        # Referrer-Policy
        if getattr(self.settings, "security_referrer_policy_enabled", True):
            ref_value = getattr(self.settings, "security_referrer_policy_value", "no-referrer")
            if "Referrer-Policy" not in response.headers:
                response.headers["Referrer-Policy"] = ref_value

        # Content-Security-Policy
        if getattr(self.settings, "security_csp_enabled", True):
            csp_value = getattr(self.settings, "security_csp_value", None)
            if csp_value and "Content-Security-Policy" not in response.headers:
                response.headers["Content-Security-Policy"] = csp_value

        return response
