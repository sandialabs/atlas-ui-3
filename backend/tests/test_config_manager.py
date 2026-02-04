"""Unit tests for ConfigManager.

Tests the centralized configuration management system without
modifying the actual environment or configuration files.
"""

import pytest
from pathlib import Path
from backend.modules.config.config_manager import (
    ConfigManager,
    AppSettings,
    LLMConfig,
    MCPConfig,
    resolve_env_var,
    MCPServerConfig,
)


class TestConfigManager:
    """Test ConfigManager initialization and basic functionality."""

    def test_config_manager_initialization(self):
        """ConfigManager should initialize without errors."""
        cm = ConfigManager()
        assert cm is not None
        assert cm._backend_root.name == "backend"

    def test_app_settings_loads(self):
        """AppSettings should load with defaults or environment values."""
        cm = ConfigManager()
        settings = cm.app_settings

        assert settings is not None
        assert isinstance(settings, AppSettings)
        assert settings.app_name is not None
        assert settings.port > 0
        assert settings.log_level in ["DEBUG", "INFO", "WARNING", "ERROR"]

    def test_llm_config_loads(self):
        """LLM config should load from config files."""
        cm = ConfigManager()
        llm_config = cm.llm_config

        assert llm_config is not None
        assert isinstance(llm_config, LLMConfig)
        # Should have at least some models configured
        assert hasattr(llm_config, "models")

    def test_mcp_config_loads(self):
        """MCP config should load from config files."""
        cm = ConfigManager()
        mcp_config = cm.mcp_config

        assert mcp_config is not None
        assert isinstance(mcp_config, MCPConfig)
        assert hasattr(mcp_config, "servers")

    def test_config_manager_caches_settings(self):
        """ConfigManager should cache settings and return same instance."""
        cm = ConfigManager()

        # Get settings twice
        settings1 = cm.app_settings
        settings2 = cm.app_settings

        # Should be the exact same object (cached)
        assert settings1 is settings2

    def test_config_manager_caches_llm_config(self):
        """ConfigManager should cache LLM config."""
        cm = ConfigManager()

        config1 = cm.llm_config
        config2 = cm.llm_config

        assert config1 is config2

    def test_search_paths_returns_list(self):
        """Search paths should return a list of Path objects."""
        cm = ConfigManager()

        paths = cm._search_paths("llmconfig.yml")

        assert isinstance(paths, list)
        assert len(paths) > 0
        assert all(isinstance(p, Path) for p in paths)

    def test_search_paths_includes_overrides_and_defaults(self):
        """Search paths should include both overrides and defaults directories."""
        cm = ConfigManager()

        paths = cm._search_paths("mcp.json")
        path_strings = [str(p) for p in paths]

        # Should include overrides directory
        assert any("overrides" in p for p in path_strings)
        # Should include defaults directory
        assert any("defaults" in p for p in path_strings)

    def test_validate_config_returns_dict(self):
        """Validate config should return a dictionary of validation results."""
        cm = ConfigManager()

        result = cm.validate_config()

        assert isinstance(result, dict)
        assert "app_settings" in result
        assert "llm_config" in result
        assert "mcp_config" in result
        # All should be boolean values
        assert all(isinstance(v, bool) for v in result.values())

    def test_reload_configs_works(self):
        """Reload configs should clear cache and reload."""
        cm = ConfigManager()

        # Load configs first
        _ = cm.app_settings
        _ = cm.llm_config

        # Reload should not raise errors
        cm.reload_configs()

        # Configs should still be accessible
        assert cm.app_settings is not None
        assert cm.llm_config is not None


