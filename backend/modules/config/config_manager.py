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
from typing import Any, Dict, List, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, AliasChoices
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


def resolve_env_var(value: Optional[str]) -> Optional[str]:
    """
    Resolve environment variables in config values.

    Supports patterns like:
    - "${ENV_VAR_NAME}" -> replaced with os.environ.get("ENV_VAR_NAME")
    - "literal-string" -> returned as-is
    - None -> returned as-is

    Args:
        value: Config value that may contain env var pattern

    Returns:
        Resolved value with env vars substituted, or None if value is None

    Raises:
        ValueError: If env var pattern is found but variable is not set
    """
    if value is None:
        return None

    # Pattern: ${VAR_NAME}
    # Uses match() not search() to ensure pattern matches from the beginning of the string
    pattern = r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}'
    match = re.match(pattern, value)

    if match:
        env_var_name = match.group(1)
        env_value = os.environ.get(env_var_name)

        if env_value is None:
            raise ValueError(
                f"Environment variable '{env_var_name}' is not set but required in config"
            )

        return env_value

    # Return literal string if no pattern found
    return value


class ModelConfig(BaseModel):
    """Configuration for a single LLM model."""
    model_name: str
    model_url: str
    api_key: str
    description: Optional[str] = None
    max_tokens: Optional[int] = 10000
    temperature: Optional[float] = 0.7
    # Optional extra HTTP headers (e.g. for providers like OpenRouter)
    extra_headers: Optional[Dict[str, str]] = None
    # Compliance/security level (e.g., "External", "Internal", "Public")
    compliance_level: Optional[str] = None


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
    auth_token: Optional[str] = None     # Bearer token for MCP server authentication
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


class AppSettings(BaseSettings):
    """Main application settings loaded from environment variables."""
    
    # Application settings
    app_name: str = "Chat UI"
    port: int = 8000
    debug_mode: bool = False
    # Logging settings
    log_level: str = "INFO"  # Override default logging level (DEBUG, INFO, WARNING, ERROR)
    
    # RAG settings
    mock_rag: bool = False
    rag_mock_url: str = "http://localhost:8001"
    
    # Banner settings
    banner_enabled: bool = False
    
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
        description="Agent loop strategy selector (react, think-act)",
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
    feature_rag_enabled: bool = False
    feature_tools_enabled: bool = False
    feature_marketplace_enabled: bool = False
    feature_files_panel_enabled: bool = False
    feature_chat_history_enabled: bool = False
    # RAG over MCP feature gate (Phase 1: Discovery)
    feature_rag_mcp_enabled: bool = Field(
        False,
        description="Enable RAG via MCP aggregator (discovery phase)",
        validation_alias=AliasChoices("FEATURE_RAG_MCP_ENABLED", "RAG_MCP_ENABLED"),
    )
    # Compliance level filtering feature gate
    feature_compliance_levels_enabled: bool = Field(
        False,
        description="Enable compliance level filtering for MCP servers and data sources",
        validation_alias=AliasChoices("FEATURE_COMPLIANCE_LEVELS_ENABLED"),
    )

    # Capability tokens (for headless access to downloads/iframes)
    capability_token_secret: str = ""
    capability_token_ttl_seconds: int = 3600

    # Rate limiting (global middleware)
    rate_limit_rpm: int = Field(default=600, validation_alias="RATE_LIMIT_RPM")
    rate_limit_window_seconds: int = Field(default=60, validation_alias="RATE_LIMIT_WINDOW_SECONDS")
    rate_limit_per_path: bool = Field(default=False, validation_alias="RATE_LIMIT_PER_PATH")

    # Security headers toggles (HSTS intentionally omitted)
    security_csp_enabled: bool = Field(default=True, validation_alias="SECURITY_CSP_ENABLED")
    security_csp_value: str | None = Field(
        default="default-src 'self'; img-src 'self' data:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'",
        validation_alias="SECURITY_CSP_VALUE",
    )
    security_xfo_enabled: bool = Field(default=True, validation_alias="SECURITY_XFO_ENABLED")
    security_xfo_value: str = Field(default="SAMEORIGIN", validation_alias="SECURITY_XFO_VALUE")
    security_nosniff_enabled: bool = Field(default=True, validation_alias="SECURITY_NOSNIFF_ENABLED")
    security_referrer_policy_enabled: bool = Field(default=True, validation_alias="SECURITY_REFERRER_POLICY_ENABLED")
    security_referrer_policy_value: str = Field(default="no-referrer", validation_alias="SECURITY_REFERRER_POLICY_VALUE")

    # Prompt / template settings
    prompt_base_path: str = "prompts"  # Relative or absolute path to directory containing prompt templates
    tool_synthesis_prompt_filename: str = "tool_synthesis_prompt.md"  # Filename for tool synthesis prompt template
    # Agent prompts
    agent_reason_prompt_filename: str = "agent_reason_prompt.md"  # Filename for agent reason phase
    agent_observe_prompt_filename: str = "agent_observe_prompt.md"  # Filename for agent observe phase
    
    # Config file names (can be overridden via environment variables)
    mcp_config_file: str = Field(default="mcp.json", validation_alias="MCP_CONFIG_FILE")
    rag_mcp_config_file: str = Field(default="mcp-rag.json", validation_alias="MCP_RAG_CONFIG_FILE")
    llm_config_file: str = Field(default="llmconfig.yml", validation_alias="LLM_CONFIG_FILE")
    help_config_file: str = Field(default="help-config.json", validation_alias="HELP_CONFIG_FILE")
    messages_config_file: str = Field(default="messages.txt", validation_alias="MESSAGES_CONFIG_FILE")
    tool_approvals_config_file: str = Field(default="tool-approvals.json", validation_alias="TOOL_APPROVALS_CONFIG_FILE")
    
    # Config directory paths
    app_config_overrides: str = Field(default="config/overrides", validation_alias="APP_CONFIG_OVERRIDES")
    app_config_defaults: str = Field(default="config/defaults", validation_alias="APP_CONFIG_DEFAULTS")
    
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
    runtime_feedback_dir: str = Field(default="runtime/feedback", validation_alias="RUNTIME_FEEDBACK_DIR")
    
    model_config = {
        "env_file": "../.env", 
        "env_file_encoding": "utf-8", 
        "extra": "ignore",
    "env_prefix": "",
    }


