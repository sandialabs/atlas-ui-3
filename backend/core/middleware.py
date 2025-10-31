"""FastAPI middleware for authentication and logging."""

import logging

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from core.auth import get_user_from_header
from core.capabilities import verify_file_token
from infrastructure.app_factory import app_factory

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to handle authentication and logging."""
    
    def __init__(self, app, debug_mode: bool = False):
        super().__init__(app)
        self.debug_mode = debug_mode
        
    async def dispatch(self, request: Request, call_next) -> Response:
        # Log request
        logger.info(f"Request: {request.method} {request.url.path}")

        # Skip auth for static files and auth endpoint
        if request.url.path.startswith('/static') or request.url.path == '/auth':
            return await call_next(request)

        # Check for capability token in download URLs (allows MCP servers to access files)
        if request.url.path.startswith('/api/files/download/'):
            token = request.query_params.get('token')
            if token:
                claims = verify_file_token(token)
                if claims:
                    # Valid capability token - extract user from token and allow request
                    user_email = claims.get('u')
                    if user_email:
                        logger.info(f"Authenticated via capability token for user: {user_email}")
                        request.state.user_email = user_email
                        return await call_next(request)
                    else:
                        logger.warning("Valid token but missing user email claim")
                else:
                    logger.warning("Invalid capability token provided")

        # Check authentication via X-User-Email header
        user_email = None
        if self.debug_mode:
            # In debug mode, honor X-User-Email header if provided, otherwise use config test user
            x_email_header = request.headers.get('X-User-Email')
            if x_email_header:
                user_email = get_user_from_header(x_email_header)
            else:
                # Get test user from config
                config_manager = app_factory.get_config_manager()
                user_email = config_manager.app_settings.test_user
            # logger.info(f"Debug mode: using user {user_email}")
        else:
            x_email_header = request.headers.get('X-User-Email')
            user_email = get_user_from_header(x_email_header)

            if not user_email:
                # Distinguish between API endpoints (return 401) and browser endpoints (redirect)
                if request.url.path.startswith('/api/'):
                    logger.warning(f"Missing authentication for API endpoint: {request.url.path}")
                    raise HTTPException(status_code=401, detail="Unauthorized")
                else:
                    logger.warning("Missing X-User-Email, redirecting to auth")
                    return RedirectResponse(url="/auth", status_code=302)

        # Add user to request state
        request.state.user_email = user_email

        response = await call_next(request)
        return response