class TestAppSettings:
    """Test AppSettings model."""

    def test_app_settings_has_required_fields(self):
        """AppSettings should have all required configuration fields."""
        settings = AppSettings()

        # Basic app settings
        assert hasattr(settings, "app_name")
        assert hasattr(settings, "port")
        assert hasattr(settings, "debug_mode")
        assert hasattr(settings, "log_level")

        # Feature flags
        assert hasattr(settings, "feature_rag_enabled")
        assert hasattr(settings, "feature_tools_enabled")
        assert hasattr(settings, "feature_marketplace_enabled")

        # S3 settings
        assert hasattr(settings, "s3_endpoint")
        assert hasattr(settings, "s3_bucket_name")

        # Config paths
        assert hasattr(settings, "app_config_overrides")
        assert hasattr(settings, "app_config_defaults")

    def test_app_settings_defaults(self):
        """AppSettings should have sensible defaults."""
        settings = AppSettings()

        assert isinstance(settings.port, int) and 1 <= settings.port <= 65535
        assert settings.log_level in ["DEBUG", "INFO", "WARNING", "ERROR"]
        assert isinstance(settings.debug_mode, bool)
        assert isinstance(settings.banner_enabled, bool)

    def test_app_settings_agent_backward_compatibility(self):
        """Agent mode available should maintain backward compatibility."""
        settings = AppSettings()

        # Both new and old property should work
        assert hasattr(settings, "feature_agent_mode_available")
        assert hasattr(settings, "agent_mode_available")
        assert settings.agent_mode_available == settings.feature_agent_mode_available


class TestConfigManagerCustomRoot:
    """Test ConfigManager with custom backend root."""

    def test_custom_backend_root(self):
        """ConfigManager should accept custom backend root path."""
        custom_root = Path(__file__).parent.parent
        cm = ConfigManager(backend_root=custom_root)

        assert cm._backend_root == custom_root

    def test_custom_root_still_loads_configs(self):
        """ConfigManager with custom root should still load configs."""
        custom_root = Path(__file__).parent.parent
        cm = ConfigManager(backend_root=custom_root)

        # Should still be able to load configs
        assert cm.app_settings is not None
        assert cm.llm_config is not None


class TestResolveEnvVar:
    """Test environment variable substitution in config values."""

    def test_resolve_env_var_with_none(self):
        """Should return None when input is None."""
        assert resolve_env_var(None) is None

    def test_resolve_env_var_with_literal_string(self):
        """Should return literal string unchanged."""
        assert resolve_env_var("my-literal-token") == "my-literal-token"

    def test_resolve_env_var_with_existing_env_var(self, monkeypatch):
        """Should resolve ${VAR_NAME} when env var exists."""
        monkeypatch.setenv("TEST_TOKEN", "secret-123")
        assert resolve_env_var("${TEST_TOKEN}") == "secret-123"

    def test_resolve_env_var_with_missing_env_var(self):
        """Should raise ValueError when env var doesn't exist."""
        with pytest.raises(ValueError, match="Environment variable 'MISSING_VAR' is not set"):
            resolve_env_var("${MISSING_VAR}")

    def test_resolve_env_var_with_empty_string(self):
        """Should return empty string unchanged."""
        assert resolve_env_var("") == ""

    def test_resolve_env_var_with_empty_env_var(self, monkeypatch):
        """Should allow empty env var values."""
        monkeypatch.setenv("EMPTY_VAR", "")
        assert resolve_env_var("${EMPTY_VAR}") == ""

    def test_resolve_env_var_with_partial_pattern(self):
        """Should not match partial patterns like 'prefix-${VAR}'."""
        # Only exact ${VAR} pattern is supported, not embedded in strings
        assert resolve_env_var("prefix-${VAR}") == "prefix-${VAR}"

    def test_resolve_env_var_with_suffix_pattern(self, monkeypatch):
        """Should not match patterns with suffix like '${VAR}-suffix'.

        Regression test: Previously re.match() would match '${VAR}' at the start
        and return just the env value, silently dropping the suffix.
        Now we use re.fullmatch() to ensure the entire string is a pattern.
        """
        monkeypatch.setenv("MY_VAR", "resolved")
        # Pattern with suffix should be treated as literal, not partially resolved
        assert resolve_env_var("${MY_VAR}-suffix") == "${MY_VAR}-suffix"
        # Contrast with exact pattern which should resolve
        assert resolve_env_var("${MY_VAR}") == "resolved"

    def test_resolve_env_var_with_invalid_pattern(self):
        """Should return strings with invalid patterns unchanged."""
        assert resolve_env_var("${123_INVALID}") == "${123_INVALID}"
        assert resolve_env_var("${INVALID-NAME}") == "${INVALID-NAME}"
        assert resolve_env_var("$VAR") == "$VAR"
        assert resolve_env_var("{VAR}") == "{VAR}"

    def test_resolve_env_var_case_sensitive(self, monkeypatch):
        """Environment variable names should be case-sensitive."""
        monkeypatch.setenv("MY_VAR", "value1")
        monkeypatch.setenv("my_var", "value2")
        assert resolve_env_var("${MY_VAR}") == "value1"
        assert resolve_env_var("${my_var}") == "value2"

    def test_resolve_env_var_with_special_chars(self, monkeypatch):
        """Should handle env vars with special characters in values."""
        monkeypatch.setenv("SPECIAL_TOKEN", "abc!@#$%^&*()_+-=[]{}|;:,.<>?")
        assert resolve_env_var("${SPECIAL_TOKEN}") == "abc!@#$%^&*()_+-=[]{}|;:,.<>?"

