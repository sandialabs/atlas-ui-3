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

import logging
import os
import re
from typing import Any, ClassVar, Dict, List, Literal, Optional, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

from atlas.modules.config.litellm_gateway_models import (
    GATEWAY_KEY_SEPARATOR,
    is_key_safe_part,
    resolve_gateway_ref,
)

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


# Reasoning-effort levels LiteLLM will carry on a chat/completions request.
# Mirrors ``litellm.types.llms.openai.REASONING_EFFORT`` (litellm 1.97.0, the
# version pinned in uv.lock). Written out here rather than imported so that
# loading configuration does not depend on a LiteLLM internal type, and so an
# unusable value is rejected when the config file is read instead of arriving as
# a provider HTTP 400 in the middle of a user's chat.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
REASONING_EFFORT_VALUES: tuple = get_args(ReasoningEffort)


class ModelConfig(BaseModel):
    """Configuration for a single LLM model."""
    model_name: str
    model_url: str
    api_key: str = ""
    description: Optional[str] = None
    max_tokens: Optional[int] = 10000
    temperature: Optional[float] = 0.7
    request_timeout_seconds: Optional[float] = Field(
        default=None, gt=0, allow_inf_nan=False,
        description="Override LLM_REQUEST_TIMEOUT_SECONDS for this model",
    )
    # Optional extra HTTP headers (e.g. for providers like OpenRouter)
    extra_headers: Optional[Dict[str, str]] = None
    # Compliance/security level (e.g., "External", "Internal", "Public")
    compliance_level: Optional[str] = None
    # Access groups. Empty (the default) means every user may access this model,
    # preserving the historical behavior. When non-empty, only users who are a
    # member of at least one listed group may see or use this model. Matches the
    # ``groups`` access-control convention used by MCPServerConfig / RAGSourceConfig.
    groups: List[str] = Field(default_factory=list)
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
    # Reasoning effort to send with every request to this model. One of
    # REASONING_EFFORT_VALUES ("none", "minimal", "low", "medium", "high",
    # "xhigh"); anything else is rejected when the config file is loaded.
    # Left unset for models that have no reasoning control, which is every model
    # shipped before the GPT-5.6 family: the key is then omitted entirely and the
    # payload is byte-identical to today's. Required for OpenAI's GPT-5.6 models,
    # which reject function tools on /v1/chat/completions unless reasoning effort
    # is explicitly "none".
    reasoning_effort: Optional[ReasoningEffort] = None
    # Rich model card text shown in the UI info panel (markdown allowed).
    # Provides details like context window, training info, strengths, etc.
    model_card: Optional[str] = None
    # When true, system messages that appear after tool messages are converted
    # to user role.  Required for models (e.g. Mistral/Devstral via vLLM)
    # that reject system messages mid-conversation after tool results.
    strict_role_ordering: bool = False
    # When true, the logged-in user's identifier is sent as the
    # "x-litellm-customer-id" HTTP header on each request.  A LiteLLM proxy
    # uses this header to attribute spend/usage to the end user (customer).
    # Only enable for models served through a LiteLLM instance that tracks
    # per-customer usage.
    pass_user_as_customer_id: bool = False
    # Optional email-domain suffix to strip from the reverse-proxy-provided
    # username before it is sent as the "x-litellm-customer-id" header.
    # Example: "@mydomain.com" turns "user@mydomain.com" into "user".  Only
    # applies when pass_user_as_customer_id is true and the value actually ends
    # with the suffix; otherwise the value is sent unchanged.
    customer_id_strip_suffix: Optional[str] = None

    @field_validator('reasoning_effort', mode='before')
    @classmethod
    def validate_reasoning_effort(cls, v):
        """Reject an unusable reasoning effort while the config file is loading.

        Without this the provider is the first thing to notice: "meduim",
        "None" and " none" all load without complaint and then come back as an
        HTTP 400 on a user's first message. Surrounding whitespace is trimmed
        and a blank value is treated as unset, which is what an operator who
        clears the field in YAML means.
        """
        if v is None:
            return None
        if not isinstance(v, str):
            raise ValueError(
                f"reasoning_effort must be a string, got {type(v).__name__}. "
                f"Valid values: {', '.join(REASONING_EFFORT_VALUES)}; omit the key to send none."
            )
        normalized = v.strip()
        if not normalized:
            return None
        if normalized not in REASONING_EFFORT_VALUES:
            hint = ""
            if normalized.lower() == "none":
                hint = (
                    ' Note that "none" must be lowercase and quoted: it means "send '
                    'reasoning_effort: none", which turns reasoning off. To send no '
                    "reasoning_effort at all, omit the key."
                )
            raise ValueError(
                f"reasoning_effort must be one of {', '.join(REASONING_EFFORT_VALUES)}, "
                f"or omitted; got {v!r}.{hint}"
            )
        return normalized


