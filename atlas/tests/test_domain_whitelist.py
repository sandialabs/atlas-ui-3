"""Tests for domain whitelist middleware."""

import json
import pytest
import tempfile
from pathlib import Path
from fastapi import FastAPI

from atlas.core.domain_whitelist_middleware import DomainWhitelistMiddleware
from atlas.core.domain_whitelist import DomainWhitelistManager


@pytest.fixture
def temp_config():
    """Create a temporary config file for testing."""
    config_data = {
        "version": "1.0",
        "description": "Test config",
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



class TestDomainWhitelistManager:
    """Test the domain whitelist manager."""

    def test_load_config(self, temp_config):
        """Test loading configuration from file."""
        manager = DomainWhitelistManager(config_path=temp_config)
        
        assert "sandia.gov" in manager.get_domains()
        assert "doe.gov" in manager.get_domains()
        assert "example.org" in manager.get_domains()
        assert len(manager.get_domains()) == 3

    def test_missing_config_file(self):
        """Test that missing config file allows all domains (fail open)."""
        non_existent_path = Path("/tmp/nonexistent_whitelist_config_12345.json")
        manager = DomainWhitelistManager(config_path=non_existent_path)
        
        # Config should not be loaded
        assert manager.config_loaded is False
        assert len(manager.get_domains()) == 0
        
        # But should allow all domains (fail open)
        assert manager.is_domain_allowed("user@gmail.com") is True
        assert manager.is_domain_allowed("user@any-domain.com") is True
        assert manager.is_domain_allowed("test@example.org") is True

    def test_invalid_json_config(self):
        """Test that invalid JSON config allows all domains (fail open)."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("{ invalid json content ]}")
            temp_path = Path(f.name)
        
        try:
            manager = DomainWhitelistManager(config_path=temp_path)
            
            # Config should not be loaded
            assert manager.config_loaded is False
            assert len(manager.get_domains()) == 0
            
            # Should allow all domains (fail open)
            assert manager.is_domain_allowed("user@gmail.com") is True
            assert manager.is_domain_allowed("test@example.org") is True
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_empty_domains_list(self):
        """Test config with empty domains list."""
        config_data = {
            "version": "1.0",
            "description": "Empty config",
            "domains": [],
            "subdomain_matching": True
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            temp_path = Path(f.name)
        
        try:
            manager = DomainWhitelistManager(config_path=temp_path)
            
            # Config should be loaded successfully even with empty domains
            assert manager.config_loaded is True
            assert len(manager.get_domains()) == 0
            
            # Should block all domains when config is valid but empty
            assert manager.is_domain_allowed("user@gmail.com") is False
            assert manager.is_domain_allowed("user@sandia.gov") is False
        finally:
            if temp_path.exists():
                temp_path.unlink()

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

    def test_middleware_with_missing_config(self, create_middleware):
        """Test that middleware with missing config allows all domains."""
        from starlette.requests import Request
        from starlette.responses import Response
        
        non_existent_path = Path("/tmp/nonexistent_whitelist_config_12345.json")
        middleware = create_middleware(non_existent_path)
        
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
            
            # Should pass even though config is missing (fail open)
            response = await middleware.dispatch(request, call_next)
            assert response.status_code == 200
        
        import asyncio
        asyncio.run(test_request())

    def test_middleware_with_invalid_config(self, create_middleware):
        """Test that middleware with invalid config allows all domains."""
        from starlette.requests import Request
        from starlette.responses import Response
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("{ invalid json }")
            temp_path = Path(f.name)
        
        try:
            middleware = create_middleware(temp_path)
            
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
                request.state.user_email = "test@anydomain.com"
                
                # Should pass even though config is invalid (fail open)
                response = await middleware.dispatch(request, call_next)
                assert response.status_code == 200
            
            import asyncio
            asyncio.run(test_request())
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def test_middleware_with_empty_domains(self, create_middleware):
        """Test that middleware with empty domains list blocks all."""
        from starlette.requests import Request
        from starlette.responses import Response
        
        config_data = {
            "version": "1.0",
            "description": "Empty config",
            "domains": [],
            "subdomain_matching": True
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config_data, f)
            temp_path = Path(f.name)
        
        try:
            middleware = create_middleware(temp_path)
            
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
                request.state.user_email = "test@anydomain.com"
                
                # Should block because empty domains is a valid config
                response = await middleware.dispatch(request, call_next)
                assert response.status_code == 403
            
            import asyncio
            asyncio.run(test_request())
        finally:
            if temp_path.exists():
                temp_path.unlink()