class TestMCPServerConfig:
    """Test MCPServerConfig with auth_token field."""

    def test_auth_token_is_optional(self):
        """auth_token field should be optional."""
        config = MCPServerConfig(
            description="Test server",
            command=["python", "server.py"]
        )
        assert config.auth_token is None

    def test_auth_token_accepts_string(self):
        """auth_token should accept string values."""
        config = MCPServerConfig(
            description="Test server",
            command=["python", "server.py"],
            auth_token="my-token-123"
        )
        assert config.auth_token == "my-token-123"

    def test_auth_token_accepts_env_var_pattern(self):
        """auth_token should accept environment variable patterns."""
        config = MCPServerConfig(
            description="Test server",
            url="http://localhost:8000",
            auth_token="${MY_TOKEN}"
        )
        assert config.auth_token == "${MY_TOKEN}"

    def test_auth_token_accepts_none(self):
        """auth_token should explicitly accept None."""
        config = MCPServerConfig(
            description="Test server",
            command=["python", "server.py"],
            auth_token=None
        )
        assert config.auth_token is None


class TestLLMConfigEnvExpansion:
    """Test LLM configuration with environment variable expansion."""

    def test_llm_model_config_with_env_var_api_key(self, monkeypatch):
        """LLM model config should accept environment variable patterns in api_key."""
        from backend.modules.config.config_manager import ModelConfig
        
        monkeypatch.setenv("TEST_API_KEY", "secret-key-123")
        
        config = ModelConfig(
            model_name="test-model",
            model_url="https://api.openai.com/v1",
            api_key="${TEST_API_KEY}"
        )
        assert config.api_key == "${TEST_API_KEY}"
        
        # Test that resolve_env_var works
        resolved_key = resolve_env_var(config.api_key)
        assert resolved_key == "secret-key-123"

    def test_llm_model_config_with_literal_api_key(self):
        """LLM model config should accept literal api_key values."""
        from backend.modules.config.config_manager import ModelConfig
        
        config = ModelConfig(
            model_name="test-model",
            model_url="https://api.openai.com/v1",
            api_key="sk-literal-key-123"
        )
        assert config.api_key == "sk-literal-key-123"
        
        # Test that resolve_env_var returns literal value unchanged
        resolved_key = resolve_env_var(config.api_key)
        assert resolved_key == "sk-literal-key-123"

    def test_llm_model_config_with_missing_env_var(self):
        """resolve_env_var should raise ValueError for missing env vars in api_key."""
        from backend.modules.config.config_manager import ModelConfig
        
        config = ModelConfig(
            model_name="test-model",
            model_url="https://api.openai.com/v1",
            api_key="${MISSING_API_KEY}"
        )
        
        with pytest.raises(ValueError, match="Environment variable 'MISSING_API_KEY' is not set"):
            resolve_env_var(config.api_key)

    def test_llm_model_config_with_env_var_in_extra_headers(self, monkeypatch):
        """LLM model config should support environment variables in extra_headers."""
        from backend.modules.config.config_manager import ModelConfig
        
        monkeypatch.setenv("REFERER_URL", "https://myapp.com")
        monkeypatch.setenv("APP_NAME", "MyApp")
        
        config = ModelConfig(
            model_name="test-model",
            model_url="https://openrouter.ai/api/v1",
            api_key="sk-test",
            extra_headers={
                "HTTP-Referer": "${REFERER_URL}",
                "X-Title": "${APP_NAME}"
            }
        )
        
        # Test that headers are stored as-is
        assert config.extra_headers["HTTP-Referer"] == "${REFERER_URL}"
        assert config.extra_headers["X-Title"] == "${APP_NAME}"
        
        # Test that resolve_env_var works for each header
        resolved_referer = resolve_env_var(config.extra_headers["HTTP-Referer"])
        resolved_title = resolve_env_var(config.extra_headers["X-Title"])
        assert resolved_referer == "https://myapp.com"
        assert resolved_title == "MyApp"

    def test_llm_model_config_with_literal_extra_headers(self):
        """LLM model config should support literal values in extra_headers."""
        from backend.modules.config.config_manager import ModelConfig
        
        config = ModelConfig(
            model_name="test-model",
            model_url="https://api.openai.com/v1",
            api_key="sk-test",
            extra_headers={
                "X-Custom-Header": "literal-value",
                "X-Another-Header": "another-literal"
            }
        )
        
        # Test that headers are stored as-is
        assert config.extra_headers["X-Custom-Header"] == "literal-value"
        assert config.extra_headers["X-Another-Header"] == "another-literal"
        
        # Test that resolve_env_var returns literal values unchanged
        resolved_custom = resolve_env_var(config.extra_headers["X-Custom-Header"])
        resolved_another = resolve_env_var(config.extra_headers["X-Another-Header"])
        assert resolved_custom == "literal-value"
        assert resolved_another == "another-literal"

    def test_llm_model_config_with_missing_env_var_in_extra_headers(self):
        """resolve_env_var should raise ValueError for missing env vars in extra_headers."""
        from backend.modules.config.config_manager import ModelConfig

        config = ModelConfig(
            model_name="test-model",
            model_url="https://api.openai.com/v1",
            api_key="sk-test",
            extra_headers={
                "X-Custom-Header": "${MISSING_HEADER_VAR}"
            }
        )

        with pytest.raises(ValueError, match="Environment variable 'MISSING_HEADER_VAR' is not set"):
            resolve_env_var(config.extra_headers["X-Custom-Header"])