class LiteLLMGatewayDelegation(BaseModel):
    """Downstream token parameters for a gateway using ``auth_type: "delegated"``.

    For Microsoft Entra On-Behalf-Of, ``scope`` is the LiteLLM app's delegated
    scope (e.g. ``https://litellm.example.gov/user_impersonation``).
    """
    audience: Optional[str] = None
    resource: Optional[str] = None
    scope: Optional[str] = None


class LiteLLMGatewayConfig(BaseModel):
    """An enterprise LiteLLM proxy whose models are scoped to LiteLLM teams.

    Instead of listing models statically, the user picks one of their LiteLLM
    teams, then one of the models that team may use. Every LLM call for such a
    model carries the chosen team in ``team_header`` so LiteLLM routes and
    charges the request to that team. See docs/admin/litellm-team-gateways.md.
    """
    base_url: str
    display_name: Optional[str] = None
    description: Optional[str] = None
    # "system": a single service key (api_key) calls LiteLLM; the user's teams
    #   are looked up by their Atlas identity.
    # "delegated": the logged-in user's OIDC token is exchanged (RFC 8693 or
    #   Entra OBO, per OIDC_DELEGATION_PROVIDER) for a LiteLLM-scoped token.
    auth_type: Literal["system", "delegated"] = "system"
    api_key: str = ""
    delegation: Optional[LiteLLMGatewayDelegation] = None
    # How the LiteLLM user_id for /team/list is derived. "email" uses the Atlas
    # user (optionally with user_id_strip_suffix removed); "token_claim" reads
    # user_id_claim from the delegated token (Entra puts the object id in "oid").
    user_id_source: Optional[Literal["email", "token_claim"]] = None
    user_id_claim: str = "oid"
    user_id_strip_suffix: Optional[str] = None
    team_header: str = "x-litellm-team-id"
    team_list_path: str = "team/list"
    models_path: str = "models"
    discovery_timeout_seconds: float = Field(default=30.0, gt=0)
    discovery_cache_seconds: float = Field(default=300.0, ge=0)
    # Access control and compliance apply to every model reached through the
    # gateway, exactly as they do for a statically configured model.
    groups: List[str] = Field(default_factory=list)
    compliance_level: Optional[str] = None
    # Static extra headers sent on every request (values may be ${ENV_VAR}).
    extra_headers: Optional[Dict[str, str]] = None
    # ModelConfig fields applied to every discovered model (max_tokens,
    # temperature, supports_tools, supports_vision, pass_user_as_customer_id...).
    model_defaults: Dict[str, Any] = Field(default_factory=dict)

    # Fields that identify or authorize the model; they come from the gateway
    # itself, so a model_defaults entry for them would be silently misleading.
    RESERVED_MODEL_DEFAULT_KEYS: ClassVar[frozenset] = frozenset({
        "model_name", "model_url", "api_key", "api_key_source", "globus_scope",
        "groups", "compliance_level", "extra_headers",
    })

    @field_validator("model_defaults")
    @classmethod
    def validate_model_defaults(cls, v):
        reserved = sorted(set(v) & cls.RESERVED_MODEL_DEFAULT_KEYS)
        if reserved:
            raise ValueError(
                f"model_defaults may not set {', '.join(reserved)}; "
                "configure these on the gateway itself"
            )
        # Surface a bad key or value at load time rather than on first use.
        ModelConfig(model_name="probe", model_url="http://probe", **v)
        return v

    @model_validator(mode="after")
    def validate_auth(self):
        if self.auth_type == "system" and not self.api_key.strip():
            # Without a key, LiteLLM's SDK would fall back to the server's own
            # OPENAI_API_KEY and send it to this gateway.
            raise ValueError("auth_type 'system' requires api_key")
        if self.auth_type == "delegated":
            delegation = self.delegation
            if not delegation or not (delegation.scope or delegation.audience or delegation.resource):
                raise ValueError(
                    "auth_type 'delegated' requires delegation.scope (or audience/resource)"
                )
        return self

    def resolved_base_url(self) -> str:
        """``base_url`` with a ``${ENV_VAR}`` value expanded."""
        return resolve_env_var(self.base_url) or ""

    def effective_user_id_source(self) -> str:
        if self.user_id_source:
            return self.user_id_source
        return "token_claim" if self.auth_type == "delegated" else "email"


