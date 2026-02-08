"""Email domain whitelist validation middleware.

This middleware enforces that users must have email addresses from whitelisted
domains. Enabled/disabled via the FEATURE_DOMAIN_WHITELIST_ENABLED feature flag.
Domain list is loaded from atlas.domain-whitelist.json.
"""

import logging

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse, Response

from atlas.core.domain_whitelist import DomainWhitelistManager

logger = logging.getLogger(__name__)


class DomainWhitelistMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce email domain whitelist restrictions."""

    def __init__(self, app, auth_redirect_url: str = "/auth"):
        """Initialize domain whitelist middleware.

        Args:
            app: ASGI application
            auth_redirect_url: URL to redirect to on auth failure (default: /auth)
        """
        super().__init__(app)
        self.auth_redirect_url = auth_redirect_url
        self.whitelist_manager = DomainWhitelistManager()

        logger.info(f"Domain whitelist middleware loaded: {len(self.whitelist_manager.get_domains())} domains (config_loaded={self.whitelist_manager.config_loaded})")

    async def dispatch(self, request: Request, call_next) -> Response:
        """Check if user email is from a whitelisted domain.

        Args:
            request: Incoming HTTP request
            call_next: Next middleware/handler in chain

        Returns:
            Response from next handler if authorized, or 403/redirect if not
        """
        # Skip check for health endpoint and auth redirect endpoint
        if request.url.path == '/api/health' or request.url.path == self.auth_redirect_url:
            return await call_next(request)

        # Get email from request state (set by AuthMiddleware)
        email = getattr(request.state, "user_email", None)

        if not email or "@" not in email:
            logger.warning("Domain whitelist check failed: missing or invalid email")
            return self._unauthorized_response(request, "User email required")

        # Check if domain is allowed
        if not self.whitelist_manager.is_domain_allowed(email):
            domain = email.split("@", 1)[1].lower()
            logger.warning(f"Domain whitelist check failed: unauthorized domain {domain}")
            return self._unauthorized_response(
                request,
                "Access restricted to whitelisted domains"
            )

        return await call_next(request)

    def _unauthorized_response(self, request: Request, detail: str) -> Response:
        """Return appropriate unauthorized response based on endpoint type.

        Args:
            request: Incoming HTTP request
            detail: Error detail message

        Returns:
            JSONResponse for API endpoints, RedirectResponse for others
        """
        if request.url.path.startswith('/api/'):
            return JSONResponse(
                status_code=403,
                content={"detail": detail}
            )
        return RedirectResponse(url=self.auth_redirect_url, status_code=302)
