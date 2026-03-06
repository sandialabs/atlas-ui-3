"""
Centralized configuration management using Pydantic models.

This module provides a unified configuration system that:
- Uses Pydantic for type validation and environment variable loading
- Replaces the duplicate config loading logic in config_utils.py
- Provides proper error handling with logging tracebacks
- Supports both .env files and direct environment variables
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import yaml
from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


def resolve_env_var(value: Optional[str], required: bool = True) -> Optional[str]:
    """
    Resolve environment variables in config values.

    Supports patterns like:
    - "${ENV_VAR_NAME}" -> replaced with os.environ.get("ENV_VAR_NAME")
    - "literal-string" -> returned as-is
    - None -> returned as-is

    Note: Only complete env var patterns are resolved. Values like "prefix-${VAR}"
    or "${VAR}-suffix" are treated as literals and returned unchanged.

    Args:
        value: Config value that may contain env var pattern
        required: If True (default), raises ValueError if env var is not set.
                  If False, returns None when env var is not set.

    Returns:
        Resolved value with env vars substituted, or None if value is None
        or if env var is not set and required=False

    Raises:
        ValueError: If env var pattern is found but variable is not set and required=True
    """
    if value is None:
        return None

    # Pattern: ${VAR_NAME}
    # Uses fullmatch() to ensure the entire string is an env var pattern.
    # Patterns like "${VAR}-suffix" or "prefix-${VAR}" are treated as literals.
    pattern = r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}'
    match = re.fullmatch(pattern, value)

    if match:
        env_var_name = match.group(1)
        env_value = os.environ.get(env_var_name)

        if env_value is None:
            if required:
                raise ValueError(
                    f"Environment variable '{env_var_name}' is not set but required in config"
                )
            return None

        return env_value

    # Return literal string if no pattern found
    return value


class ModelConfig(BaseModel):
    """Configuration for a single LLM model."""
    model_name: str
    model_url: str
    api_key: str = ""
    description: Optional[str] = None
    max_tokens: Optional[int] = 10000
    temperature: Optional[float] = 0.7
    # Optional extra HTTP headers (e.g. for providers like OpenRouter)
    extra_headers: Optional[Dict[str, str]] = None
    # Compliance/security level (e.g., "External", "Internal", "Public")
    compliance_level: Optional[str] = None
    # API key source: "system" uses env var resolution, "user" requires per-user key from token storage,
    # "globus" uses Globus OAuth token for the configured scope (requires globus_scope)
    api_key_source: str = "system"
    # Globus scope identifier for models using api_key_source: "globus"
    # This is the resource_server UUID from the Globus token response other_tokens
    # Example for ALCF: "681c10cc-f684-4540-bcd7-0b4df3bc26ef"
    globus_scope: Optional[str] = None


class LLMConfig(BaseModel):
    """Configuration for all LLM models."""
    models: Dict[str, ModelConfig]

    @field_validator('models', mode='before')
    @classmethod
    def validate_models(cls, v):
        """Convert dict values to ModelConfig objects."""
        if isinstance(v, dict):
            return {name: ModelConfig(**config) if isinstance(config, dict) else config
                   for name, config in v.items()}
        return v


class OAuthConfig(BaseModel):
    """OAuth 2.1 configuration for MCP server authentication.

    Supports the OAuth 2.1 Authorization Code Grant with PKCE as implemented
    by FastMCP. See https://gofastmcp.com/clients/auth/oauth for details.
    """
    scopes: Optional[List[str]] = None  # OAuth scopes to request (e.g., ["read", "write"])
    client_name: str = "Atlas UI"  # Client name for dynamic registration
    callback_port: Optional[int] = None  # Fixed port for OAuth callback (default: random)


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server."""
    description: Optional[str] = None
    author: Optional[str] = None         # Author of the MCP server
    short_description: Optional[str] = None  # Short description for marketplace display
    help_email: Optional[str] = None     # Contact email for help/support
    groups: List[str] = Field(default_factory=list)
    enabled: bool = True
    command: Optional[List[str]] = None  # Command to run server (for stdio servers)
    cwd: Optional[str] = None            # Working directory for command
    env: Optional[Dict[str, str]] = None  # Environment variables for stdio servers
    url: Optional[str] = None            # URL for HTTP servers
    type: str = "stdio"                  # Server type: "stdio" or "http" (deprecated, use transport)
    transport: Optional[str] = None      # Explicit transport: "stdio", "http", "sse" - takes priority over auto-detection
    # Authentication configuration
    auth_type: str = "none"  # Authentication type: "none", "api_key", "bearer", "jwt", "oauth"
    auth_token: Optional[str] = None     # Bearer token for MCP server authentication (supports ${ENV_VAR})
    oauth_config: Optional[OAuthConfig] = None  # OAuth 2.1 configuration (when auth_type="oauth")
    compliance_level: Optional[str] = None  # Compliance/security level (e.g., "SOC2", "HIPAA", "Public")
    require_approval: List[str] = Field(default_factory=list)  # List of tool names (without server prefix) requiring approval
    allow_edit: List[str] = Field(default_factory=list)  # LEGACY. List of tool names (without server prefix) allowing argument editing


class MCPConfig(BaseModel):
    """Configuration for all MCP servers."""
    servers: Dict[str, MCPServerConfig] = Field(default_factory=dict)

    @field_validator('servers', mode='before')
    @classmethod
    def validate_servers(cls, v):
        """Convert dict values to MCPServerConfig objects."""
        if isinstance(v, dict):
            return {name: MCPServerConfig(**config) if isinstance(config, dict) else config
                   for name, config in v.items()}
        return v


