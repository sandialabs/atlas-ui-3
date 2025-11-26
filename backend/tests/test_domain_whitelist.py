"""Tests for domain whitelist middleware."""

import json
import pytest
import tempfile
from pathlib import Path
from fastapi import FastAPI

from core.domain_whitelist_middleware import DomainWhitelistMiddleware
from core.domain_whitelist import DomainWhitelistManager


@pytest.fixture
def temp_config():
    """Create a temporary config file for testing."""
    config_data = {
        "version": "1.0",
        "description": "Test config",
        "enabled": True,
        "domains": [
            {"domain": "sandia.gov", "description": "Sandia National Labs"},
            {"domain": "doe.gov", "description": "DOE"},
            {"domain": "example.org", "description": "Example"},
        ],
        "subdomain_matching": True
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config_data, f)
        temp_path = Path(f.name)
    
    yield temp_path
    
    # Cleanup
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def disabled_config():
    """Create a config file with whitelist disabled."""
    config_data = {
        "version": "1.0",
        "description": "Disabled config",
        "enabled": False,
        "domains": [
            {"domain": "sandia.gov", "description": "Sandia National Labs"},
        ],
        "subdomain_matching": True
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(config_data, f)
        temp_path = Path(f.name)
    
    yield temp_path
    
    if temp_path.exists():
        temp_path.unlink()


class TestDomainWhitelistManager:
    """Test the domain whitelist manager."""

    def test_load_config(self, temp_config):
        """Test loading configuration from file."""
        manager = DomainWhitelistManager(config_path=temp_config)
        
        assert manager.is_enabled() is True
        assert "sandia.gov" in manager.get_domains()
        assert "doe.gov" in manager.get_domains()
        assert "example.org" in manager.get_domains()
        assert len(manager.get_domains()) == 3

    def test_disabled_config(self, disabled_config):
        """Test that disabled config doesn't enforce whitelist."""
        manager = DomainWhitelistManager(config_path=disabled_config)
        
        assert manager.is_enabled() is False
        # Even though disabled, should allow all
        assert manager.is_domain_allowed("user@gmail.com") is True

    def test_domain_matching(self, temp_config):
        """Test domain matching logic."""
        manager = DomainWhitelistManager(config_path=temp_config)
        
        # Exact matches
        assert manager.is_domain_allowed("user@sandia.gov") is True
        assert manager.is_domain_allowed("user@doe.gov") is True
        
        # Subdomain matches
        assert manager.is_domain_allowed("user@mail.sandia.gov") is True
        assert manager.is_domain_allowed("user@sub.doe.gov") is True
        
        # Invalid domains
        assert manager.is_domain_allowed("user@gmail.com") is False
        assert manager.is_domain_allowed("user@sandia.com") is False  # Wrong TLD

    def test_invalid_email(self, temp_config):
        """Test handling of invalid email addresses."""
        manager = DomainWhitelistManager(config_path=temp_config)
        
        assert manager.is_domain_allowed("notanemail") is False
        assert manager.is_domain_allowed("") is False
        assert manager.is_domain_allowed("no-at-sign.com") is False


@pytest.fixture
def create_middleware():
    """Factory fixture to create middleware with custom config."""
    from starlette.middleware.base import BaseHTTPMiddleware
    
    def _create(config_path):
        app = FastAPI()
        
        # Monkey-patch to use custom config
        original_init = DomainWhitelistMiddleware.__init__
        def patched_init(self, app, auth_redirect_url="/auth"):
            BaseHTTPMiddleware.__init__(self, app)
            self.auth_redirect_url = auth_redirect_url
            self.whitelist_manager = DomainWhitelistManager(config_path=config_path)
        
        DomainWhitelistMiddleware.__init__ = patched_init
        middleware = DomainWhitelistMiddleware(app)
        DomainWhitelistMiddleware.__init__ = original_init
        
        return middleware
    
    return _create


class TestDomainWhitelistMiddleware:
    """Test domain whitelist middleware."""

    def test_middleware_with_allowed_domain(self, temp_config, create_middleware):
        """Test that allowed domains pass through."""
        from starlette.requests import Request
        from starlette.responses import Response
        
        middleware = create_middleware(temp_config)
        
        async def call_next(request):
            return Response("OK", status_code=200)
        
        async def test_request():
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/test",
                "query_string": b"",
                "headers": [],
                "state": {},
            }
            request = Request(scope)
            request.state.user_email = "test@sandia.gov"
            
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200
        
        import asyncio
        asyncio.run(test_request())

    def test_middleware_with_disallowed_domain(self, temp_config, create_middleware):
        """Test that disallowed domains are blocked."""
        from starlette.requests import Request
        from starlette.responses import Response
        
        middleware = create_middleware(temp_config)
        
        async def call_next(request):
            return Response("OK", status_code=200)
        
        async def test_request():
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/test",
                "query_string": b"",
                "headers": [],
                "state": {},
            }
            request = Request(scope)
            request.state.user_email = "test@gmail.com"
            
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 403
        
        import asyncio
        asyncio.run(test_request())

    def test_middleware_disabled(self, disabled_config, create_middleware):
        """Test that disabled config allows all domains."""
        from starlette.requests import Request
        from starlette.responses import Response
        
        middleware = create_middleware(disabled_config)
        
        async def call_next(request):
            return Response("OK", status_code=200)
        
        async def test_request():
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/test",
                "query_string": b"",
                "headers": [],
                "state": {},
            }
            request = Request(scope)
            request.state.user_email = "test@gmail.com"
            
            # Should pass even though gmail.com is not in whitelist
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200
        
        import asyncio
        asyncio.run(test_request())

    def test_health_endpoint_bypass(self, temp_config, create_middleware):
        """Test that health endpoint bypasses whitelist check."""
        from starlette.requests import Request
        from starlette.responses import Response
        
        middleware = create_middleware(temp_config)
        
        async def call_next(request):
            return Response("OK", status_code=200)
        
        async def test_request():
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/health",
                "query_string": b"",
                "headers": [],
                "state": {},
            }
            request = Request(scope)
            # No email - should still pass for health check
            
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200
        
        import asyncio
        asyncio.run(test_request())
