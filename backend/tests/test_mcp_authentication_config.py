"""Tests for MCP config authentication features (API keys and headers)."""

import os
import pytest
from backend.modules.config.config_manager import (
    ConfigManager,
    MCPConfig,
    MCPServerConfig,
)


class TestMCPAuthenticationConfig:
    """Test MCP authentication configuration features."""

    def test_mcp_server_config_has_auth_fields(self):
        """MCPServerConfig should have api_key and extra_headers fields."""
        config = MCPServerConfig(
            url="https://example.com",
            groups=["users"],
        )
        
        assert hasattr(config, "api_key")
        assert hasattr(config, "extra_headers")
        assert config.api_key is None
        assert config.extra_headers is None

    def test_mcp_server_config_with_api_key(self):
        """MCPServerConfig should accept api_key field."""
        config = MCPServerConfig(
            url="https://example.com",
            groups=["users"],
            api_key="test-api-key-123",
        )
        
        assert config.api_key == "test-api-key-123"

    def test_mcp_server_config_with_extra_headers(self):
        """MCPServerConfig should accept extra_headers field."""
        headers = {
            "X-API-Key": "test-key",
            "X-Client-ID": "test-client",
        }
        config = MCPServerConfig(
            url="https://example.com",
            groups=["users"],
            extra_headers=headers,
        )
        
        assert config.extra_headers == headers
        assert config.extra_headers["X-API-Key"] == "test-key"

    def test_mcp_server_config_with_both_auth_methods(self):
        """MCPServerConfig should accept both api_key and extra_headers."""
        headers = {"X-Custom": "value"}
        config = MCPServerConfig(
            url="https://example.com",
            groups=["users"],
            api_key="bearer-token",
            extra_headers=headers,
        )
        
        assert config.api_key == "bearer-token"
        assert config.extra_headers == headers