class RAGSourceConfig(BaseModel):
    """Configuration for a single RAG source (MCP or HTTP-based).

    Supports two types:
    - "mcp": MCP-based RAG server that exposes rag_discover_resources tool
    - "http": HTTP REST API RAG server (like ATLAS RAG API)
    """
    type: Literal["mcp", "http"] = "mcp"

    # Common fields
    display_name: Optional[str] = None  # UI display name
    description: Optional[str] = None
    icon: Optional[str] = None  # UI icon
    groups: List[str] = Field(default_factory=list)  # Access groups
    compliance_level: Optional[str] = None
    enabled: bool = True

    # MCP-specific fields (type="mcp")
    command: Optional[List[str]] = None  # Command for stdio MCP servers
    cwd: Optional[str] = None  # Working directory
    env: Optional[Dict[str, str]] = None  # Environment variables
    url: Optional[str] = None  # URL for HTTP/SSE MCP servers
    transport: Optional[str] = None  # "stdio", "http", "sse"
    auth_token: Optional[str] = None  # MCP server auth token

    # HTTP REST API fields (type="http")
    bearer_token: Optional[str] = None  # Bearer token for HTTP RAG API
    default_model: Optional[str] = None  # Model for RAG queries
    top_k: int = 4  # Number of documents to retrieve
    timeout: float = 60.0  # Request timeout in seconds

    # API endpoint customization (HTTP type)
    discovery_endpoint: str = "/discover/datasources"
    query_endpoint: str = "/rag/completions"

    @model_validator(mode='after')
    def validate_type_specific_fields(self):
        """Validate that required fields are present based on type."""
        if self.type == "mcp":
            # MCP type requires either command (stdio) or url (http/sse)
            if not self.command and not self.url:
                raise ValueError("MCP RAG source requires either 'command' or 'url'")
        elif self.type == "http":
            # HTTP type requires url
            if not self.url:
                raise ValueError("HTTP RAG source requires 'url'")
        return self


class RAGSourcesConfig(BaseModel):
    """Configuration for all RAG sources."""
    sources: Dict[str, RAGSourceConfig] = Field(default_factory=dict)

    @field_validator('sources', mode='before')
    @classmethod
    def validate_sources(cls, v):
        """Convert dict values to RAGSourceConfig objects."""
        if isinstance(v, dict):
            return {name: RAGSourceConfig(**config) if isinstance(config, dict) else config
                   for name, config in v.items()}
        return v


class ToolApprovalConfig(BaseModel):
    """Configuration for a single tool's approval settings."""
    require_approval: bool = False
    allow_edit: bool = True


class ToolApprovalsConfig(BaseModel):
    """Configuration for tool approvals."""
    require_approval_by_default: bool = False
    tools: Dict[str, ToolApprovalConfig] = Field(default_factory=dict)

    @field_validator('tools', mode='before')
    @classmethod
    def validate_tools(cls, v):
        """Convert dict values to ToolApprovalConfig objects."""
        if isinstance(v, dict):
            return {name: ToolApprovalConfig(**config) if isinstance(config, dict) else config
                   for name, config in v.items()}
        return v


class FileExtractorConfig(BaseModel):
    """Configuration for a single file content extractor service."""
    url: str
    method: str = "POST"
    timeout_seconds: int = 30
    max_file_size_mb: int = 50
    preview_chars: Optional[int] = 2000
    request_format: str = "base64"  # "base64", "multipart", or "url"
    form_field_name: str = "file"  # Field name for multipart form uploads
    response_field: str = "text"
    enabled: bool = True
    # API key for authentication (supports ${ENV_VAR} syntax)
    api_key: Optional[str] = None
    # Additional HTTP headers (values support ${ENV_VAR} syntax)
    headers: Optional[Dict[str, str]] = None


class FileExtractorsConfig(BaseModel):
    """Configuration for file content extraction services."""
    enabled: bool = True
    default_behavior: str = "full"  # "full" | "preview" | "none"
    extractors: Dict[str, FileExtractorConfig] = Field(default_factory=dict)
    extension_mapping: Dict[str, str] = Field(default_factory=dict)
    mime_mapping: Dict[str, str] = Field(default_factory=dict)

    @field_validator('default_behavior', mode='before')
    @classmethod
    def normalize_default_behavior(cls, v):
        """Normalize legacy values to new 3-mode scheme."""
        legacy_map = {"extract": "full", "attach_only": "none"}
        return legacy_map.get(v, v)

    @field_validator('extractors', mode='before')
    @classmethod
    def validate_extractors(cls, v):
        """Convert dict values to FileExtractorConfig objects."""
        if isinstance(v, dict):
            return {name: FileExtractorConfig(**config) if isinstance(config, dict) else config
                   for name, config in v.items()}
        return v