class LLMConfig(BaseModel):
    """Configuration for all LLM models."""
    models: Dict[str, ModelConfig]
    # Enterprise LiteLLM proxies with team-scoped model selection, keyed by a
    # short gateway name that prefixes the gateway's model keys.
    litellm_gateways: Dict[str, LiteLLMGatewayConfig] = Field(default_factory=dict)

    @field_validator('models', mode='before')
    @classmethod
    def validate_models(cls, v):
        """Convert dict values to ModelConfig objects."""
        if isinstance(v, dict):
            return {name: ModelConfig(**config) if isinstance(config, dict) else config
                   for name, config in v.items()}
        return v

    @model_validator(mode="after")
    def validate_gateway_names(self):
        for gateway_name in self.litellm_gateways:
            if not is_key_safe_part(gateway_name):
                raise ValueError(
                    f"LiteLLM gateway name {gateway_name!r} must be non-empty, must not "
                    f"contain {GATEWAY_KEY_SEPARATOR!r}, and must not start or end with ':'"
                )
            prefix = f"{gateway_name}{GATEWAY_KEY_SEPARATOR}"
            clashing = [name for name in self.models if name.startswith(prefix)]
            if clashing:
                raise ValueError(
                    f"Model name(s) {', '.join(clashing)} collide with LiteLLM gateway "
                    f"'{gateway_name}' model keys; rename the model or the gateway"
                )
        return self

    def get_model(self, model_name: str) -> Optional[ModelConfig]:
        """Look up a model by name, including team-scoped gateway models."""
        model_config = self.models.get(model_name)
        if model_config is not None:
            return model_config
        return self._build_gateway_model(model_name)

    def _build_gateway_model(self, model_name: str) -> Optional[ModelConfig]:
        """Synthesize the ModelConfig for a gateway model key.

        Derived from the gateway configuration alone -- no network call -- so a
        saved conversation or a restarted server resolves the same model
        without rediscovery. Whether the user may use the team is checked
        separately, per call, by the gateway client.
        """
        ref = resolve_gateway_ref(self, model_name)
        if ref is None:
            return None
        gateway = self.litellm_gateways[ref.gateway]
        gateway_label = gateway.display_name or ref.gateway
        try:
            base_url = gateway.resolved_base_url()
        except ValueError:
            # base_url names an unset ${ENV_VAR}: the gateway is unusable, so
            # its models are unknown rather than an error on every lookup.
            logger.error("LiteLLM gateway base_url is not configured (environment variable unset)")
            return None
        fields = {"description": f"{ref.model_id} via {gateway_label}", **gateway.model_defaults}
        fields.update(
            model_name=ref.model_id,
            model_url=base_url,
            # The gateway client supplies the bearer token per call (a service
            # key or a delegated token), so no static key is resolved here.
            api_key="",
            groups=list(gateway.groups),
            compliance_level=gateway.compliance_level,
            extra_headers=dict(gateway.extra_headers) if gateway.extra_headers else None,
        )
        return ModelConfig(**fields)