class TestMCPEnvVarExpansion:
    """Test environment variable expansion in MCP config."""

    def test_expand_api_key_env_var(self):
        """Environment variables in api_key should be expanded."""
        # Set test environment variable
        os.environ["TEST_MCP_API_KEY"] = "secret-key-123"
        
        try:
            # Create MCP config with env var reference
            server_config = MCPServerConfig(
                url="https://example.com",
                groups=["users"],
                api_key="${TEST_MCP_API_KEY}",
            )
            
            mcp_config = MCPConfig(servers={"test": server_config})
            
            # Create config manager and expand vars
            cm = ConfigManager()
            cm._expand_mcp_env_vars(mcp_config)
            
            # Check that the environment variable was expanded
            assert mcp_config.servers["test"].api_key == "secret-key-123"
        finally:
            # Clean up
            del os.environ["TEST_MCP_API_KEY"]

    def test_expand_extra_headers_env_vars(self):
        """Environment variables in extra_headers should be expanded."""
        # Set test environment variables
        os.environ["TEST_HEADER_KEY"] = "expanded-key"
        os.environ["TEST_HEADER_VALUE"] = "expanded-value"
        
        try:
            # Create MCP config with env var references in headers
            server_config = MCPServerConfig(
                url="https://example.com",
                groups=["users"],
                extra_headers={
                    "X-API-Key": "${TEST_HEADER_KEY}",
                    "X-Custom": "${TEST_HEADER_VALUE}",
                    "X-Static": "static-value",
                },
            )
            
            mcp_config = MCPConfig(servers={"test": server_config})
            
            # Create config manager and expand vars
            cm = ConfigManager()
            cm._expand_mcp_env_vars(mcp_config)
            
            # Check that environment variables were expanded
            assert mcp_config.servers["test"].extra_headers["X-API-Key"] == "expanded-key"
            assert mcp_config.servers["test"].extra_headers["X-Custom"] == "expanded-value"
            assert mcp_config.servers["test"].extra_headers["X-Static"] == "static-value"
        finally:
            # Clean up
            del os.environ["TEST_HEADER_KEY"]
            del os.environ["TEST_HEADER_VALUE"]

    def test_undefined_env_var_in_api_key(self):
        """Undefined environment variables should remain unexpanded."""
        # Ensure the env var is not set
        if "UNDEFINED_TEST_VAR" in os.environ:
            del os.environ["UNDEFINED_TEST_VAR"]
        
        server_config = MCPServerConfig(
            url="https://example.com",
            groups=["users"],
            api_key="${UNDEFINED_TEST_VAR}",
        )
        
        mcp_config = MCPConfig(servers={"test": server_config})
        
        # Create config manager and expand vars
        cm = ConfigManager()
        cm._expand_mcp_env_vars(mcp_config)
        
        # Undefined vars should remain as-is (with warning logged)
        assert mcp_config.servers["test"].api_key == "${UNDEFINED_TEST_VAR}"

    def test_undefined_env_var_in_extra_headers(self):
        """Undefined environment variables in headers should remain unexpanded."""
        # Ensure the env var is not set
        if "UNDEFINED_HEADER_VAR" in os.environ:
            del os.environ["UNDEFINED_HEADER_VAR"]
        
        server_config = MCPServerConfig(
            url="https://example.com",
            groups=["users"],
            extra_headers={
                "X-Undefined": "${UNDEFINED_HEADER_VAR}",
            },
        )
        
        mcp_config = MCPConfig(servers={"test": server_config})
        
        # Create config manager and expand vars
        cm = ConfigManager()
        cm._expand_mcp_env_vars(mcp_config)
        
        # Undefined vars should remain as-is (with warning logged)
        assert mcp_config.servers["test"].extra_headers["X-Undefined"] == "${UNDEFINED_HEADER_VAR}"

    def test_mixed_env_var_expansion(self):
        """Mix of defined and undefined env vars should be handled correctly."""
        # Set one env var but not the other
        os.environ["DEFINED_VAR"] = "defined-value"
        if "UNDEFINED_VAR" in os.environ:
            del os.environ["UNDEFINED_VAR"]
        
        try:
            server_config = MCPServerConfig(
                url="https://example.com",
                groups=["users"],
                api_key="${DEFINED_VAR}",
                extra_headers={
                    "X-Defined": "${DEFINED_VAR}",
                    "X-Undefined": "${UNDEFINED_VAR}",
                },
            )
            
            mcp_config = MCPConfig(servers={"test": server_config})
            
            # Create config manager and expand vars
            cm = ConfigManager()
            cm._expand_mcp_env_vars(mcp_config)
            
            # Defined should be expanded, undefined should remain
            assert mcp_config.servers["test"].api_key == "defined-value"
            assert mcp_config.servers["test"].extra_headers["X-Defined"] == "defined-value"
            assert mcp_config.servers["test"].extra_headers["X-Undefined"] == "${UNDEFINED_VAR}"
        finally:
            # Clean up
            del os.environ["DEFINED_VAR"]

    def test_env_var_expansion_for_multiple_servers(self):
        """Environment variable expansion should work for multiple servers."""
        # Set test environment variables
        os.environ["SERVER1_KEY"] = "key-for-server-1"
        os.environ["SERVER2_KEY"] = "key-for-server-2"
        
        try:
            mcp_config = MCPConfig(
                servers={
                    "server1": MCPServerConfig(
                        url="https://server1.com",
                        groups=["users"],
                        api_key="${SERVER1_KEY}",
                    ),
                    "server2": MCPServerConfig(
                        url="https://server2.com",
                        groups=["users"],
                        api_key="${SERVER2_KEY}",
                    ),
                }
            )
            
            # Create config manager and expand vars
            cm = ConfigManager()
            cm._expand_mcp_env_vars(mcp_config)
            
            # Both servers should have their env vars expanded
            assert mcp_config.servers["server1"].api_key == "key-for-server-1"
            assert mcp_config.servers["server2"].api_key == "key-for-server-2"
        finally:
            # Clean up
            del os.environ["SERVER1_KEY"]
            del os.environ["SERVER2_KEY"]

    def test_no_expansion_for_servers_without_auth(self):
        """Servers without auth fields should not cause errors during expansion."""
        server_config = MCPServerConfig(
            url="https://example.com",
            groups=["users"],
            # No api_key or extra_headers
        )
        
        mcp_config = MCPConfig(servers={"test": server_config})
        
        # Create config manager and expand vars
        cm = ConfigManager()
        # Should not raise any errors
        cm._expand_mcp_env_vars(mcp_config)
        
        # Fields should remain None
        assert mcp_config.servers["test"].api_key is None
        assert mcp_config.servers["test"].extra_headers is None