class ConfigManager:
    """Centralized configuration manager with proper error handling."""
    
    def __init__(self, backend_root: Optional[Path] = None):
        self._backend_root = backend_root or Path(__file__).parent.parent.parent
        self._app_settings: Optional[AppSettings] = None
        self._llm_config: Optional[LLMConfig] = None
        self._mcp_config: Optional[MCPConfig] = None
        self._rag_mcp_config: Optional[MCPConfig] = None
        self._tool_approvals_config: Optional[ToolApprovalsConfig] = None
    
    def _search_paths(self, file_name: str) -> List[Path]:
        """Generate common search paths for a configuration file.

        Preferred layout uses project_root/config/overrides and project_root/config/defaults.
        The backend process often runs with CWD=backend/, so relative paths like
        "config/overrides" incorrectly resolve to backend/config/overrides (which doesn't exist).

        Configuration settings can override these directories:
            app_config_overrides, app_config_defaults (can be absolute or relative to project root)

        Legacy fallbacks (backend/configfilesadmin, backend/configfiles) are preserved.
        """
        project_root = self._backend_root.parent  # /workspaces/atlas-ui-3-11

        # Use app_settings for config paths
        overrides_env = self.app_settings.app_config_overrides
        defaults_env = self.app_settings.app_config_defaults

        overrides_root = Path(overrides_env)
        defaults_root = Path(defaults_env)

        # If provided paths are relative, interpret them relative to project root first.
        if not overrides_root.is_absolute():
            overrides_root_project = project_root / overrides_root
        else:
            overrides_root_project = overrides_root
        if not defaults_root.is_absolute():
            defaults_root_project = project_root / defaults_root
        else:
            defaults_root_project = defaults_root

        # Legacy locations (inside backend)
        legacy_admin = self._backend_root / "configfilesadmin" / file_name
        legacy_defaults = self._backend_root / "configfiles" / file_name

        # Build list including both CWD-relative (for backwards compat if running from project root)
        # and project-root-relative variants. Deduplicate while preserving order.
        candidates: List[Path] = [
            overrides_root / file_name,
            defaults_root / file_name,
            overrides_root_project / file_name,
            defaults_root_project / file_name,
            legacy_admin,
            legacy_defaults,
            Path(file_name),                # CWD
            Path(f"../{file_name}"),       # parent of CWD
            project_root / file_name,
            self._backend_root / file_name,
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
            # Standardize on running from backend/ directory (agent_start.sh)
            # Use non-prefixed imports so they resolve when cwd=backend
            from core.compliance import get_compliance_manager
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
        """Get RAG MCP configuration (cached) from mcp-rag.json."""
        if self._rag_mcp_config is None:
            try:
                rag_filename = self.app_settings.rag_mcp_config_file
                file_paths = self._search_paths(rag_filename)
                data = self._load_file_with_error_handling(file_paths, "JSON")

                if data:
                    servers_data = {"servers": data}
                    self._rag_mcp_config = MCPConfig(**servers_data)
                    # Validate compliance levels
                    self._validate_mcp_compliance_levels(self._rag_mcp_config, "RAG MCP")
                    logger.info(f"Loaded RAG MCP config with {len(self._rag_mcp_config.servers)} servers: {list(self._rag_mcp_config.servers.keys())}")
                else:
                    self._rag_mcp_config = MCPConfig()
                    logger.info("Created empty RAG MCP config (no configuration file found)")

            except Exception as e:
                logger.error(f"Failed to parse RAG MCP configuration: {e}", exc_info=True)
                self._rag_mcp_config = MCPConfig()

        return self._rag_mcp_config
    
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
    
    def _validate_mcp_compliance_levels(self, config: MCPConfig, config_type: str):
        """Validate compliance levels for all MCP servers."""
        try:
            # Standardize on running from backend/ directory (agent_start.sh)
            from core.compliance import get_compliance_manager
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
        self._tool_approvals_config = None
        logger.info("Configuration cache cleared, will reload on next access")
    
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