class AppSettings(BaseSettings):
    """Main application settings loaded from environment variables."""

    # Application settings
    app_name: str = "Chat UI"
    port: int = 8000
    debug_mode: bool = False
    # Logging settings
    log_level: str = "INFO"  # Override default logging level (DEBUG, INFO, WARNING, ERROR)
    feature_metrics_logging_enabled: bool = Field(
        False,
        description="Enable metrics logging for user activities (LLM calls, tool calls, file uploads, errors)",
        validation_alias=AliasChoices("FEATURE_METRICS_LOGGING_ENABLED"),
    )
    # Suppress LiteLLM verbose logging (independent of log_level)
    feature_suppress_litellm_logging: bool = Field(
        default=True,
        description="Suppress LiteLLM verbose stdout/debug output by setting LITELLM_LOG=ERROR",
        validation_alias=AliasChoices("FEATURE_SUPPRESS_LITELLM_LOGGING"),
    )

    # RAG Feature Flag
    # When enabled, RAG sources are configured in config/rag-sources.json
    # See docs/admin/external-rag-api.md for configuration details
    feature_rag_enabled: bool = Field(
        False,
        description="Enable RAG (Retrieval-Augmented Generation). Configure sources in rag-sources.json",
        validation_alias=AliasChoices("FEATURE_RAG_ENABLED"),
    )

    # Banner settings
    banner_enabled: bool = False

    # Splash screen settings
    feature_splash_screen_enabled: bool = Field(
        False,
        description="Enable startup splash screen for displaying policies and information",
        validation_alias=AliasChoices("FEATURE_SPLASH_SCREEN_ENABLED", "SPLASH_SCREEN_ENABLED"),
    )

    # Agent settings
    # Renamed to feature_agent_mode_available to align with other FEATURE_* flags.
    feature_agent_mode_available: bool = Field(
        True,
        description="Agent mode availability feature flag",
        validation_alias=AliasChoices("FEATURE_AGENT_MODE_AVAILABLE", "AGENT_MODE_AVAILABLE")
    )  # Accept both old and new env var names
    agent_max_steps: int = 10
    agent_loop_strategy: str = Field(
        default="think-act",
        description="Agent loop strategy selector (react, think-act, act, agentic)",
        validation_alias=AliasChoices("AGENT_LOOP_STRATEGY"),
    )
    # Backward compatibility: support old AGENT_MODE_AVAILABLE env if present
    @property
    def agent_mode_available(self) -> bool:
        """Maintain backward compatibility for code still referencing agent_mode_available."""
        return self.feature_agent_mode_available

    # Tool approval settings
    require_tool_approval_by_default: bool = False
    # When true, all tools require approval (admin-enforced), overriding per-tool and default settings
    force_tool_approval_globally: bool = Field(default=False, validation_alias="FORCE_TOOL_APPROVAL_GLOBALLY")

    # LLM Health Check settings
    llm_health_check_interval: int = 5  # minutes

    # MCP Health Check settings
    mcp_health_check_interval: int = 300  # seconds (5 minutes)

    # MCP Auto-Reconnect settings
    feature_mcp_auto_reconnect_enabled: bool = Field(
        False,
        description="Enable automatic reconnection to failed MCP servers with exponential backoff",
        validation_alias=AliasChoices("FEATURE_MCP_AUTO_RECONNECT_ENABLED"),
    )
    mcp_reconnect_interval: int = Field(
        default=60,
        description="Base interval in seconds between MCP reconnect attempts",
        validation_alias="MCP_RECONNECT_INTERVAL"
    )
    mcp_reconnect_max_interval: int = Field(
        default=300,
        description="Maximum interval in seconds between MCP reconnect attempts (caps exponential backoff)",
        validation_alias="MCP_RECONNECT_MAX_INTERVAL"
    )
    mcp_reconnect_backoff_multiplier: float = Field(
        default=2.0,
        description="Multiplier for exponential backoff between reconnect attempts",
        validation_alias="MCP_RECONNECT_BACKOFF_MULTIPLIER"
    )
    mcp_discovery_timeout: int = Field(
        default=30,
        description="Timeout in seconds for MCP discovery calls (list_tools, list_prompts)",
        validation_alias="MCP_DISCOVERY_TIMEOUT"
    )
    mcp_call_timeout: int = Field(
        default=120,
        description="Timeout in seconds for MCP tool calls (call_tool)",
        validation_alias="MCP_CALL_TIMEOUT"
    )

    # MCP Token Storage settings
    mcp_token_storage_dir: Optional[str] = Field(
        default=None,
        description="Directory for storing encrypted user tokens. Defaults to config/secure/",
        validation_alias="MCP_TOKEN_STORAGE_DIR"
    )
    mcp_token_encryption_key: Optional[str] = Field(
        default=None,
        description="Encryption key for user tokens. If not set, tokens won't persist across restarts",
        validation_alias="MCP_TOKEN_ENCRYPTION_KEY"
    )

    # Admin settings
    admin_group: str = "admin"
    test_user: str = "test@test.com"  # Test user for development
    auth_group_check_url: Optional[str] = Field(default=None, validation_alias="AUTH_GROUP_CHECK_URL")
    auth_group_check_api_key: Optional[str] = Field(default=None, validation_alias="AUTH_GROUP_CHECK_API_KEY")

    # Authentication header configuration
    auth_user_header: str = Field(
        default="X-User-Email",
        description="HTTP header name to extract authenticated username from reverse proxy",
        validation_alias="AUTH_USER_HEADER"
    )

    # Authentication header configuration
    auth_user_header_type: str = Field(
        default="email-string",
        description="The datatype stored in AUTH_USER_HEADER",
        validation_alias="AUTH_USER_HEADER_TYPE"
    )

    # Authentication AWS expected ALB ARN
    auth_aws_expected_alb_arn: str = Field(
        default="",
        description="The expected AWS ALB ARN",
        validation_alias="AUTH_AWS_EXPECTED_ALB_ARN"
    )

    # Authentication AWS region
    auth_aws_region: str = Field(
        default="us-east-1",
        description="The AWS region",
        validation_alias="AUTH_AWS_REGION"
    )

    # Proxy secret authentication configuration
    feature_proxy_secret_enabled: bool = Field(
        default=False,
        description="Enable proxy secret validation to ensure requests come from trusted reverse proxy",
        validation_alias="FEATURE_PROXY_SECRET_ENABLED"
    )
    proxy_secret_header: str = Field(
        default="X-Proxy-Secret",
        description="HTTP header name for proxy secret validation",
        validation_alias="PROXY_SECRET_HEADER"
    )
    proxy_secret: Optional[str] = Field(
        default=None,
        description="Secret value that must be sent by reverse proxy for validation",
        validation_alias="PROXY_SECRET"
    )
    auth_redirect_url: str = Field(
        default="/auth",
        description="URL to redirect to when authentication fails",
        validation_alias="AUTH_REDIRECT_URL"
    )

    # Globus OAuth settings
    feature_globus_auth_enabled: bool = Field(
        default=False,
        description="Enable Globus OAuth authentication for ALCF and other Globus-scoped services",
        validation_alias=AliasChoices("FEATURE_GLOBUS_AUTH_ENABLED"),
    )
    globus_client_id: Optional[str] = Field(
        default=None,
        description="Globus OAuth client ID (register at app.globus.org/settings/developers)",
        validation_alias="GLOBUS_CLIENT_ID",
    )
    globus_client_secret: Optional[str] = Field(
        default=None,
        description="Globus OAuth client secret",
        validation_alias="GLOBUS_CLIENT_SECRET",
    )
    globus_redirect_uri: Optional[str] = Field(
        default=None,
        description="Globus OAuth redirect URI (e.g. http://localhost:8000/auth/globus/callback)",
        validation_alias="GLOBUS_REDIRECT_URI",
    )
    globus_scopes: str = Field(
        default="openid profile email",
        description="Space-separated Globus OAuth scopes to request (include service-specific scopes for ALCF etc.)",
        validation_alias="GLOBUS_SCOPES",
    )
    globus_session_secret: str = Field(
        default="atlas-globus-session-change-me",
        description="Secret key for Globus session middleware",
        validation_alias="GLOBUS_SESSION_SECRET",
    )

    # S3/MinIO storage settings
    use_mock_s3: bool = False  # Use in-process S3 mock (no Docker required)
    s3_endpoint: str = "http://localhost:9000"
    s3_bucket_name: str = "atlas-files"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_region: str = "us-east-1"
    s3_timeout: int = 30
    s3_use_ssl: bool = False

    # Feature flags
    feature_workspaces_enabled: bool = False
    feature_tools_enabled: bool = False
    feature_marketplace_enabled: bool = False
    feature_files_panel_enabled: bool = False
    feature_chat_history_enabled: bool = Field(
        False,
        description="Enable conversation history persistence (DuckDB local, PostgreSQL production)",
        validation_alias=AliasChoices("FEATURE_CHAT_HISTORY_ENABLED"),
    )
    chat_history_db_url: str = Field(
        default="duckdb:///data/chat_history.db",
        description="Database URL for chat history. Use duckdb:///path for local, postgresql://... for production",
        validation_alias="CHAT_HISTORY_DB_URL",
    )
    # Compliance level filtering feature gate
    feature_compliance_levels_enabled: bool = Field(
        False,
        description="Enable compliance level filtering for MCP servers and data sources",
        validation_alias=AliasChoices("FEATURE_COMPLIANCE_LEVELS_ENABLED"),
    )
    # Email domain whitelist feature gate
    feature_domain_whitelist_enabled: bool = Field(
        False,
        description="Enable email domain whitelist restriction (configured in domain-whitelist.json)",
        validation_alias=AliasChoices("FEATURE_DOMAIN_WHITELIST_ENABLED", "FEATURE_DOE_LAB_CHECK_ENABLED"),
    )
    # File content extraction feature gate
    feature_file_content_extraction_enabled: bool = Field(
        False,
        description="Enable automatic content extraction from uploaded files (PDFs, images)",
        validation_alias=AliasChoices("FEATURE_FILE_CONTENT_EXTRACTION_ENABLED"),
    )

    # Capability tokens (for headless access to downloads/iframes)
    capability_token_secret: str = ""
    capability_token_ttl_seconds: int = 3600

    # Backend URL configuration for MCP server file access
    # This should be the publicly accessible URL of the backend API
    # Example: "https://atlas-ui.example.com" or "http://localhost:8000"
    # If not set, relative URLs will be used (only works for local/stdio servers)
    backend_public_url: Optional[str] = Field(
        default=None,
        description="Public URL of the backend API for file downloads by remote MCP servers",
        validation_alias="BACKEND_PUBLIC_URL",
    )

    # Whether to include base64 file content as fallback in tool arguments
    # This allows MCP servers to access files even if they cannot reach the backend URL
    # WARNING: Enabling this can significantly increase message sizes for large files
    include_file_content_base64: bool = Field(
        default=False,
        description="Include base64 encoded file content in tool arguments as fallback",
        validation_alias="INCLUDE_FILE_CONTENT_BASE64",
    )

    # Rate limiting (global middleware)
    rate_limit_rpm: int = Field(default=600, validation_alias="RATE_LIMIT_RPM")
    rate_limit_window_seconds: int = Field(default=60, validation_alias="RATE_LIMIT_WINDOW_SECONDS")
    rate_limit_per_path: bool = Field(default=False, validation_alias="RATE_LIMIT_PER_PATH")

    # Security headers toggles (HSTS intentionally omitted)
    security_csp_enabled: bool = Field(default=True, validation_alias="SECURITY_CSP_ENABLED")
    security_csp_value: str | None = Field(
        default="default-src 'self'; img-src 'self' data:; font-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'",
        validation_alias="SECURITY_CSP_VALUE",
    )
    security_xfo_enabled: bool = Field(default=True, validation_alias="SECURITY_XFO_ENABLED")
    security_xfo_value: str = Field(default="SAMEORIGIN", validation_alias="SECURITY_XFO_VALUE")
    security_nosniff_enabled: bool = Field(default=True, validation_alias="SECURITY_NOSNIFF_ENABLED")
    security_referrer_policy_enabled: bool = Field(default=True, validation_alias="SECURITY_REFERRER_POLICY_ENABLED")
    security_referrer_policy_value: str = Field(default="no-referrer", validation_alias="SECURITY_REFERRER_POLICY_VALUE")

    # Prompt / template settings
    prompt_base_path: str = "prompts"  # Relative or absolute path to directory containing prompt templates
    system_prompt_filename: str = "system_prompt.md"  # Filename for system prompt template
    tool_synthesis_prompt_filename: str = "tool_synthesis_prompt.md"  # Filename for tool synthesis prompt template
    # Agent prompts
    agent_reason_prompt_filename: str = "agent_reason_prompt.md"  # Filename for agent reason phase
    agent_observe_prompt_filename: str = "agent_observe_prompt.md"  # Filename for agent observe phase

    # Config file names (can be overridden via environment variables)
    mcp_config_file: str = Field(default="mcp.json", validation_alias="MCP_CONFIG_FILE")
    rag_sources_config_file: str = Field(default="rag-sources.json", validation_alias="RAG_SOURCES_CONFIG_FILE")
    llm_config_file: str = Field(default="llmconfig.yml", validation_alias="LLM_CONFIG_FILE")
    help_config_file: str = Field(default="help-config.json", validation_alias="HELP_CONFIG_FILE")
    messages_config_file: str = Field(default="messages.txt", validation_alias="MESSAGES_CONFIG_FILE")
    tool_approvals_config_file: str = Field(default="tool-approvals.json", validation_alias="TOOL_APPROVALS_CONFIG_FILE")
    splash_config_file: str = Field(default="splash-config.json", validation_alias="SPLASH_CONFIG_FILE")
    file_extractors_config_file: str = Field(default="file-extractors.json", validation_alias="FILE_EXTRACTORS_CONFIG_FILE")

    # Config directory path (user customizations; falls back to atlas/config/ for defaults)
    app_config_dir: str = Field(default="config", validation_alias="APP_CONFIG_DIR")

    # Logging directory
    app_log_dir: Optional[str] = Field(default=None, validation_alias="APP_LOG_DIR")

    # Environment mode
    environment: str = Field(default="production", validation_alias="ENVIRONMENT")

    # Prompt injection risk thresholds
    # NOT USED RIGHT NOW.
    pi_threshold_low: int = Field(default=30, validation_alias="PI_THRESHOLD_LOW")
    pi_threshold_medium: int = Field(default=50, validation_alias="PI_THRESHOLD_MEDIUM")
    pi_threshold_high: int = Field(default=80, validation_alias="PI_THRESHOLD_HIGH")

    # Runtime directories
    runtime_feedback_dir: Optional[str] = Field(default=None, validation_alias="RUNTIME_FEEDBACK_DIR")

    @model_validator(mode='after')
    def validate_aws_alb_config(self):
        """Validate that AWS ALB ARN is properly configured when using aws-alb-jwt auth."""
        if self.auth_user_header_type == "aws-alb-jwt":
            placeholder = "arn:aws:elasticloadbalancing:us-east-1:123456789012:loadbalancer/app/your-alb-name/..."
            if not self.auth_aws_expected_alb_arn or self.auth_aws_expected_alb_arn == placeholder:
                raise ValueError(
                    "auth_aws_expected_alb_arn must be set to a valid AWS ALB ARN when auth_user_header_type is 'aws-alb-jwt'. "
                    "Current value is empty or a placeholder. Set AUTH_AWS_EXPECTED_ALB_ARN environment variable."
                )
        return self

    model_config = {
        "env_file": "../.env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    "env_prefix": "",
    }


