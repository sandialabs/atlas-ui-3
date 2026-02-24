"""Unified RAG Service that aggregates HTTP and MCP RAG sources.

This service provides a single interface for:
- Discovering data sources across all configured RAG backends
- Querying RAG sources with automatic routing based on source type
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from atlas.core.compliance import get_compliance_manager
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.modules.config.config_manager import ConfigManager, RAGSourceConfig, resolve_env_var
from atlas.modules.rag.atlas_rag_client import AtlasRAGClient
from atlas.modules.rag.client import RAGResponse

logger = logging.getLogger(__name__)


class UnifiedRAGService:
    """Aggregates RAG discovery and querying across HTTP and MCP sources."""

    def __init__(
        self,
        config_manager: ConfigManager,
        mcp_manager: Optional[Any] = None,
        auth_check_func: Optional[Callable] = None,
        rag_mcp_service: Optional[Any] = None,
    ) -> None:
        """Initialize the unified RAG service.

        Args:
            config_manager: Configuration manager for loading RAG sources config.
            mcp_manager: MCP tool manager for MCP-based RAG sources.
            auth_check_func: Function to check user authorization for groups.
            rag_mcp_service: Optional RAGMCPService instance for MCP RAG queries.
        """
        self.config_manager = config_manager
        self.mcp_manager = mcp_manager
        self.auth_check_func = auth_check_func
        self.rag_mcp_service = rag_mcp_service

        # Cache of HTTP RAG clients by source name
        self._http_clients: Dict[str, AtlasRAGClient] = {}

    def _get_http_client(self, source_name: str, config: RAGSourceConfig) -> AtlasRAGClient:
        """Get or create an HTTP RAG client for a source."""
        if source_name not in self._http_clients:
            # Resolve environment variables in config
            url = resolve_env_var(config.url, required=True)
            bearer_token = resolve_env_var(config.bearer_token, required=False)

            self._http_clients[source_name] = AtlasRAGClient(
                base_url=url,
                bearer_token=bearer_token,
                default_model=config.default_model or "openai/gpt-oss-120b",
                top_k=config.top_k,
                timeout=config.timeout,
            )
            logger.info("Created HTTP RAG client for source: %s", source_name)

        return self._http_clients[source_name]

    async def _is_user_authorized(self, username: str, groups: List[str]) -> bool:
        """Check if user is authorized for a RAG source based on groups."""
        if not groups:
            return True  # No groups restriction
        if not self.auth_check_func:
            return True  # No auth check function provided

        for group in groups:
            if await self.auth_check_func(username, group):
                return True
        return False

    async def discover_data_sources(
        self,
        username: str,
        user_compliance_level: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Discover data sources across all configured RAG backends.

        Returns a list of RAG servers with their sources in the format expected by the UI:
        [
            {
                "server": "atlas_rag",
                "displayName": "ATLAS RAG",
                "icon": "database",
                "complianceLevel": "Internal",
                "sources": [
                    {"id": "technical-docs", "name": "technical-docs", ...}
                ]
            }
        ]
        """
        rag_servers: List[Dict[str, Any]] = []
        rag_config = self.config_manager.rag_sources_config

        for source_name, source_config in rag_config.sources.items():
            try:
                if not source_config.enabled:
                    continue

                # Check group authorization
                if not await self._is_user_authorized(username, source_config.groups):
                    logger.debug(
                        "User %s not authorized for RAG source %s (groups: %s)",
                        sanitize_for_logging(username),
                        sanitize_for_logging(source_name),
                        source_config.groups,
                    )
                    continue

                # Check compliance level filtering
                if user_compliance_level and source_config.compliance_level:
                    compliance_mgr = get_compliance_manager()
                    if not compliance_mgr.is_accessible(
                        user_level=user_compliance_level,
                        resource_level=source_config.compliance_level,
                    ):
                        logger.info(
                            "Skipping RAG source %s due to compliance level mismatch (user: %s, source: %s)",
                            sanitize_for_logging(source_name),
                            sanitize_for_logging(user_compliance_level),
                            sanitize_for_logging(source_config.compliance_level),
                        )
                        continue

                if source_config.type == "http":
                    # Discover from HTTP RAG API
                    server_info = await self._discover_http_source(
                        source_name, source_config, username
                    )
                    if server_info:
                        rag_servers.append(server_info)

                elif source_config.type == "mcp":
                    # MCP sources from rag-sources.json are handled by RAGMCPService
                    # which reads them via config_manager.rag_mcp_config
                    logger.debug("Skipping MCP source %s (handled by RAGMCPService)", source_name)

            except Exception as e:
                logger.error(
                    "Error discovering RAG source %s, continuing with remaining sources: %s",
                    sanitize_for_logging(source_name),
                    e,
                )

        return rag_servers

    async def _discover_http_source(
        self,
        source_name: str,
        config: RAGSourceConfig,
        username: str,
    ) -> Optional[Dict[str, Any]]:
        """Discover data sources from an HTTP RAG API."""
        try:
            client = self._get_http_client(source_name, config)
            data_sources = await client.discover_data_sources(username)

            if not data_sources:
                logger.debug("No data sources found for HTTP source %s", source_name)
                return None

            # Build UI sources array
            ui_sources = [
                {
                    "id": ds.id,
                    "name": ds.label,
                    "label": ds.label,
                    "description": ds.description,
                    "authRequired": True,
                    "selected": False,
                    "complianceLevel": ds.compliance_level,
                }
                for ds in data_sources
            ]

            return {
                "server": source_name,
                "displayName": config.display_name or source_name,
                "icon": config.icon or "database",
                "complianceLevel": config.compliance_level,
                "sources": ui_sources,
            }

        except Exception as e:
            logger.error("Failed to discover HTTP source %s: %s", source_name, e)
            return None

    async def query_rag(
        self,
        username: str,
        qualified_data_source: str,
        messages: List[Dict],
    ) -> RAGResponse:
        """Query a RAG source.

        Args:
            username: The user making the query.
            qualified_data_source: Data source in format "server:source_id" (e.g., "atlas_rag:technical-docs").
            messages: List of message dictionaries.

        Returns:
            RAGResponse with content and metadata.
        """
        logger.debug(
            "[RAG] query_rag called: qualified_source=%s, user=%s, message_count=%d",
            sanitize_for_logging(qualified_data_source),
            sanitize_for_logging(username),
            len(messages),
        )

        # Parse the qualified data source
        if ":" in qualified_data_source:
            server_name, source_id = qualified_data_source.split(":", 1)
        else:
            # No prefix - assume it's the source ID and try to find the server
            source_id = qualified_data_source
            server_name = self._find_server_for_source(source_id)
            if not server_name:
                logger.error("[RAG] Could not find server for source: %s", source_id)
                raise ValueError(f"Could not find server for source: {source_id}")

        logger.info(
            "[RAG] Routing query: server=%s, source=%s, user=%s",
            server_name, source_id, sanitize_for_logging(username)
        )

        rag_config = self.config_manager.rag_sources_config
        source_config = rag_config.sources.get(server_name)

        if not source_config:
            logger.error("[RAG] Source not found in config: %s", server_name)
            raise ValueError(f"RAG source not found: {server_name}")

        logger.debug(
            "[RAG] Source config: type=%s, enabled=%s, compliance_level=%s",
            source_config.type,
            source_config.enabled,
            source_config.compliance_level,
        )

        if source_config.type == "http":
            logger.debug("[RAG] Routing to HTTP RAG client for server: %s", server_name)
            client = self._get_http_client(server_name, source_config)
            # Pass the unqualified source_id to the HTTP API
            response = await client.query_rag(username, source_id, messages)
            logger.debug(
                "[RAG] HTTP RAG response received: content_length=%d, has_metadata=%s",
                len(response.content) if response.content else 0,
                response.metadata is not None,
            )
            return response

        elif source_config.type == "mcp":
            logger.debug("[RAG] Routing to MCP RAG service for server: %s", server_name)
            # Route MCP queries to RAGMCPService
            if not self.rag_mcp_service:
                logger.error("[RAG] RAGMCPService not configured for MCP RAG queries")
                raise ValueError("RAGMCPService not configured for MCP RAG queries")

            # Extract the query from messages (last user message)
            query = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    query = msg.get("content", "")
                    break

            logger.debug(
                "[RAG] MCP RAG query: server=%s, source=%s, query_preview=%s...",
                server_name,
                source_id,
                sanitize_for_logging(query[:100]) if query else "(empty)",
            )

            # Call RAGMCPService.synthesize() for MCP sources
            qualified_sources = [qualified_data_source]  # Format: "server:source_id"
            mcp_response = await self.rag_mcp_service.synthesize(
                username=username,
                query=query,
                sources=qualified_sources,
            )

            logger.debug(
                "[RAG] MCP RAG response received: has_results=%s, meta_data_keys=%s",
                "results" in mcp_response,
                list(mcp_response.get("meta_data", {}).keys()),
            )

            # Convert MCP response to RAGResponse format
            results = mcp_response.get("results", {})
            answer = results.get("answer", "No response from MCP RAG.")
            meta_data = mcp_response.get("meta_data", {})

            logger.debug(
                "[RAG] MCP RAG answer: length=%d, preview=%s...",
                len(answer) if answer else 0,
                sanitize_for_logging(answer[:200]) if answer else "(empty)",
            )

            # Build metadata if available
            metadata = None
            if meta_data.get("providers"):
                # Create basic metadata from MCP response
                from atlas.modules.rag.client import DocumentMetadata, RAGMetadata
                providers_info = meta_data.get("providers", {})
                docs_found = []
                for provider_name, provider_info in providers_info.items():
                    if provider_info.get("used_synth"):
                        docs_found.append(DocumentMetadata(
                            source=provider_name,
                            content_type="mcp_synthesis",
                            confidence_score=1.0,
                        ))
                metadata = RAGMetadata(
                    query_processing_time_ms=0,
                    total_documents_searched=len(providers_info),
                    documents_found=docs_found,
                    data_source_name=server_name,
                    retrieval_method="mcp_synthesis",
                )

            return RAGResponse(content=answer, metadata=metadata)

        else:
            raise ValueError(f"Unknown RAG source type: {source_config.type}")

    def _find_server_for_source(self, source_id: str) -> Optional[str]:
        """Try to find which server a source belongs to (best effort)."""
        # For now, just return None - caller should provide qualified source
        return None

    def get_http_sources(self) -> Dict[str, RAGSourceConfig]:
        """Get all HTTP-type RAG sources from config."""
        rag_config = self.config_manager.rag_sources_config
        return {
            name: config
            for name, config in rag_config.sources.items()
            if config.type == "http" and config.enabled
        }

    def get_mcp_sources(self) -> Dict[str, RAGSourceConfig]:
        """Get all MCP-type RAG sources from config."""
        rag_config = self.config_manager.rag_sources_config
        return {
            name: config
            for name, config in rag_config.sources.items()
            if config.type == "mcp" and config.enabled
        }

    def invalidate_cache(self, source_name: Optional[str] = None) -> None:
        """Invalidate cached HTTP clients.

        Call this when configuration changes to ensure clients are recreated
        with updated settings (URLs, tokens, etc.).

        Args:
            source_name: Specific source to invalidate, or None to invalidate all.
        """
        if source_name:
            if source_name in self._http_clients:
                del self._http_clients[source_name]
                logger.info("Invalidated HTTP client cache for source: %s", source_name)
        else:
            self._http_clients.clear()
            logger.info("Invalidated all HTTP client caches")


__all__ = ["UnifiedRAGService"]
