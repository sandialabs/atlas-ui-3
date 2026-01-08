"""
Tests for BACKEND_PUBLIC_URL configuration and absolute URL generation.

This module tests that the file download URL generation correctly handles:
1. Relative URLs when BACKEND_PUBLIC_URL is not configured (backward compatibility)
2. Absolute URLs when BACKEND_PUBLIC_URL is configured (remote MCP server support)
3. Proper token generation and validation
"""

import os
import pytest
from unittest.mock import patch, MagicMock

from core.capabilities import create_download_url, generate_file_token


def create_mock_settings(backend_public_url=None):
    """Helper to create a properly mocked settings object."""
    mock_settings = MagicMock()
    mock_settings.backend_public_url = backend_public_url
    mock_settings.capability_token_secret = "test-secret-key-for-testing"
    mock_settings.capability_token_ttl_seconds = 3600
    return mock_settings


class TestBackendPublicUrlConfiguration:
    """Test suite for BACKEND_PUBLIC_URL configuration behavior."""
    
    def test_relative_url_without_backend_public_url(self):
        """Test that relative URLs are generated when BACKEND_PUBLIC_URL is not set."""
        mock_settings = create_mock_settings(backend_public_url=None)
        
        with patch('core.capabilities.config_manager') as mock_cm:
            mock_cm.app_settings = mock_settings
            
            url = create_download_url("test-key-123", "user@example.com")
            
            # Should start with / (relative URL)
            assert url.startswith("/api/files/download/")
            assert "test-key-123" in url
            assert "token=" in url
            assert not url.startswith("http")
    
    def test_absolute_url_with_backend_public_url(self):
        """Test that absolute URLs are generated when BACKEND_PUBLIC_URL is configured."""
        mock_settings = create_mock_settings(backend_public_url="https://atlas.example.com")
        
        with patch('core.capabilities.config_manager') as mock_cm:
            mock_cm.app_settings = mock_settings
            
            url = create_download_url("test-key-456", "admin@example.com")
            
            # Should be absolute URL
            assert url.startswith("https://atlas.example.com/api/files/download/")
            assert "test-key-456" in url
            assert "token=" in url
    
    def test_absolute_url_strips_trailing_slash(self):
        """Test that trailing slashes in BACKEND_PUBLIC_URL are handled correctly."""
        mock_settings = create_mock_settings(backend_public_url="https://atlas.example.com/")
        
        with patch('core.capabilities.config_manager') as mock_cm:
            mock_cm.app_settings = mock_settings
            
            url = create_download_url("test-key-789", "user@example.com")
            
            # Should not have double slash
            assert "https://atlas.example.com/api/files/download/" in url
            assert "https://atlas.example.com//api" not in url
    
    def test_url_with_non_standard_port(self):
        """Test absolute URL generation with non-standard port."""
        mock_settings = create_mock_settings(backend_public_url="https://atlas.example.com:8443")
        
        with patch('core.capabilities.config_manager') as mock_cm:
            mock_cm.app_settings = mock_settings
            
            url = create_download_url("test-key-abc", "user@example.com")
            
            # Should include port in URL
            assert url.startswith("https://atlas.example.com:8443/api/files/download/")
    
    def test_url_with_localhost(self):
        """Test absolute URL generation with localhost (development mode)."""
        mock_settings = create_mock_settings(backend_public_url="http://localhost:8000")
        
        with patch('core.capabilities.config_manager') as mock_cm:
            mock_cm.app_settings = mock_settings
            
            url = create_download_url("test-key-dev", "dev@example.com")
            
            # Should use localhost URL
            assert url.startswith("http://localhost:8000/api/files/download/")
    
    def test_fallback_without_user_email(self):
        """Test URL generation without user email (no token)."""
        mock_settings = create_mock_settings(backend_public_url="https://atlas.example.com")
        
        with patch('core.capabilities.config_manager') as mock_cm:
            mock_cm.app_settings = mock_settings
            
            url = create_download_url("test-key-nouser", None)
            
            # Should be absolute but without token
            assert url.startswith("https://atlas.example.com/api/files/download/")
            assert "test-key-nouser" in url
            assert "token=" not in url
    
    def test_config_error_falls_back_to_relative(self):
        """Test that configuration errors fall back to relative URLs gracefully."""
        # Mock config manager app_settings to raise exception when accessed
        mock_cm = MagicMock()
        # When app_settings is accessed, raise exception
        type(mock_cm).app_settings = property(lambda self: (_ for _ in ()).throw(Exception("Config error")))
        
        # Also need to mock _get_secret since it also accesses config_manager
        with patch('core.capabilities.config_manager', mock_cm):
            with patch('core.capabilities._get_secret', return_value=b'test-secret'):
                url = create_download_url("test-key-error", "user@example.com")
                
                # Should fall back to relative URL
                assert url.startswith("/api/files/download/")
                assert not url.startswith("http")


class TestTokenValidation:
    """Test suite for token generation and validation with absolute URLs."""
    
    def test_token_works_with_absolute_urls(self):
        """Test that tokens generated for absolute URLs are valid."""
        from core.capabilities import verify_file_token
        
        user_email = "test@example.com"
        file_key = "test-key-123"
        
        mock_settings = create_mock_settings()
        with patch('core.capabilities.config_manager') as mock_cm:
            mock_cm.app_settings = mock_settings
            
            # Generate token
            token = generate_file_token(user_email, file_key, ttl_seconds=60)
            
            # Verify token
            claims = verify_file_token(token)
            
            assert claims is not None
            assert claims["u"] == user_email
            assert claims["k"] == file_key
            assert claims["e"] > 0  # Expiry timestamp exists


class TestBackwardCompatibility:
    """Test suite to ensure backward compatibility with existing behavior."""
    
    def test_stdio_servers_still_work_with_relative_urls(self):
        """Test that stdio (local) servers continue to work with relative URLs."""
        mock_settings = create_mock_settings(backend_public_url=None)
        
        with patch('core.capabilities.config_manager') as mock_cm:
            mock_cm.app_settings = mock_settings
            
            url = create_download_url("local-file", "local@example.com")
            
            # Local servers can resolve relative URLs
            assert url.startswith("/api/files/download/")
    
    def test_existing_mcp_servers_unaffected(self):
        """Test that existing MCP server configurations continue to work."""
        # This test verifies that the changes don't break existing deployments
        # that haven't configured BACKEND_PUBLIC_URL
        
        mock_settings = create_mock_settings(backend_public_url=None)
        
        with patch('core.capabilities.config_manager') as mock_cm:
            mock_cm.app_settings = mock_settings
            
            # Should handle missing attribute gracefully and return relative URL
            url = create_download_url("legacy-key", "legacy@example.com")
            
            assert url.startswith("/api/files/download/")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

