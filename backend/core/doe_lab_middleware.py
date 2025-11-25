"""DOE lab email domain validation middleware.

This middleware enforces that users must have email addresses from DOE, NNSA,
or DOE national laboratory domains. It can be enabled/disabled via the
FEATURE_DOE_LAB_CHECK_ENABLED feature flag.
"""

import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse, Response

logger = logging.getLogger(__name__)


class DOELabMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce DOE/NNSA/DOE lab email domain restrictions."""
    
    # Comprehensive list of DOE, NNSA, and DOE national laboratory domains
    DOE_LAB_DOMAINS = frozenset([
        # HQ / NNSA / DOE-wide
        "doe.gov", "nnsa.doe.gov", "hq.doe.gov",
        # National labs (broad coverage)
        "anl.gov", "bnl.gov", "fnal.gov", "inl.gov", "lbl.gov", "lanl.gov",
        "llnl.gov", "ornl.gov", "pnnl.gov", "sandia.gov", "srnl.doe.gov",
        "ameslab.gov", "jlab.org", "princeton.edu", "slac.stanford.edu",
        "pppl.gov", "nrel.gov", "netl.doe.gov", "stanford.edu",
    ])
    
    def __init__(self, app, auth_redirect_url: str = "/auth"):
        """Initialize DOE lab middleware.
        
        Args:
            app: ASGI application
            auth_redirect_url: URL to redirect to on auth failure (default: /auth)
        """
        super().__init__(app)
        self.auth_redirect_url = auth_redirect_url
    
    async def dispatch(self, request: Request, call_next) -> Response:
        """Check if user email is from DOE/lab domain.
        
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
            logger.warning(f"DOE check failed: missing or invalid email")
            return self._unauthorized_response(request, "User email required")
        
        # Extract domain and check against allowed list
        domain = email.split("@", 1)[1].lower()
        if not self._is_doe_domain(domain):
            logger.warning(f"DOE check failed: unauthorized domain {domain}")
            return self._unauthorized_response(
                request, 
                "Access restricted to DOE / NNSA / DOE labs"
            )
        
        return await call_next(request)
    
    def _is_doe_domain(self, domain: str) -> bool:
        """Check if domain is a DOE/lab domain or subdomain.
        
        Args:
            domain: Email domain to check
            
        Returns:
            True if domain is authorized, False otherwise
        """
        # Direct match or subdomain match (e.g., foo.sandia.gov matches sandia.gov)
        return any(domain == d or domain.endswith("." + d) for d in self.DOE_LAB_DOMAINS)
    
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
