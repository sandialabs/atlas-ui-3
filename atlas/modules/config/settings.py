"""Application settings: the ``AppSettings`` pydantic-settings model (loaded from env + ``.env``)."""

import logging
import sys
from typing import Optional

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


def build_db_url_from_parts(
    db_driver: str = "postgresql",
    db_host: Optional[str] = None,
    db_port: Optional[int | str] = None,
    db_name: Optional[str] = None,
    db_user: Optional[str] = None,
    db_password: Optional[str] = None,
) -> Optional[str]:
    """Build a SQLAlchemy database URL from DB_* components, if any are set."""
    if not (db_host or db_name or db_user):
        return None

    from urllib.parse import quote

    user_part = ""
    if db_user:
        user_part = quote(db_user, safe="")
        if db_password is not None:
            user_part += ":" + quote(db_password, safe="")
        user_part += "@"

    host_part = db_host or "localhost"
    port_part = f":{db_port}" if db_port else ""
    name_part = f"/{db_name}" if db_name else ""

    return f"{db_driver}://{user_part}{host_part}{port_part}{name_part}"


class AppSettings(BaseSettings):
    """Main application settings loaded from environment variables."""

    # Application settings
    app_name: str = "ATLAS"
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
    feature_atlas_rag_tools_enabled: bool = Field(
        False,
        description=(
            "Expose the atlas_rag pseudo-server/tools when general RAG is enabled. "
            "Requires FEATURE_RAG_ENABLED=true."
        ),
        validation_alias=AliasChoices("FEATURE_ATLAS_RAG_TOOLS_ENABLED"),
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
        default="agentic",
        description=(
            "Agent loop strategy. Only 'agentic' (native tool_choice=auto loop) "
            "is supported; retained for backward compatibility."
        ),
        validation_alias=AliasChoices("AGENT_LOOP_STRATEGY"),
    )
    # Backward compatibility: support old AGENT_MODE_AVAILABLE env if present
    @property
    def agent_mode_available(self) -> bool:
        """Maintain backward compatibility for code still referencing agent_mode_available."""
        return self.feature_agent_mode_available

    # Standard (non-agent) tools mode: how many ADDITIONAL tool-calling rounds
    # the model may take after its first round before the turn is finalized.
    # 0 keeps the classic single-round behavior. The default of 3 lets the model
    # chain a few dependent tool calls (e.g. compute a value, then use it) without
    # enabling full Agent Mode. An anti-loop guard refuses repeated identical tool
    # calls, so this cannot spin on the same tool. Admins tune this with no code
    # change via TOOLS_MODE_MAX_EXTRA_ROUNDS.
    tools_mode_max_extra_rounds: int = Field(
        default=3,
        ge=0,
        description="Max additional tool-calling rounds in standard tools mode (0 = single round).",
        validation_alias=AliasChoices("TOOLS_MODE_MAX_EXTRA_ROUNDS"),
    )

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
    mcp_task_timeout: float = Field(
        default=10.0,
        description="Seconds to wait synchronously before switching to background task polling",
        validation_alias="MCP_TASK_TIMEOUT"
    )
    mcp_user_client_cache_max_entries: int = Field(
        default=1000,
        description="Maximum cached per-user/per-conversation MCP HTTP clients",
        validation_alias="MCP_USER_CLIENT_CACHE_MAX_ENTRIES",
    )
    mcp_user_client_cache_idle_ttl_seconds: int = Field(
        default=3600,
        description="Seconds before an idle cached MCP HTTP client is evicted",
        validation_alias="MCP_USER_CLIENT_CACHE_IDLE_TTL_SECONDS",
    )
    mcp_user_client_cache_sweep_interval_seconds: int = Field(
        default=300,
        description="Interval in seconds between idle MCP HTTP client cache sweeps",
        validation_alias="MCP_USER_CLIENT_CACHE_SWEEP_INTERVAL_SECONDS",
    )
    mcp_user_client_cache_in_use_window_seconds: int = Field(
        default=60,
        description=(
            "LRU eviction skips cached MCP HTTP clients touched within this many "
            "seconds; tool calls in flight should not have their connection torn "
            "down by the cache-bound enforcer. Allows temporary cache overflow."
        ),
        validation_alias="MCP_USER_CLIENT_CACHE_IN_USE_WINDOW_SECONDS",
    )
    mcp_user_client_close_timeout_seconds: float = Field(
        default=5.0,
        description=(
            "Maximum seconds to wait for a single cached MCP HTTP client to close. "
            "Bounds sweeper iteration and shutdown so a stuck server cannot hang "
            "Atlas teardown."
        ),
        validation_alias="MCP_USER_CLIENT_CLOSE_TIMEOUT_SECONDS",
    )
    websocket_keepalive_interval_seconds: int = Field(
        default=30,
        description="Interval in seconds for WebSocket ping keepalives; maps to Uvicorn's ws_ping_interval and ws_ping_timeout settings",
        validation_alias="WEBSOCKET_KEEPALIVE_INTERVAL_SECONDS",
    )

    # MCP Token Storage settings
    mcp_token_storage_dir: Optional[str] = Field(
        default=None,
        description="Directory for storing encrypted user tokens. Defaults to config/secure/",
        validation_alias="MCP_TOKEN_STORAGE_DIR"
    )
    mcp_token_encryption_key: Optional[str] = Field(
        default=None,
        description="Encryption key for user tokens. Required: Atlas refuses to start without it",
        validation_alias="MCP_TOKEN_ENCRYPTION_KEY"
    )

    # Admin settings
    admin_group: str = "admin"
    test_user: str = "test@test.com"  # Test user for development
    admin_test_user: str = Field(
        default="admin@example.com",
        validation_alias="ADMIN_TEST_USER",
        description="Admin test user for development/test auth flows"
    )
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
        default=True,
        description="Enable proxy secret validation to ensure requests come from trusted reverse proxy. "
                    "Enabled by default to prevent direct backend access from spoofing auth headers. "
                    "Set PROXY_SECRET to a strong random value, or explicitly disable with "
                    "FEATURE_PROXY_SECRET_ENABLED=false if the backend is network-isolated.",
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

    # Wormhole authentication settings.
    # A Wormhole-wrapped Atlas receives a JWT carrying a unique subtoken header
    # (default ``x-subtoken``) that must be forwarded to Wormhole-enabled MCP
    # servers as the configured forward header (default ``X-Token``).
    feature_wormhole_enabled: bool = Field(
        default=False,
        description="Enable Wormhole subtoken capture/forwarding for Wormhole-enabled MCP servers",
        validation_alias="FEATURE_WORMHOLE_ENABLED",
    )
    wormhole_subtoken_header: str = Field(
        default="x-subtoken",
        description="Incoming request/WebSocket header carrying the Wormhole subtoken",
        validation_alias="WORMHOLE_SUBTOKEN_HEADER",
    )
    wormhole_forward_header: str = Field(
        default="X-Token",
        description="Header used to forward the Wormhole subtoken to MCP servers",
        validation_alias="WORMHOLE_FORWARD_HEADER",
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
        default="",
        description="Secret key for Globus session middleware. Must be set to a strong random "
                    "value when FEATURE_GLOBUS_AUTH_ENABLED=true. Globus auth will not start "
                    "without this.",
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
    max_file_upload_size_mb: int = Field(
        default=250,
        ge=1,
        description="Maximum user-uploaded file size in MiB.",
        validation_alias=AliasChoices("MAX_FILE_UPLOAD_SIZE_MB", "MAX_FILE_SIZE_MB"),
    )

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
    feature_custom_prompts_enabled: bool = Field(
        False,
        description="Enable the per-user custom prompt library UI and API",
        validation_alias=AliasChoices("FEATURE_CUSTOM_PROMPTS_ENABLED"),
    )

    @property
    def custom_prompts_effective(self) -> bool:
        """Whether the custom prompt library is actually usable.

        Custom prompts persist as per-user library entries, so the feature is
        only effective when chat history is also enabled. This single derived
        flag is the authoritative gate used by the config payload, the prompt
        CRUD routes, and the chat WebSocket path so they cannot drift apart.
        """
        return bool(
            self.feature_custom_prompts_enabled and self.feature_chat_history_enabled
        )
    chat_history_db_url: str = Field(
        default="duckdb:///data/chat_history.db",
        description="Database URL for chat history. Use duckdb:///path for local, postgresql://... for production",
        validation_alias="CHAT_HISTORY_DB_URL",
    )
    # Individual database connection components (alternative to CHAT_HISTORY_DB_URL).
    # When chat_history_db_url is not explicitly provided by any source (process env,
    # .env file, or init kwargs) but at least one of DB_HOST / DB_NAME / DB_USER is,
    # chat_history_db_url is assembled from these parts in a model validator below.
    db_driver: str = Field(
        default="postgresql",
        description="SQLAlchemy driver scheme used when assembling chat_history_db_url from DB_* parts",
        validation_alias="DB_DRIVER",
    )
    db_host: Optional[str] = Field(default=None, validation_alias="DB_HOST")
    db_port: Optional[int] = Field(default=None, validation_alias="DB_PORT")
    db_name: Optional[str] = Field(default=None, validation_alias="DB_NAME")
    db_user: Optional[str] = Field(default=None, validation_alias="DB_USER")
    db_password: Optional[str] = Field(default=None, validation_alias="DB_PASSWORD")
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
    # Follow-up question suggestions feature gate
    feature_followup_suggestions_enabled: bool = Field(
        False,
        description="Enable AI-generated follow-up question suggestions after each chat response",
        validation_alias=AliasChoices("FEATURE_FOLLOWUP_SUGGESTIONS_ENABLED"),
    )
    # Agent Portal feature gate (launch and stream host processes from the UI)
    feature_agent_portal_enabled: bool = Field(
        False,
        description="Enable the Agent Portal UI for launching and streaming host processes",
        validation_alias=AliasChoices("FEATURE_AGENT_PORTAL_ENABLED"),
    )
    # Additional Origin header hosts (beyond loopback) allowed to open the
    # agent_portal WebSocket stream. Comma-separated list of hostnames, e.g.
    # "atlas-dev.example.com,atlas.internal". Loopback hosts are always allowed.
    # Only set this when the deployment is fronted by an auth proxy (e.g.
    # Cloudflare Access) — the WS upgrade bypasses CORS, so an attacker page
    # on any listed origin can drive the socket if it can reach the backend.
    agent_portal_allowed_origins: str = Field(
        default="",
        description="Comma-separated extra Origin hostnames allowed for agent_portal WS",
        validation_alias=AliasChoices("AGENT_PORTAL_ALLOWED_ORIGINS"),
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
    prompt_base_path: str = "config/prompts"  # Relative or absolute path to directory containing prompt templates
    system_prompt_filename: str = "system_prompt.md"  # Filename for system prompt template
    tool_synthesis_prompt_filename: str = "tool_synthesis_prompt.md"  # Filename for tool synthesis prompt template
    # Agent prompts
    agent_reason_prompt_filename: str = "agent_reason_prompt.md"  # Filename for agent reason phase
    agent_observe_prompt_filename: str = "agent_observe_prompt.md"  # Filename for agent observe phase

    # Config file names (can be overridden via environment variables)
    mcp_config_file: str = Field(default="mcp.json", validation_alias="MCP_CONFIG_FILE")
    rag_sources_config_file: str = Field(default="rag-sources.json", validation_alias="RAG_SOURCES_CONFIG_FILE")
    llm_config_file: str = Field(default="llmconfig.yml", validation_alias="LLM_CONFIG_FILE")
    help_config_file: str = Field(default="help.md", validation_alias="HELP_CONFIG_FILE")
    messages_config_file: str = Field(default="messages.txt", validation_alias="MESSAGES_CONFIG_FILE")
    tool_approvals_config_file: str = Field(default="tool-approvals.json", validation_alias="TOOL_APPROVALS_CONFIG_FILE")
    splash_config_file: str = Field(default="splash-config.json", validation_alias="SPLASH_CONFIG_FILE")
    splash_screen_file: str = Field(default="splash-screen.md", validation_alias="SPLASH_SCREEN_FILE")
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

    # Opt-in fine-tune capture (off by default; also gated per-user by consent).
    # When enabled, full LLM I/O for opted-in users is recorded for fine-tuning.
    feature_finetune_capture_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("FEATURE_FINETUNE_CAPTURE_ENABLED"),
        description="System gate for opt-in fine-tune data capture.",
    )
    runtime_capture_dir: Optional[str] = Field(
        default=None, validation_alias="RUNTIME_CAPTURE_DIR"
    )
    capture_user_salt: Optional[str] = Field(
        default=None,
        validation_alias="CAPTURE_USER_SALT",
        description="Salt for pseudonymizing user identifiers in capture records.",
    )

    @model_validator(mode='after')
    def assemble_chat_history_db_url(self):
        """Build chat_history_db_url from DB_* parts when no full URL is supplied.

        An explicitly-provided chat_history_db_url always wins, regardless of which
        pydantic-settings source supplied it (process env, .env file, or init kwargs).
        We detect "explicitly provided" via self.model_fields_set, which excludes
        the field's static default. Otherwise, if at least one of
        DB_HOST / DB_NAME / DB_USER is set we treat the operator as opting in to
        component-based config and assemble a SQLAlchemy URL from the parts. User and
        password are URL-encoded so special characters (e.g. '@', ':', '/') are safe.
        """
        if "chat_history_db_url" in self.model_fields_set:
            return self
        if not (self.db_host or self.db_name or self.db_user):
            return self

        self.chat_history_db_url = build_db_url_from_parts(
            db_driver=self.db_driver,
            db_host=self.db_host,
            db_port=self.db_port,
            db_name=self.db_name,
            db_user=self.db_user,
            db_password=self.db_password,
        )
        return self

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

    @model_validator(mode='after')
    def disable_agent_portal_on_windows(self):
        """Treat Agent Portal as unavailable on Windows hosts."""
        if self.feature_agent_portal_enabled and sys.platform.startswith("win"):
            logger.warning(
                "FEATURE_AGENT_PORTAL_ENABLED=true ignored because Agent Portal is not supported on Windows."
            )
            self.feature_agent_portal_enabled = False
        return self

    @model_validator(mode='after')
    def validate_agent_portal_dev_only(self):
        """Refuse to boot with Agent Portal enabled outside debug mode.

        The feature is a dev-preview that grants any authenticated caller
        arbitrary command execution on the host. See
        docs/agentportal/threat-model.md for the full rationale.
        """
        if self.feature_agent_portal_enabled and not self.debug_mode:
            logging.getLogger(__name__).error(
                "SECURITY: FEATURE_AGENT_PORTAL_ENABLED=true but DEBUG_MODE=false. "
                "The Agent Portal is a dev-only preview and must not run outside debug mode. "
                "See docs/agentportal/threat-model.md. Refusing to start."
            )
            raise ValueError(
                "FEATURE_AGENT_PORTAL_ENABLED is only permitted when DEBUG_MODE=true. "
                "See docs/agentportal/threat-model.md."
            )
        return self

    model_config = {
        "env_file": "../.env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "env_prefix": "",
    }
