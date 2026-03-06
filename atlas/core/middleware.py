"""FastAPI middleware for authentication and logging."""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from atlas.core.auth import get_user_from_aws_alb_jwt, get_user_from_header
from atlas.core.capabilities import verify_file_token
from atlas.infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to handle authentication and logging."""

    def __init__(
        self,
        app,
        debug_mode: bool = False,
        auth_header_name: str = "X-User-Email",
        auth_header_type: str = "email-string",
        auth_aws_expected_alb_arn: str = "",
        auth_aws_region: str = "us-east-1",
        proxy_secret_enabled: bool = False,
        proxy_secret_header: str = "X-Proxy-Secret",
        proxy_secret: str = None,
        auth_redirect_url: str = "/auth"
    ):
        super().__init__(app)
        self.debug_mode = debug_mode
        self.auth_header_name = auth_header_name
        self.auth_header_type = auth_header_type
        self.auth_aws_expected_alb_arn = auth_aws_expected_alb_arn
        self.auth_aws_region = auth_aws_region
        self.proxy_secret_enabled = proxy_secret_enabled
        self.proxy_secret_header = proxy_secret_header
        self.proxy_secret = proxy_secret
        self.auth_redirect_url = auth_redirect_url

    async def dispatch(self, request: Request, call_next) -> Response:
        # Log request
        logger.debug("Request: %s %s", request.method, request.url.path)

        # Skip auth for static files, health/heartbeat check, configured auth endpoint,
        # and Globus OAuth browser routes (login redirect and callback need to work pre-auth)
        if (request.url.path.startswith('/static') or
            request.url.path == '/api/health' or
            request.url.path == '/api/heartbeat' or
            request.url.path.startswith('/auth/globus/') or
            request.url.path == self.auth_redirect_url):
            return await call_next(request)

        # Validate proxy secret if enabled (skip in debug mode for local development)
        if self.proxy_secret_enabled and self.proxy_secret and not self.debug_mode:
            proxy_secret_value = request.headers.get(self.proxy_secret_header)

            if not proxy_secret_value or proxy_secret_value != self.proxy_secret:
                logger.warning(f"Invalid or missing proxy secret for {request.url.path}")
                # Distinguish between API endpoints (return 401) and browser endpoints (redirect)
                if request.url.path.startswith('/api/'):
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Unauthorized: Invalid proxy secret"}
                    )
                else:
                    return RedirectResponse(url=self.auth_redirect_url, status_code=302)

        # Check for capability token in download URLs (allows MCP servers to access files)
        if request.url.path.startswith('/api/files/download/'):
            token = request.query_params.get('token')
            if token:
                claims = verify_file_token(token)
                if claims:
                    # Valid capability token - extract user from token and allow request
                    # Note: We only validate token authenticity here (authentication).
                    # The route handler validates that token's file key matches the requested
                    # file (authorization). This separation of concerns keeps middleware focused
                    # on authentication while route handlers handle resource-specific authorization.
                    user_email = claims.get('u')
                    if user_email:
                        logger.debug("Authenticated via capability token for user: %s", user_email)
                        request.state.user_email = user_email
                        return await call_next(request)
                    else:
                        logger.warning("Valid token but missing user email claim")
                else:
                    logger.warning("Invalid capability token provided")

        # Check authentication via configured header (default: X-User-Email)
        user_email = None
        if self.debug_mode:
            # In debug mode, honor auth header if provided, otherwise use config test user
            x_auth_header = request.headers.get(self.auth_header_name)
            if x_auth_header:
                # Apply same authentication logic as production for testing
                if self.auth_header_type == "aws-alb-jwt":
                    user_email = get_user_from_aws_alb_jwt(x_auth_header, self.auth_aws_expected_alb_arn, self.auth_aws_region)
                else:
                    user_email = get_user_from_header(x_auth_header)
            else:
                # Get test user from config
                config_manager = app_factory.get_config_manager()
                user_email = config_manager.app_settings.test_user
            # logger.info(f"Debug mode: using user {user_email}")
        else:
            x_auth_header = request.headers.get(self.auth_header_name)

            # Extract the user's email, depending on the datatype of auth header
            if self.auth_header_type == "aws-alb-jwt": # Amazon Application Load Balancer
                user_email = get_user_from_aws_alb_jwt(x_auth_header, self.auth_aws_expected_alb_arn, self.auth_aws_region)
            else:
                user_email = get_user_from_header(x_auth_header)

            if not user_email:
                # Distinguish between API endpoints (return 401) and browser endpoints (redirect)
                if request.url.path.startswith('/api/'):
                    logger.warning(f"Missing authentication for API endpoint: {request.url.path}")
                    return JSONResponse(
                        status_code=401,
                        content={"detail": "Unauthorized"}
                    )
                else:
                    logger.warning(f"Missing {self.auth_header_name}, redirecting to {self.auth_redirect_url}")
                    return RedirectResponse(url=self.auth_redirect_url, status_code=302)

        # Add user to request state
        request.state.user_email = user_email

        response = await call_next(request)
        return response
