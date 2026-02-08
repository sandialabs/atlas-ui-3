"""Security headers middleware with ConfigManager-based toggles.

Sets common security headers:
 - Content-Security-Policy (CSP)
 - X-Frame-Options (XFO)
 - X-Content-Type-Options: nosniff
 - Referrer-Policy

Each header is individually togglable via AppSettings. HSTS is intentionally omitted.
"""

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from starlette.types import ASGIApp

from atlas.modules.config import config_manager


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        settings = config_manager.app_settings

        # X-Content-Type-Options
        if getattr(settings, "security_nosniff_enabled", True):
            if "X-Content-Type-Options" not in response.headers:
                response.headers["X-Content-Type-Options"] = "nosniff"

        # X-Frame-Options
        if getattr(settings, "security_xfo_enabled", True):
            xfo_value = getattr(settings, "security_xfo_value", "SAMEORIGIN")
            if "X-Frame-Options" not in response.headers:
                response.headers["X-Frame-Options"] = xfo_value

        # Referrer-Policy
        if getattr(settings, "security_referrer_policy_enabled", True):
            ref_value = getattr(settings, "security_referrer_policy_value", "no-referrer")
            if "Referrer-Policy" not in response.headers:
                response.headers["Referrer-Policy"] = ref_value

        # Content-Security-Policy
        if getattr(settings, "security_csp_enabled", True):
            csp_value = getattr(settings, "security_csp_value", None)
            if csp_value and "Content-Security-Policy" not in response.headers:
                # Inject WebSocket origins for the configured port so that
                # non-default ports (e.g. worktrees on 8004) are allowed.
                port = getattr(settings, "port", 8000)
                if port not in (80, 443):
                    ws_origins = f"ws://localhost:{port} wss://localhost:{port}"
                    csp_value = _inject_ws_origins(csp_value, ws_origins)
                response.headers["Content-Security-Policy"] = csp_value

        return response


def _inject_ws_origins(csp: str, ws_origins: str) -> str:
    """Append WebSocket origins to the connect-src CSP directive.

    Parses the CSP directives by splitting on ';' so that injection works
    regardless of spacing or ordering in the CSP string.
    """
    directives = [d.strip() for d in csp.split(";")]
    updated = []
    injected = False
    for directive in directives:
        if directive.startswith("connect-src"):
            directive = f"{directive} {ws_origins}"
            injected = True
        updated.append(directive)
    if not injected:
        updated.append(f"connect-src {ws_origins}")
    return "; ".join(updated)
