"""
Pydantic configuration models.

These models describe the structure of Atlas' file-based configuration
(LLM models, MCP servers, RAG sources, tool approvals, file extractors).
They are intentionally free of any I/O or loading logic so they can be
imported without pulling in the file-loading machinery.

It also hosts ``resolve_env_var``, the helper that expands ``${ENV_VAR}``
patterns found in file-based config values, since it is a config-value
primitive consumed by the loader.

Dependency direction: ``config_loader`` -> ``settings`` -> ``models``.
"""

import os
import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


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
    # Whether this model supports vision (multimodal image input).
    # When true, attached image files are sent as inline image content blocks
    # instead of being listed in the files manifest.
    supports_vision: bool = False
    # Whether this model supports native PDF document input.
    # When true, attached PDF files are sent as inline document content blocks
    # (base64) instead of being text-extracted into the files manifest.
    supports_pdf: bool = False
    # Whether this model supports tool/function calling.
    # When false, tools are stripped from requests and the user is warned.
    supports_tools: bool = True
    # Rich model card text shown in the UI info panel (markdown allowed).
    # Provides details like context window, training info, strengths, etc.
    model_card: Optional[str] = None
    # When true, system messages that appear after tool messages are converted
    # to user role.  Required for models (e.g. Mistral/Devstral via vLLM)
    # that reject system messages mid-conversation after tool results.
    strict_role_ordering: bool = False


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
    wormhole: bool = False  # Forward the per-session Wormhole subtoken (via WORMHOLE_FORWARD_HEADER) when connecting
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
    strip_domain: bool = False  # Strip @domain from username (e.g. user@corp.com -> user)

    # API endpoint customization (HTTP type)
    discovery_endpoint: str = "/api/v1/discover/datasources"
    query_endpoint: str = "/api/v1/rag/completions"

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
    # Extensions whose files are plain text and can be read directly (no extractor service needed).
    # All values are normalised to lowercase on load.
    plain_text_types: List[str] = Field(default_factory=list)
    # Max file size (MB) for plain-text direct reads.  Mirrors the per-extractor
    # max_file_size_mb used by HTTP-backed extractors so the fast path stays bounded.
    max_plain_text_size_mb: int = 50
    # Preview truncation length (chars) for plain-text reads.
    plain_text_preview_chars: int = 2000

    @field_validator('plain_text_types', mode='before')
    @classmethod
    def normalize_plain_text_types(cls, v):
        """Normalise all extensions to lowercase."""
        if isinstance(v, list):
            return [ext.lower() for ext in v]
        return v

    @model_validator(mode='after')
    def reject_plain_text_extractor_overlap(self):
        """Reject extensions that appear in both plain_text_types and extension_mapping."""
        overlap = set(self.plain_text_types) & set(self.extension_mapping)
        if overlap:
            raise ValueError(
                f"Extensions must not appear in both plain_text_types and "
                f"extension_mapping: {sorted(overlap)}"
            )
        return self

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