def lookup_model_config(llm_config: Any, model_name: str) -> Optional[ModelConfig]:
    """Resolve a model name against an LLMConfig or a bare ``models`` holder.

    Call sites receive whatever the config manager exposes; test doubles often
    carry only a ``models`` dict, which cannot hold gateway models anyway.
    """
    if isinstance(llm_config, LLMConfig):
        return llm_config.get_model(model_name)
    # Deliberately not defaulted: a missing or broken registry must raise here
    # so callers that fail open on a lookup error can still log it.
    return llm_config.models.get(model_name)


class OAuthConfig(BaseModel):
    """OAuth 2.1 configuration for an ``auth_type: "oauth"`` MCP server.

    Every field is optional: Atlas discovers the authorization server from the
    MCP endpoint (RFC 9728 -> RFC 8414) and registers itself dynamically
    (RFC 7591), so a server usually needs nothing here at all. The fields
    exist for providers that do not offer dynamic registration, or where an
    operator wants to pin the scopes Atlas asks for.
    """
    scopes: Optional[List[str]] = None  # Scopes to request; default: whatever the server advertises
    client_name: str = "Atlas UI"  # Client name presented at dynamic registration
    client_id: Optional[str] = None  # Pre-registered client_id; skips dynamic registration
    client_secret: Optional[str] = None  # Only for a provider that requires a confidential client
    callback_port: Optional[int] = None  # Deprecated and ignored: the callback is an Atlas route


class DelegationConfig(BaseModel):
    """Downstream authorization parameters for an ``auth_type: "delegated"`` server.

    Without this model the keys were silently dropped: ``MCPServerConfig`` is a
    plain ``BaseModel``, so Pydantic discards unknown fields, and the manager
    rebuilds each server from ``model_dump()``. A documented ``delegation``
    block therefore never reached the exchange, which then fell back to the
    server URL as the audience and sent no scope.
    """

    audience: Optional[str] = None  # Defaults to the server URL when unset
    resource: Optional[str] = None  # RFC 8693 `resource` parameter
    scope: Optional[str] = None     # Space-separated scopes for the downstream token


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
    auth_type: str = "none"  # Authentication type: "none", "api_key", "bearer", "jwt", "oauth", "delegated"
    auth_token: Optional[str] = None     # Bearer token for MCP server authentication (supports ${ENV_VAR})
    oauth_config: Optional[OAuthConfig] = None  # OAuth 2.1 configuration (when auth_type="oauth")
    delegation: Optional[DelegationConfig] = None  # Downstream token parameters (when auth_type="delegated")
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

    # Which ATLAS RAG contract this backend speaks (HTTP type).
    # "v1" posts the conversation to /rag/completions and gets a completion
    # back; "v2" posts an explicit query to /rag/query and picks raw evidence
    # or a synthesized answer. See docs/admin/external-rag-api.md.
    api_version: Literal["v1", "v2"] = "v1"

    # API endpoint customization (HTTP type). Left unset, each resolves to the
    # default path for ``api_version``.
    discovery_endpoint: Optional[str] = None
    query_endpoint: Optional[str] = None

    # Default response shape for v2 queries. "synthesized" preserves the v1
    # user-visible behaviour (the backend answers); "raw" returns evidence for
    # Atlas UI's own LLM to reason over. Callers can override per query.
    default_mode: Literal["raw", "synthesized"] = "synthesized"

    DEFAULT_ENDPOINTS: ClassVar[Dict[str, Dict[str, str]]] = {
        "v1": {
            "discovery": "/api/v1/discover/datasources",
            "query": "/api/v1/rag/completions",
        },
        "v2": {
            "discovery": "/api/v2/discover/datasources",
            "query": "/api/v2/rag/query",
        },
    }

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

        defaults = self.DEFAULT_ENDPOINTS[self.api_version]
        if not self.discovery_endpoint:
            self.discovery_endpoint = defaults["discovery"]
        if not self.query_endpoint:
            self.query_endpoint = defaults["query"]
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


def qualified_tool_name(server_name: str, tool_name: str) -> str:
    """The fully-qualified name every server/tool pair maps to.

    Approval config keys, the LLM-facing tool schema, and the tool index all
    use this `<server>_<tool>` spelling, so any place that builds or looks up
    one of those keys must go through here to stay in sync.
    """
    return f"{server_name}_{tool_name}"


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
