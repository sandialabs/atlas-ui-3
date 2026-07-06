"""
Configuration loader.

Holds the ``ConfigManager`` class, which discovers and lazily loads Atlas'
file-based configuration (YAML/JSON) into the pydantic models, with caching,
error handling, and compliance-level validation.

Dependency direction: ``config_loader`` -> ``settings`` -> ``models``.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .models import (
    FileExtractorsConfig,
    LLMConfig,
    MCPConfig,
    MCPServerConfig,
    RAGSourcesConfig,
    ToolApprovalConfig,
    ToolApprovalsConfig,
    resolve_env_var,
)
from .settings import AppSettings

logger = logging.getLogger(__name__)


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
        HTTP RAG can still be enabled independently from atlas_rag pseudo-tools;
        this gate only controls MCP-backed RAG sources/tool exposure.
        """
        if not (
            self.app_settings.feature_rag_enabled
            and self.app_settings.feature_atlas_rag_tools_enabled
        ):
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