class TestAppSettingsRAGFeature:
    """Test AppSettings RAG feature flag configuration.

    RAG is now configured via a simple on/off toggle (FEATURE_RAG_ENABLED).
    All RAG source configuration is done in rag-sources.json.
    """

    def test_feature_rag_enabled_default_false(self, monkeypatch):
        """feature_rag_enabled should default to False."""
        monkeypatch.delenv("FEATURE_RAG_ENABLED", raising=False)
        settings = AppSettings(_env_file=None)
        assert settings.feature_rag_enabled is False

    def test_feature_rag_enabled_from_environment(self, monkeypatch):
        """FEATURE_RAG_ENABLED environment variable should enable RAG."""
        monkeypatch.setenv("FEATURE_RAG_ENABLED", "true")
        settings = AppSettings()
        assert settings.feature_rag_enabled is True

    def test_feature_rag_disabled_from_environment(self, monkeypatch):
        """FEATURE_RAG_ENABLED=false should disable RAG."""
        monkeypatch.setenv("FEATURE_RAG_ENABLED", "false")
        settings = AppSettings()
        assert settings.feature_rag_enabled is False

    def test_feature_rag_enabled_is_stored_field(self):
        """feature_rag_enabled should be a stored field, not a derived property."""
        assert "feature_rag_enabled" in AppSettings.model_fields


