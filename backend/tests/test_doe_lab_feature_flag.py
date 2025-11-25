"""Integration test for DOE lab middleware feature flag."""

import pytest
from fastapi import FastAPI, Request
from starlette.testclient import TestClient
from starlette.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware

from core.doe_lab_middleware import DOELabMiddleware


class SimpleAuthMiddleware(BaseHTTPMiddleware):
    """Simplified auth middleware for testing that just sets user_email in state."""
    
    def __init__(self, app, debug_mode: bool = False):
        super().__init__(app)
        self.debug_mode = debug_mode
    
    async def dispatch(self, request: Request, call_next) -> Response:
        # Simulate setting user_email from header (like real AuthMiddleware)
        email = request.headers.get("X-User-Email")
        if email:
            request.state.user_email = email
        elif self.debug_mode:
            request.state.user_email = "test@test.com"
        
        return await call_next(request)


def test_doe_middleware_not_active_when_disabled():
    """Test that DOE middleware allows non-DOE emails when not added to app."""
    # Create app without DOE middleware
    app = FastAPI()
    
    @app.get("/api/test")
    def test_endpoint(request: Request):
        return {"user": getattr(request.state, "user_email", "none")}
    
    # Add only simple auth middleware (which sets user_email in state)
    app.add_middleware(SimpleAuthMiddleware, debug_mode=True)
    
    # DOE middleware NOT added (simulating feature flag disabled)
    
    # Create client
    client = TestClient(app)
    
    # Test with non-DOE email - should pass because DOE middleware is not active
    response = client.get("/api/test", headers={"X-User-Email": "test@gmail.com"})
    assert response.status_code == 200
    assert response.json()["user"] == "test@gmail.com"


def test_doe_middleware_active_when_enabled():
    """Test that DOE middleware blocks non-DOE emails when added to app."""
    # Create app with DOE middleware
    app = FastAPI()
    
    @app.get("/api/test")
    def test_endpoint(request: Request):
        return {"user": request.state.user_email}
    
    @app.get("/auth")
    def auth_endpoint():
        return {"login": True}
    
    # Add DOE middleware first (will run second after auth)
    app.add_middleware(DOELabMiddleware)
    
    # Add auth middleware second (will run first, setting email)
    app.add_middleware(SimpleAuthMiddleware, debug_mode=True)
    
    # Create client
    client = TestClient(app)
    
    # Test with non-DOE email - should be rejected
    response = client.get("/api/test", headers={"X-User-Email": "test@gmail.com"})
    assert response.status_code == 403
    assert "Access restricted" in response.json()["detail"]
    
    # Test with valid DOE email - should pass
    response = client.get("/api/test", headers={"X-User-Email": "test@sandia.gov"})
    assert response.status_code == 200
    assert response.json()["user"] == "test@sandia.gov"


def test_middleware_ordering_auth_before_doe():
    """Test that auth middleware must run before DOELabMiddleware."""
    # This test verifies the correct middleware ordering
    app = FastAPI()
    
    @app.get("/api/test")
    def test_endpoint(request: Request):
        return {"user": request.state.user_email}
    
    # Add DOE first, Auth second (so Auth runs first in request flow)
    app.add_middleware(DOELabMiddleware)
    app.add_middleware(SimpleAuthMiddleware, debug_mode=True)
    
    client = TestClient(app)
    
    # Auth middleware should set the email, then DOELabMiddleware should check it
    response = client.get("/api/test", headers={"X-User-Email": "test@lanl.gov"})
    assert response.status_code == 200
    assert response.json()["user"] == "test@lanl.gov"