class ConfigManager:
    """Centralized configuration manager with proper error handling."""

    def __init__(self, atlas_root: Optional[Path] = None):
        self._atlas_root = atlas_root or Path(__file__).parent.parent.parent
        self._app_settings: Optional[AppSettings] = None
        self._llm_config: Optional[LLMConfig] = None
        self._mcp_config: Optional[MCPConfig] = None
        self._rag_mcp_config: Optional[MCPConfig] = None
        self._rag_sources_config: Optional[RAGSourcesConfig] = None
        self._tool_approvals_config: Optional[ToolApprovalsConfig] = None
        self._file_extractors_config: Optional[FileExtractorsConfig] = None

    def _search_paths(self, file_name: str) -> List[Path]:
        """Generate search paths for a configuration file.

        Two-layer lookup:
        1. User config dir (APP_CONFIG_DIR, default "config/") - user customizations
        2. Package defaults (atlas/config/) - always available as fallback
        """
        project_root = self._atlas_root.parent

        config_dir = Path(self.app_settings.app_config_dir)
        if not config_dir.is_absolute():
            config_dir_project = project_root / config_dir
        else:
            config_dir_project = config_dir

        package_defaults = self._atlas_root / "config" / file_name

        candidates: List[Path] = [
            config_dir / file_name,
            config_dir_project / file_name,
            package_defaults,
        ]

        seen = set()
        search_paths: List[Path] = []
        for p in candidates:
            if p not in seen:
                seen.add(p)
                search_paths.append(p)

        logger.debug(
            "Config search paths for %s: %s", file_name, [str(p) for p in search_paths]
        )
        return search_paths

    def _load_file_with_error_handling(self, file_paths: List[Path], file_type: str) -> Optional[Dict[str, Any]]:
        """Load a file with comprehensive error handling and logging."""
        for path in file_paths:
            try:
                if not path.exists():
                    continue

                logger.info(f"Found {file_type} config at: {path.absolute()}")

                with open(path, "r", encoding="utf-8") as f:
                    if file_type.lower() == "yaml":
                        data = yaml.safe_load(f)
                    elif file_type.lower() == "json":
                        data = json.load(f)
                    else:
                        raise ValueError(f"Unsupported file type: {file_type}")

                if not isinstance(data, dict):
                    logger.error(
                        f"Invalid {file_type} format in {path}: expected dict, got {type(data)}",
                        exc_info=True
                    )
                    continue

                logger.info(f"Successfully loaded {file_type} config from {path}")
                return data

            except (yaml.YAMLError, json.JSONDecodeError) as e:
                logger.error(f"{file_type} parsing error in {path}: {e}", exc_info=True)
                continue
            except Exception as e:
                logger.error(f"Unexpected error reading {path}: {e}", exc_info=True)
                continue

        logger.warning(f"{file_type} config not found in any of these locations: {[str(p) for p in file_paths]}")
        return None

    @property
    def app_settings(self) -> AppSettings:
        """Get application settings (cached)."""
        if self._app_settings is None:
            try:
                self._app_settings = AppSettings()
                logger.info("Application settings loaded successfully")
            except Exception as e:
                logger.error(f"Failed to load application settings: {e}", exc_info=True)
                # Create default settings as fallback
                self._app_settings = AppSettings()
        return self._app_settings

    @property
    def llm_config(self) -> LLMConfig:
        """Get LLM configuration (cached)."""
        if self._llm_config is None:
            try:
                # Use config filename from app settings
                llm_filename = self.app_settings.llm_config_file
                file_paths = self._search_paths(llm_filename)
                data = self._load_file_with_error_handling(file_paths, "YAML")

                if data:
                    self._llm_config = LLMConfig(**data)
                    # Validate compliance levels
                    self._validate_llm_compliance_levels()
                    logger.info(f"Loaded {len(self._llm_config.models)} models from LLM config")
                else:
                    self._llm_config = LLMConfig(models={})
                    logger.info("Created empty LLM config (no configuration file found)")

            except Exception as e:
                logger.error(f"Failed to parse LLM configuration: {e}", exc_info=True)
                self._llm_config = LLMConfig(models={})

        return self._llm_config

    def _validate_llm_compliance_levels(self):
        """Validate compliance levels for all LLM models."""
        try:
            # Standardize on running from atlas/ directory (agent_start.sh)
            # Use non-prefixed imports so they resolve when cwd=backend
            from atlas.core.compliance import get_compliance_manager
            compliance_mgr = get_compliance_manager()

            for model_name, model_config in self._llm_config.models.items():
                if model_config.compliance_level:
                    validated = compliance_mgr.validate_compliance_level(
                        model_config.compliance_level,
                        context=f"for LLM model '{model_name}'"
                    )
                    # Update to canonical name or None if invalid
                    model_config.compliance_level = validated
        except Exception as e:
            logger.warning(f"Could not validate LLM compliance levels: {e}")

    @property
    def mcp_config(self) -> MCPConfig:
        """Get MCP configuration (cached)."""
        if self._mcp_config is None:
            try:
                # Use config filename from app settings
                mcp_filename = self.app_settings.mcp_config_file
                file_paths = self._search_paths(mcp_filename)
                data = self._load_file_with_error_handling(file_paths, "JSON")

                if data:
                    # Convert flat structure to nested structure for Pydantic
                    servers_data = {"servers": data}
                    self._mcp_config = MCPConfig(**servers_data)
                    # Validate compliance levels
                    self._validate_mcp_compliance_levels(self._mcp_config, "MCP")
                    logger.info(f"Loaded MCP config with {len(self._mcp_config.servers)} servers: {list(self._mcp_config.servers.keys())}")
                else:
                    self._mcp_config = MCPConfig()
                    logger.info("Created empty MCP config (no configuration file found)")

            except Exception as e:
                logger.error(f"Failed to parse MCP configuration: {e}", exc_info=True)
                self._mcp_config = MCPConfig()

        return self._mcp_config

    @property
    def rag_mcp_config(self) -> MCPConfig:
        """Get RAG MCP configuration (cached) derived from rag-sources.json.

        Extracts MCP-type sources from rag_sources_config and converts them
        to MCPServerConfig format for compatibility with RAGMCPService.
        Returns an empty config when FEATURE_RAG_ENABLED is false.
        """
        if not self.app_settings.feature_rag_enabled:
            if self._rag_mcp_config is None:
                self._rag_mcp_config = MCPConfig()
            return self._rag_mcp_config

        if self._rag_mcp_config is None:
            try:
                # Get all RAG sources and filter to MCP type only
                rag_sources = self.rag_sources_config
                mcp_servers: Dict[str, MCPServerConfig] = {}

                for name, source in rag_sources.sources.items():
                    if source.type != "mcp":
                        continue
                    if not source.enabled:
                        continue

                    # Convert RAGSourceConfig to MCPServerConfig
                    mcp_servers[name] = MCPServerConfig(
                        description=source.description,
                        groups=source.groups,
                        enabled=source.enabled,
                        command=source.command,
                        cwd=source.cwd,
                        env=source.env,
                        url=source.url,
                        transport=source.transport,
                        auth_token=source.auth_token,
                        compliance_level=source.compliance_level,
                    )

                self._rag_mcp_config = MCPConfig(servers=mcp_servers)

                if mcp_servers:
                    # Validate compliance levels
                    self._validate_mcp_compliance_levels(self._rag_mcp_config, "RAG MCP")
                    logger.info(
                        "Loaded RAG MCP config with %d servers from rag-sources.json: %s",
                        len(mcp_servers),
                        list(mcp_servers.keys())
                    )
                else:
                    logger.info("No MCP-type RAG sources found in rag-sources.json")

            except Exception as e:
                logger.error("Failed to build RAG MCP configuration: %s", e, exc_info=True)
                self._rag_mcp_config = MCPConfig()

        return self._rag_mcp_config

    @property
    def rag_sources_config(self) -> RAGSourcesConfig:
        """Get unified RAG sources configuration (cached) from rag-sources.json.

        This config supports both MCP-based and HTTP REST API RAG sources.
        Returns an empty config when FEATURE_RAG_ENABLED is false.
        """
        if not self.app_settings.feature_rag_enabled:
            if self._rag_sources_config is None:
                self._rag_sources_config = RAGSourcesConfig()
                logger.info("RAG sources config skipped (FEATURE_RAG_ENABLED=false)")
            return self._rag_sources_config

        if self._rag_sources_config is None:
            try:
                rag_filename = self.app_settings.rag_sources_config_file
                file_paths = self._search_paths(rag_filename)
                data = self._load_file_with_error_handling(file_paths, "JSON")

                if data:
                    sources_data = {"sources": data}
                    self._rag_sources_config = RAGSourcesConfig(**sources_data)
                    # Validate compliance levels
                    self._validate_rag_sources_compliance_levels(self._rag_sources_config)
                    logger.info(
                        "Loaded RAG sources config with %d sources: %s",
                        len(self._rag_sources_config.sources),
                        list(self._rag_sources_config.sources.keys())
                    )
                else:
                    self._rag_sources_config = RAGSourcesConfig()
                    logger.info("Created empty RAG sources config (no configuration file found)")

            except Exception as e:
                logger.error("Failed to parse RAG sources configuration: %s", e, exc_info=True)
                self._rag_sources_config = RAGSourcesConfig()

        return self._rag_sources_config

    def _validate_rag_sources_compliance_levels(self, config: RAGSourcesConfig) -> None:
        """Validate that RAG source compliance levels are defined."""
        from atlas.core.compliance import get_compliance_manager
        try:
            compliance_mgr = get_compliance_manager()
            for source_name, source_config in config.sources.items():
                level = source_config.compliance_level
                if level and not compliance_mgr.is_valid_level(level):
                    logger.warning(
                        "RAG source '%s' has unknown compliance level: %s",
                        source_name,
                        level
                    )
        except Exception as e:
            logger.debug("Compliance validation skipped for RAG sources: %s", e)

    @property
    def tool_approvals_config(self) -> ToolApprovalsConfig:
        """Get tool approvals configuration built from mcp.json and env variables (cached)."""
        if self._tool_approvals_config is None:
            try:
                # Get default from environment
                default_require_approval = self.app_settings.require_tool_approval_by_default

                # Build tool-specific configs from MCP servers (Option B):
                # Only include entries explicitly listed under require_approval.
                tools_config: Dict[str, ToolApprovalConfig] = {}

                for server_name, server_config in self.mcp_config.servers.items():
                    require_approval_list = server_config.require_approval or []

                    for tool_name in require_approval_list:
                        full_tool_name = f"{server_name}_{tool_name}"
                        # Mark as explicitly requiring approval; allow_edit is moot for requirement
                        tools_config[full_tool_name] = ToolApprovalConfig(
                            require_approval=True,
                            allow_edit=True  # UI always allows edits; keep True for compatibility
                        )

                self._tool_approvals_config = ToolApprovalsConfig(
                    require_approval_by_default=default_require_approval,
                    tools=tools_config
                )
                logger.info(f"Built tool approvals config from mcp.json with {len(tools_config)} tool-specific settings (default: {default_require_approval})")

            except Exception as e:
                logger.error(f"Failed to build tool approvals configuration: {e}", exc_info=True)
                self._tool_approvals_config = ToolApprovalsConfig()

        return self._tool_approvals_config

    @property
    def file_extractors_config(self) -> FileExtractorsConfig:
        """Get file extractors configuration (cached)."""
        if self._file_extractors_config is None:
            try:
                extractors_filename = self.app_settings.file_extractors_config_file
                file_paths = self._search_paths(extractors_filename)
                data = self._load_file_with_error_handling(file_paths, "JSON")

                if data:
                    self._file_extractors_config = FileExtractorsConfig(**data)
                    # Resolve environment variables in extractor configs
                    self._resolve_file_extractor_env_vars()
                    logger.info(
                        f"Loaded file extractors config with {len(self._file_extractors_config.extractors)} extractors"
                    )
                else:
                    # Return disabled config if file not found
                    self._file_extractors_config = FileExtractorsConfig(enabled=False)
                    logger.info("File extractors config not found, using disabled defaults")

            except Exception as e:
                logger.error(f"Failed to parse file extractors configuration: {e}", exc_info=True)
                self._file_extractors_config = FileExtractorsConfig(enabled=False)

        return self._file_extractors_config

    def _resolve_file_extractor_env_vars(self) -> None:
        """Resolve environment variables in file extractor configurations.

        Supports ${ENV_VAR} syntax for:
        - url: Extractor service URL (required - extractor disabled if not set)
        - api_key: Authentication API key (optional - None if not set)
        - headers: Header values (optional - omitted if not set)
        """
        if self._file_extractors_config is None:
            return

        for extractor_name, extractor in self._file_extractors_config.extractors.items():
            try:
                # Resolve URL if it contains env var pattern (required)
                if extractor.url:
                    resolved_url = resolve_env_var(extractor.url, required=True)
                    if resolved_url != extractor.url:
                        extractor.url = resolved_url
                        logger.debug(f"Resolved URL env var for extractor '{extractor_name}'")

            except ValueError as e:
                logger.error(f"Failed to resolve URL env var for extractor '{extractor_name}': {e}")
                # Disable the extractor if URL env var resolution fails
                extractor.enabled = False
                continue

            # Resolve API key if it contains env var pattern (optional)
            if extractor.api_key:
                resolved_key = resolve_env_var(extractor.api_key, required=False)
                if resolved_key is None:
                    logger.debug(f"API key env var not set for extractor '{extractor_name}', will make unauthenticated requests")
                    extractor.api_key = None
                elif resolved_key != extractor.api_key:
                    extractor.api_key = resolved_key
                    logger.debug(f"Resolved API key env var for extractor '{extractor_name}'")

            # Resolve header values if they contain env var patterns (optional)
            if extractor.headers:
                resolved_headers = {}
                for header_name, header_value in extractor.headers.items():
                    resolved_value = resolve_env_var(header_value, required=False)
                    if resolved_value is not None:
                        resolved_headers[header_name] = resolved_value
                        if resolved_value != header_value:
                            logger.debug(f"Resolved header '{header_name}' env var for extractor '{extractor_name}'")
                    else:
                        logger.debug(f"Header '{header_name}' env var not set for extractor '{extractor_name}', omitting header")
                extractor.headers = resolved_headers if resolved_headers else None

    def _validate_mcp_compliance_levels(self, config: MCPConfig, config_type: str):
        """Validate compliance levels for all MCP servers."""
        try:
            # Standardize on running from atlas/ directory (agent_start.sh)
            from atlas.core.compliance import get_compliance_manager
            compliance_mgr = get_compliance_manager()

            for server_name, server_config in config.servers.items():
                if server_config.compliance_level:
                    validated = compliance_mgr.validate_compliance_level(
                        server_config.compliance_level,
                        context=f"for {config_type} server '{server_name}'"
                    )
                    # Update to canonical name or None if invalid
                    server_config.compliance_level = validated
        except Exception as e:
            logger.warning(f"Could not validate {config_type} compliance levels: {e}")

    def reload_configs(self) -> None:
        """Reload all configurations from files."""
        self._app_settings = None
        self._llm_config = None
        self._mcp_config = None
        self._rag_mcp_config = None
        self._rag_sources_config = None
        self._tool_approvals_config = None
        self._file_extractors_config = None
        logger.info("Configuration cache cleared, will reload on next access")

    def reload_mcp_config(self) -> MCPConfig:
        """Reload MCP configuration from disk.

        This clears the cached MCP config and forces a reload from the config file.
        Used for hot-reloading MCP server configuration without restarting the application.

        Returns:
            The newly loaded MCPConfig
        """
        self._mcp_config = None
        self._tool_approvals_config = None  # Also clear tool approvals since they depend on MCP
        logger.info("MCP configuration cache cleared, reloading from disk")
        return self.mcp_config

    def validate_config(self) -> Dict[str, bool]:
        """Validate all configurations and return status."""
        status = {}

        try:
            self.app_settings
            status["app_settings"] = True
        except Exception as e:
            logger.error(f"App settings validation failed: {e}", exc_info=True)
            status["app_settings"] = False

        try:
            llm_config = self.llm_config
            status["llm_config"] = len(llm_config.models) > 0
            if not status["llm_config"]:
                logger.warning("LLM config is valid but contains no models")
        except Exception as e:
            logger.error(f"LLM config validation failed: {e}", exc_info=True)
            status["llm_config"] = False

        try:
            mcp_config = self.mcp_config
            status["mcp_config"] = len(mcp_config.servers) > 0
            if not status["mcp_config"]:
                logger.warning("MCP config is valid but contains no servers")
        except Exception as e:
            logger.error(f"MCP config validation failed: {e}", exc_info=True)
            status["mcp_config"] = False

        return status


# Global configuration manager instance
config_manager = ConfigManager()


# Convenience functions for easy access
def get_app_settings() -> AppSettings:
    """Get application settings."""
    return config_manager.app_settings


def get_llm_config() -> LLMConfig:
    """Get LLM configuration."""
    return config_manager.llm_config


def get_mcp_config() -> MCPConfig:
    """Get MCP configuration."""
    return config_manager.mcp_config


def get_file_extractors_config() -> FileExtractorsConfig:
    """Get file extractors configuration."""
    return config_manager.file_extractors_config