class TestRAGSourceConfig:
    """Test RAGSourceConfig model validation."""

    def test_mcp_source_with_command(self):
        """MCP source should accept command for stdio transport."""
        from backend.modules.config.config_manager import RAGSourceConfig

        config = RAGSourceConfig(
            type="mcp",
            display_name="Test MCP",
            command=["python", "server.py"],
        )
        assert config.type == "mcp"
        assert config.command == ["python", "server.py"]

    def test_mcp_source_with_url(self):
        """MCP source should accept url for HTTP/SSE transport."""
        from backend.modules.config.config_manager import RAGSourceConfig

        config = RAGSourceConfig(
            type="mcp",
            display_name="Test MCP",
            url="http://localhost:8080",
        )
        assert config.type == "mcp"
        assert config.url == "http://localhost:8080"

    def test_mcp_source_requires_command_or_url(self):
        """MCP source should raise error if neither command nor url is provided."""
        from backend.modules.config.config_manager import RAGSourceConfig

        with pytest.raises(ValueError, match="MCP RAG source requires either 'command' or 'url'"):
            RAGSourceConfig(
                type="mcp",
                display_name="Invalid MCP",
            )

    def test_http_source_with_url(self):
        """HTTP source should accept url."""
        from backend.modules.config.config_manager import RAGSourceConfig

        config = RAGSourceConfig(
            type="http",
            display_name="Test HTTP RAG",
            url="https://rag-api.example.com",
            bearer_token="secret-token",
        )
        assert config.type == "http"
        assert config.url == "https://rag-api.example.com"
        assert config.bearer_token == "secret-token"

    def test_http_source_requires_url(self):
        """HTTP source should raise error if url is not provided."""
        from backend.modules.config.config_manager import RAGSourceConfig

        with pytest.raises(ValueError, match="HTTP RAG source requires 'url'"):
            RAGSourceConfig(
                type="http",
                display_name="Invalid HTTP",
            )

    def test_rag_source_defaults(self):
        """RAGSourceConfig should have sensible defaults."""
        from backend.modules.config.config_manager import RAGSourceConfig

        config = RAGSourceConfig(
            type="http",
            url="https://example.com",
        )
        assert config.enabled is True
        assert config.groups == []
        assert config.compliance_level is None
        assert config.top_k == 4
        assert config.timeout == 60.0
        assert config.discovery_endpoint == "/discover/datasources"
        assert config.query_endpoint == "/rag/completions"

    def test_rag_source_with_all_fields(self):
        """RAGSourceConfig should accept all optional fields."""
        from backend.modules.config.config_manager import RAGSourceConfig

        config = RAGSourceConfig(
            type="http",
            display_name="Full Config RAG",
            description="A fully configured RAG source",
            icon="search",
            groups=["admin", "users"],
            compliance_level="Internal",
            enabled=True,
            url="https://rag.example.com",
            bearer_token="my-token",
            default_model="custom-model",
            top_k=10,
            timeout=120.0,
            discovery_endpoint="/custom/discover",
            query_endpoint="/custom/query",
        )
        assert config.display_name == "Full Config RAG"
        assert config.description == "A fully configured RAG source"
        assert config.icon == "search"
        assert config.groups == ["admin", "users"]
        assert config.compliance_level == "Internal"
        assert config.default_model == "custom-model"
        assert config.top_k == 10
        assert config.timeout == 120.0
        assert config.discovery_endpoint == "/custom/discover"
        assert config.query_endpoint == "/custom/query"

    def test_rag_source_disabled(self):
        """RAGSourceConfig should support disabled state."""
        from backend.modules.config.config_manager import RAGSourceConfig

        config = RAGSourceConfig(
            type="http",
            url="https://example.com",
            enabled=False,
        )
        assert config.enabled is False


class TestRAGSourcesConfig:
    """Test RAGSourcesConfig model for multiple RAG sources."""

    def test_empty_sources_config(self):
        """RAGSourcesConfig should accept empty sources dict."""
        from backend.modules.config.config_manager import RAGSourcesConfig

        config = RAGSourcesConfig()
        assert config.sources == {}

    def test_sources_config_with_multiple_sources(self):
        """RAGSourcesConfig should accept multiple source configurations."""
        from backend.modules.config.config_manager import RAGSourcesConfig, RAGSourceConfig

        config = RAGSourcesConfig(
            sources={
                "http_source": RAGSourceConfig(
                    type="http",
                    url="https://http-rag.example.com",
                ),
                "mcp_source": RAGSourceConfig(
                    type="mcp",
                    command=["python", "mcp_server.py"],
                ),
            }
        )
        assert len(config.sources) == 2
        assert "http_source" in config.sources
        assert "mcp_source" in config.sources
        assert config.sources["http_source"].type == "http"
        assert config.sources["mcp_source"].type == "mcp"

    def test_sources_config_from_dict(self):
        """RAGSourcesConfig should convert dict values to RAGSourceConfig."""
        from backend.modules.config.config_manager import RAGSourcesConfig

        config = RAGSourcesConfig(
            sources={
                "test_source": {
                    "type": "http",
                    "url": "https://example.com",
                    "display_name": "Test Source",
                }
            }
        )
        assert config.sources["test_source"].type == "http"
        assert config.sources["test_source"].url == "https://example.com"
        assert config.sources["test_source"].display_name == "Test Source"


