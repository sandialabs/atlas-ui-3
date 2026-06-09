"""
Centralized configuration management using Pydantic models.

This module provides a unified configuration system that:
- Uses Pydantic for type validation and environment variable loading
- Replaces the duplicate config loading logic in config_utils.py
- Provides proper error handling with logging tracebacks
- Supports both .env files and direct environment variables

The implementation is split across three submodules:
- ``models``        - Pydantic config models (LLM, MCP, RAG, etc.) + ``resolve_env_var`` helper
- ``settings``      - ``AppSettings`` + ``build_db_url_from_parts`` helper
- ``config_loader`` - ``ConfigManager`` lazy file loaders

This module re-exports those symbols so the historical import path
``from atlas.modules.config.config_manager import ...`` keeps working, and
exposes the module-level singleton plus convenience getter functions.
"""

from .config_loader import ConfigManager
from .models import (
    FileExtractorConfig,
    FileExtractorsConfig,
    LLMConfig,
    MCPConfig,
    MCPServerConfig,
    ModelConfig,
    OAuthConfig,
    RAGSourceConfig,
    RAGSourcesConfig,
    ToolApprovalConfig,
    ToolApprovalsConfig,
    resolve_env_var,
)
from .settings import AppSettings, build_db_url_from_parts

__all__ = [
    # Helpers
    "resolve_env_var",
    "build_db_url_from_parts",
    # Models
    "ModelConfig",
    "LLMConfig",
    "OAuthConfig",
    "MCPServerConfig",
    "MCPConfig",
    "RAGSourceConfig",
    "RAGSourcesConfig",
    "ToolApprovalConfig",
    "ToolApprovalsConfig",
    "FileExtractorConfig",
    "FileExtractorsConfig",
    # Settings + loader
    "AppSettings",
    "ConfigManager",
    # Singleton + getters
    "config_manager",
    "get_app_settings",
    "get_llm_config",
    "get_mcp_config",
    "get_file_extractors_config",
]


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
