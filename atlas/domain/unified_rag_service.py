"""Unified RAG Service that aggregates HTTP and MCP RAG sources.

This service provides a single interface for:
- Discovering data sources across all configured RAG backends
- Querying RAG sources with automatic routing based on source type
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from atlas.core.compliance import (
    declared_classifications,
    get_active_compliance_context,
    get_compliance_manager,
    get_model_classification_floor,
    reset_active_compliance_context,
    set_active_compliance_context,
)
from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.telemetry import (
    LABEL_MAX_CHARS,
    hash_short,
    safe_label,
    set_attrs,
    size_bytes,
    start_span,
)
from atlas.domain.errors import DataSourcePermissionError
from atlas.domain.rag_corpus_classifications import (
    CORPUS_DISCOVERY_TIMEOUT_SECONDS,
    CORPUS_METADATA_MIN_REFRESH_SECONDS,
    CorpusMetadataCache,
    corpus_classifications,
    corpus_declares_own,
)
from atlas.hooks import HookEvent, get_hook_manager
from atlas.modules.config.config_manager import ConfigManager, RAGSourceConfig, resolve_env_var
from atlas.modules.rag.atlas_rag_client import AtlasRAGClient
from atlas.modules.rag.client import RAGResponse

logger = logging.getLogger(__name__)


def _extract_query_text(messages: List[Dict]) -> str:
    """Return the last user message content used as the RAG query."""
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            return content if isinstance(content, str) else str(content)
    return ""


def _describe_sources(
    server_name: str, source_ids: Optional[List[str]]
) -> tuple[str, str]:
    """Build the subject of a denial message, naming what the user selected.

    Authorization is decided per *server*, but the user picked *corpora*, and
    the server key from ``rag-sources.json`` is never displayed in the UI. So
    every corpus selected from the rejected server is named -- all of them are
    rejected together, and in the batch path naming only one would leave the
    rest unexplained.

    Returns ``(subject, pronoun)``: a phrase ending in "is"/"are" so callers can
    append the reason (e.g. ``"The data source 'internal-docs' is"``), and the
    matching "it"/"them" for the remedy clause. Falls back to the server key
    when no corpus is known, which is better than naming nothing at all.
    """
    names = [str(s) for s in (source_ids or []) if s]
    if not names:
        names = [server_name]

    quoted = [f"'{name}'" for name in names]
    if len(quoted) == 1:
        return f"The data source {quoted[0]} is", "it"
    listed = ", ".join(quoted[:-1]) + f" and {quoted[-1]}"
    return f"The data sources {listed} are", "them"


def _rag_response_attrs(response: RAGResponse) -> Dict[str, Any]:
    """Extract per-response RAG attributes for a span.

    ``docs_used_in_context`` equals ``doc_ids`` in the current implementation
    because every retrieved doc is injected into the LLM prompt; the separate
    field is preserved so that future filtering/reranking can distinguish
    retrieved-but-unused docs without a schema change.
    """
    attrs: Dict[str, Any] = {
        "is_completion": bool(response.is_completion),
        "content_size": size_bytes(response.content or ""),
    }
    metadata = response.metadata
    if metadata is None:
        attrs["num_results"] = 0
        attrs["doc_ids"] = []
        attrs["doc_scores"] = []
        attrs["docs_used_in_context"] = []
        return attrs

    docs = metadata.documents_found or []
    doc_ids: List[str] = []
    doc_scores: List[float] = []
    for doc in docs:
        # Prefer chunk_id (opaque, backend-generated, low leak risk). Fall
        # back to title/source only when no chunk_id is available and
        # sanitize + cap aggressively — titles and sources are untrusted
        # text from the RAG backend that can contain control characters,
        # prompt-injection payloads, or user PII.
        if doc.chunk_id:
            identifier = safe_label(doc.chunk_id, max_chars=LABEL_MAX_CHARS)
        else:
            raw_fallback = doc.title or doc.source or ""
            identifier = safe_label(raw_fallback, max_chars=LABEL_MAX_CHARS)
        doc_ids.append(identifier)
        doc_scores.append(float(doc.confidence_score))

    attrs["num_results"] = len(docs)
    attrs["total_documents_searched"] = metadata.total_documents_searched
    attrs["retrieval_method"] = metadata.retrieval_method
    attrs["query_processing_time_ms"] = metadata.query_processing_time_ms
    attrs["doc_ids"] = doc_ids
    attrs["doc_scores"] = doc_scores
    attrs["docs_used_in_context"] = doc_ids
    attrs["top_score"] = max(doc_scores) if doc_scores else None
    return attrs


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
        # Recent HTTP discovery answers, read by query-time per-corpus checks.
        self._corpus_metadata = CorpusMetadataCache()
        # In-flight query-time discoveries, so concurrent misses share one call.
        self._corpus_refreshes: Dict[Tuple[str, str], "asyncio.Future[bool]"] = {}

    # ----------------------------------------------------- RAG hooks (GH #713)

    @staticmethod
    def _rag_session_context(username: str) -> Dict[str, Any]:
        cl, _ = get_active_compliance_context()
        return {"user_email": username, "compliance_level": cl}

    async def _fire_rag_call_hook(
        self,
        query_text: str,
        sources: List[str],
        username: str,
    ) -> Optional[Any]:
        """RagCall hook: rewrite the query, narrow sources (batch), or block.

        Returns the ``HookOutcome`` or ``None`` when no hooks fired. Call sites
        apply: ``deny`` -> empty RAGResponse; ``modify`` with ``query`` rewrites
        the last user message; ``modify`` with ``qualified_data_sources`` narrows
        a batch to a subset (sources outside the original allow-list are dropped;
        a hook can never widen). ``require_approval`` is a no-op at the RAG layer.
        """
        mgr = get_hook_manager()
        if mgr is None or not mgr.has_hooks(HookEvent.RAG_CALL):
            return None
        return await mgr.run_event(
            HookEvent.RAG_CALL,
            {"query": query_text, "qualified_data_sources": list(sources), "username": username},
            session_context=self._rag_session_context(username),
            matcher_value=",".join(sources) if sources else None,
        )

    async def _fire_rag_response_hook(
        self,
        query_text: str,
        sources: List[str],
        username: str,
        response: "RAGResponse",
    ) -> "RAGResponse":
        """RagResponse hook: redact/filter chunks or replace synthesized content.

        Returns the (possibly modified) ``RAGResponse``. ``deny`` returns an empty
        response (retrieval blocked). Observability-default fail-open so a broken
        audit hook does not discard results. Source narrowing is NOT honored
        here (retrieval already happened); use RagCall for that.
        """
        mgr = get_hook_manager()
        if mgr is None or not mgr.has_hooks(HookEvent.RAG_RESPONSE):
            return response
        outcome = await mgr.run_event(
            HookEvent.RAG_RESPONSE,
            {
                "query": query_text,
                "qualified_data_sources": list(sources),
                "username": username,
                "content": response.content,
                "metadata": response.metadata.model_dump() if response.metadata else None,
            },
            session_context=self._rag_session_context(username),
            matcher_value=",".join(sources) if sources else None,
        )
        if outcome.verdict == "deny":
            return RAGResponse(content="", metadata=None)
        if outcome.modified:
            new_content = outcome.payload.get("content")
            if isinstance(new_content, str):
                response.content = new_content
        return response

    @staticmethod
    def _rewrite_query_messages(messages: List[Dict], new_query: str) -> List[Dict]:
        """Return a shallow-copied messages list with the last user message
        content replaced by ``new_query`` (so the backend extracts the rewritten
        query). Non-mutating; if no user message is present, appends one."""
        out = list(messages)
        for i in range(len(out) - 1, -1, -1):
            if out[i].get("role") == "user":
                out[i] = {**out[i], "content": new_query}
                return out
        out.append({"role": "user", "content": new_query})
        return out

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
                strip_domain=config.strip_domain,
                discovery_path=config.discovery_endpoint,
                query_path=config.query_endpoint,
                api_version=config.api_version,
            )
            logger.info(
                "Created HTTP RAG client for source: %s (api_version=%s)",
                source_name,
                config.api_version,
            )

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

    async def _ensure_source_query_allowed(
        self,
        username: str,
        source_name: str,
        source_config: RAGSourceConfig,
        source_ids: Optional[List[str]] = None,
    ) -> None:
        """Enforce server-side access controls before querying a RAG backend.

        Args:
            username: The user making the query.
            source_name: The server key from ``rag-sources.json``. Used for the
                log lines and as a fallback in messages; it is a config key the
                UI never displays, so it is not what the user is told about.
            source_config: The server's configuration. ``enabled``, ``groups``
                and the server's classifications live on this entry, so a denial
                on those grounds covers every corpus selected from that server.
                For HTTP sources each corpus is then checked against its own
                classifications as well (``_ensure_corpora_allowed``, #1035).
            source_ids: The corpora the user actually selected from this server,
                as displayed in the picker. All of them are named in the denial
                message -- the check is per-server, so every one of them is
                rejected together, and in the batch path a message naming only
                one (or naming the server key instead) leaves the user unable to
                tell what to deselect.

        Raises:
            DataSourcePermissionError: The source is disabled, the user is not in
                an authorized group, or the source sits outside the compliance
                boundary that is active for this turn. Every failure mode raises
                the same error type so callers can distinguish an authorization
                denial from a backend failure; the ``code`` attribute
                (``DATA_SOURCE_DISABLED`` / ``DATA_SOURCE_ACCESS_DENIED`` /
                ``DATA_SOURCE_COMPLIANCE_MISMATCH``) says which.

        Scope:
            This gate covers RAG sources routed through ``UnifiedRAGService``,
            i.e. the ``http``-origin sources configured in ``rag-sources.json``.
            It does **not** cover ``mcp``-origin sources: ``mcp_execution``
            splits selected sources by origin and hands MCP ones directly to
            ``RAGMCPService.synthesize``, which performs compliance filtering
            only at discovery time. Closing that requires lifting this check
            into a shared policy called by both services (tracked in #752);
            until then the query-time boundary is enforced for HTTP RAG
            sources only.
        """
        subject, pronoun = _describe_sources(source_name, source_ids)

        if not source_config.enabled:
            logger.warning(
                "Rejected RAG query for source %s: source is disabled",
                sanitize_for_logging(source_name),
            )
            raise DataSourcePermissionError(
                f"{subject} currently disabled. Deselect {pronoun} and try again.",
                code="DATA_SOURCE_DISABLED",
            )

        if not await self._is_user_authorized(username, source_config.groups):
            logger.warning(
                "Rejected RAG query for source %s: user %s is not in an authorized group",
                sanitize_for_logging(source_name),
                hash_short(username),
            )
            raise DataSourcePermissionError(
                f"{subject} not accessible to you. Deselect {pronoun}, or ask "
                "an administrator for access to its group.",
                code="DATA_SOURCE_ACCESS_DENIED",
            )

        active_compliance_level, enforce_compliance = get_active_compliance_context()
        if not enforce_compliance:
            # No classification is active. Keep the model floor: a source that
            # declares classifications must share one with the selected model,
            # so an unclassified turn cannot pull a source into a model never
            # approved for any of its classifications.
            floor = get_model_classification_floor()
            source_classifications = declared_classifications(source_config)
            if floor is None or not source_classifications:
                return
            compliance_mgr = get_compliance_manager()
            if any(
                compliance_mgr.classification_permits(level, floor)
                for level in source_classifications
            ):
                await self._ensure_corpora_allowed(
                    username, source_name, source_config, source_ids, None, floor
                )
                return
            logger.warning(
                "Rejected RAG query for source %s: no classification shared with the model",
                sanitize_for_logging(source_name),
            )
            raise DataSourcePermissionError(
                f"{subject} not approved for any classification the selected model "
                f"may receive. Deselect {pronoun}, select a compliance level, or "
                "switch to a model approved for that source.",
                code="DATA_SOURCE_COMPLIANCE_MISMATCH",
            )

        if not active_compliance_level:
            logger.warning(
                "Rejected RAG query for source %s: no compliance level is active",
                sanitize_for_logging(source_name),
            )
            raise DataSourcePermissionError(
                f"{subject} not accessible without a compliance level. "
                f"Deselect {pronoun}, or select a compliance level.",
                code="DATA_SOURCE_COMPLIANCE_MISMATCH",
            )

        # The source must explicitly list the active classification; one that
        # declares nothing is approved for no classified session (#1032).
        compliance_mgr = get_compliance_manager()
        if compliance_mgr.classification_permits(
            active_compliance_level,
            declared_classifications(source_config),
        ):
            await self._ensure_corpora_allowed(
                username, source_name, source_config, source_ids,
                active_compliance_level, None,
            )
            return

        # Deliberately logs neither compliance label: the point of query-time
        # enforcement is to keep compliance labels out of the log stream. The
        # source name is enough to diagnose a denial.
        logger.warning(
            "Rejected RAG query for source %s: outside the active compliance boundary",
            sanitize_for_logging(source_name),
        )
        raise DataSourcePermissionError(
            f"{subject} not approved for the selected compliance level. "
            f"Deselect {pronoun}, or select a different compliance level.",
            code="DATA_SOURCE_COMPLIANCE_MISMATCH",
        )

    async def _corpus_metadata_for(
        self,
        username: str,
        source_name: str,
        source_config: RAGSourceConfig,
        source_ids: List[str],
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Server-owned discovery metadata for ``source_ids``, by corpus id.

        A fresh cached answer that lists every requested corpus is used as is;
        otherwise the backend is asked again (at most once every
        ``CORPUS_METADATA_MIN_REFRESH_SECONDS``), so a corpus added since the
        last discovery is found. An answer is trusted for at most
        ``CORPUS_METADATA_TTL_SECONDS``, which bounds how long a reclassified
        corpus keeps its old classifications. Corpus ids come from the request
        and are only ever looked up here -- never taken as evidence of
        anything.

        Returns ``(corpora, refresh_failed)``: the fresh answer (``None`` when
        there is none), and whether the backend failed to answer when it was
        needed -- just now, or recently enough not to be asked again yet.
        """
        cache = self._corpus_metadata
        cached = cache.lookup(source_name, username)
        if cached is not None and (
            all(cid in cached for cid in source_ids)
            or (cache.age(source_name, username) or 0.0) < CORPUS_METADATA_MIN_REFRESH_SECONDS
        ):
            return cached, False
        if cache.recently_failed(source_name, username):
            return cached, True
        key = (source_name, username or "")
        refresh = self._corpus_refreshes.get(key)
        if refresh is None:
            refresh = asyncio.ensure_future(
                self._refresh_corpus_metadata(username, source_name, source_config)
            )
            self._corpus_refreshes[key] = refresh
            refresh.add_done_callback(lambda _done: self._corpus_refreshes.pop(key, None))
        # Shielded: one caller giving up must not cancel the shared refresh.
        answered = await asyncio.shield(refresh)
        return cache.lookup(source_name, username), not answered

    async def _refresh_corpus_metadata(
        self,
        username: str,
        source_name: str,
        source_config: RAGSourceConfig,
    ) -> bool:
        """Ask the backend for its corpora once; True when it answered."""
        cache = self._corpus_metadata
        try:
            client = self._get_http_client(source_name, source_config)
            data_sources = await asyncio.wait_for(
                client.discover_data_sources(username),
                timeout=min(source_config.timeout, CORPUS_DISCOVERY_TIMEOUT_SECONDS),
            )
        except Exception as exc:
            logger.warning(
                "Could not load corpus metadata for RAG source %s (%s)",
                sanitize_for_logging(source_name),
                type(exc).__name__,
            )
            data_sources = None
        if not data_sources:
            cache.mark_failed(source_name, username)
            return False
        cache.store(source_name, username, data_sources)
        return True

    async def _ensure_corpora_allowed(
        self,
        username: str,
        source_name: str,
        source_config: RAGSourceConfig,
        source_ids: Optional[List[str]],
        active_level: Optional[str],
        floor: Optional[Any],
    ) -> None:
        """Check every requested HTTP corpus against its own classifications.

        Runs after the server passed, with the same per-corpus calculation
        discovery uses (``corpus_classifications``), so a corpus the picker
        hides cannot be reached by a stale selection, a hand-built request or
        a model's tool call. Every corpus is checked before any is queried; one
        failure refuses the whole request.

        ``active_level`` is the trusted per-turn classification. Without one,
        ``floor`` (the selected model's classifications) applies instead, and
        only to corpora that declare their own list -- one that inherits its
        server's was covered by the server's floor check.

        In a classified session a corpus the backend's discovery does not list,
        or any corpus when discovery does not answer, cannot be confirmed and
        is refused. Under the floor alone the same holds for a server with
        ``legacy_corpus_classifications``, whose operator has said per-corpus
        levels matter. Otherwise such a corpus is let through under the floor:
        the server already shares a classification with the model, and
        refusing would turn a discovery outage into an outage for unclassified
        chat; each such pass is logged at WARNING.
        """
        if not source_ids or source_config.type != "http":
            return
        corpora, refresh_failed = await self._corpus_metadata_for(
            username, source_name, source_config, source_ids
        )
        unanswered = refresh_failed or corpora is None
        corpora = corpora or {}

        compliance_mgr = get_compliance_manager()
        denied: List[str] = []
        unconfirmed: List[str] = []
        unchecked_under_floor: List[str] = []
        for corpus_id in source_ids:
            ds = corpora.get(corpus_id)
            if ds is None:
                if active_level is not None or source_config.legacy_corpus_classifications:
                    unconfirmed.append(corpus_id)
                else:
                    unchecked_under_floor.append(corpus_id)
                continue
            classifications = corpus_classifications(ds, source_config)
            if active_level is not None:
                allowed = compliance_mgr.classification_permits(active_level, classifications)
            elif not corpus_declares_own(ds, source_config):
                continue
            else:
                allowed = any(
                    compliance_mgr.classification_permits(level, floor)
                    for level in classifications or []
                )
            if not allowed:
                denied.append(corpus_id)

        # Corpus ids are logged, never the labels they were checked against.
        if unchecked_under_floor and not denied:
            logger.warning(
                "Allowed RAG query for source %s under the model floor without "
                "per-corpus metadata for %d corpus(es): %s",
                sanitize_for_logging(source_name),
                len(unchecked_under_floor),
                sanitize_for_logging(",".join(unchecked_under_floor)),
            )
        if denied:
            logger.warning(
                "Rejected RAG query for source %s: %d corpus(es) outside the "
                "active compliance boundary: %s",
                sanitize_for_logging(source_name),
                len(denied),
                sanitize_for_logging(",".join(denied)),
            )
            subject, pronoun = _describe_sources(source_name, denied)
            if active_level is not None:
                reason = "not approved for the selected compliance level"
                remedy = "or select a different compliance level"
            else:
                reason = "not approved for any classification the selected model may receive"
                remedy = "select a compliance level, or switch to a model approved for that source"
            raise DataSourcePermissionError(
                f"{subject} {reason}. Deselect {pronoun}, {remedy}.",
                code="DATA_SOURCE_COMPLIANCE_MISMATCH",
            )
        if unconfirmed:
            logger.warning(
                "Rejected RAG query for source %s: %d corpus(es) not confirmed by discovery: %s",
                sanitize_for_logging(source_name),
                len(unconfirmed),
                sanitize_for_logging(",".join(unconfirmed)),
            )
            subject, pronoun = _describe_sources(source_name, unconfirmed)
            plural = pronoun == "them"
            if unanswered:
                raise DataSourcePermissionError(
                    f"{subject} not verifiable right now: the RAG backend did not "
                    f"answer, so {'their data classifications' if plural else 'its data classification'} "
                    f"cannot be checked. Try again later, or deselect {pronoun}.",
                    code="DATA_SOURCE_UNVERIFIED",
                )
            raise DataSourcePermissionError(
                f"{subject} not offered to you by the RAG backend, so "
                f"{'they' if plural else 'it'} cannot be confirmed as approved for this "
                f"conversation. Deselect {pronoun}, or ask an administrator for access.",
                code="DATA_SOURCE_NOT_LISTED",
            )

    async def discover_data_sources(
        self,
        username: str,
        user_compliance_level: Optional[str] = None,
        only_servers: Optional[List[str]] = None,
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
            if only_servers is not None and source_name not in only_servers:
                continue
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
                if user_compliance_level:
                    compliance_mgr = get_compliance_manager()
                    if not compliance_mgr.classification_permits(
                        user_compliance_level,
                        declared_classifications(source_config),
                    ):
                        logger.info(
                            "Skipping RAG source %s: not approved for the active classification",
                            sanitize_for_logging(source_name),
                        )
                        continue

                if source_config.type == "http":
                    # Discover from HTTP RAG API
                    server_info = await self._discover_http_source(
                        source_name, source_config, username, user_compliance_level
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
        user_compliance_level: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Discover data sources from an HTTP RAG API.

        With ``user_compliance_level`` set, corpora not approved for it are
        left out (issue #1032), so this list is also the server-side gate for
        per-corpus classifications.
        """
        try:
            client = self._get_http_client(source_name, config)
            data_sources = await client.discover_data_sources(username)
            # Unfiltered, so query-time checks at any level can reuse it. A
            # failed answer opens the same short window query-time discovery
            # uses, so the queries that follow do not each wait on the backend.
            if data_sources:
                self._corpus_metadata.store(source_name, username, data_sources)
            else:
                self._corpus_metadata.mark_failed(source_name, username)

            if not data_sources:
                logger.debug("No data sources found for HTTP source %s", source_name)
                return None

            compliance_mgr = get_compliance_manager() if user_compliance_level else None
            ui_sources = []
            for ds in data_sources:
                classifications = corpus_classifications(ds, config)
                if compliance_mgr and not compliance_mgr.classification_permits(
                    user_compliance_level, classifications
                ):
                    logger.info(
                        "Skipping RAG corpus %s:%s: not approved for the active classification",
                        sanitize_for_logging(source_name),
                        sanitize_for_logging(ds.id),
                    )
                    continue
                ui_sources.append({
                    "id": ds.id,
                    "name": ds.label,
                    "label": ds.label,
                    "description": ds.description,
                    "authRequired": True,
                    "selected": False,
                    # Display badge; the boundary is allowedDataClassifications.
                    "complianceLevel": ds.compliance_level or None,
                    "allowedDataClassifications": classifications,
                })

            return {
                "server": source_name,
                "displayName": config.display_name or source_name,
                "icon": config.icon or "database",
                "complianceLevel": config.compliance_level,
                "allowedDataClassifications": declared_classifications(config),
                "sources": ui_sources,
            }

        except Exception as e:
            logger.error("Failed to discover HTTP source %s: %s", source_name, e)
            return None

    def _resolve_query(self, messages: List[Dict], query: Optional[str]) -> str:
        """Return the query text to search with.

        An explicit ``query`` wins; otherwise it is derived from the last user
        message the way v1 always has. v2 backends only ever receive this
        string -- never ``messages``.
        """
        if query is not None:
            return query
        return _extract_query_text(messages)

    async def _query_http_client(
        self,
        client: AtlasRAGClient,
        source_config: RAGSourceConfig,
        username: str,
        source_ids: List[str],
        messages: List[Dict],
        query: Optional[str],
        mode: Optional[str],
        search_kwargs: Optional[Dict[str, Any]] = None,
    ) -> RAGResponse:
        """Send one query to an HTTP RAG backend over its configured contract.

        v1 posts the conversation and gets a completion; v2 posts only the
        resolved query string plus a mode. The corpora, authorization and
        impersonation are identical either way -- the contract decides the
        request shape, not who may ask.

        ``search_kwargs`` is a v2-only retrieval-tuning block (``top_k_final``,
        ``rerank`` and friends). It is dropped on v1, whose request body has no
        equivalent -- so a caller asking for more results from a v1 source gets
        that source's configured behaviour rather than an error.
        """
        if source_config.api_version != "v2":
            return await client.query_rag(
                username,
                source_ids[0],
                messages,
                data_sources=source_ids if len(source_ids) > 1 else None,
            )

        query_text = self._resolve_query(messages, query)
        # An explicit ``search_kwargs`` replaces the whole block in the v2
        # client, so fold the source's configured ``top_k`` in as the default
        # rather than letting a caller-supplied ``depth`` silently drop it.
        effective_search_kwargs = search_kwargs
        if effective_search_kwargs is not None:
            effective_search_kwargs = {
                "top_k_final": source_config.top_k,
                **effective_search_kwargs,
            }
        return await client.query_v2(
            user_name=username,
            query=query_text,
            corpora=source_ids if len(source_ids) > 1 else source_ids[0],
            mode=mode or source_config.default_mode,
            top_k=source_config.top_k,
            search_kwargs=effective_search_kwargs,
        )

    async def query_rag(
        self,
        username: str,
        qualified_data_source: str,
        messages: List[Dict],
        enforced_compliance_level: Optional[str] = None,
        query: Optional[str] = None,
        mode: Optional[str] = None,
        search_kwargs: Optional[Dict[str, Any]] = None,
        _skip_hooks: bool = False,
    ) -> RAGResponse:
        """Query a RAG source.

        Args:
            username: The user making the query.
            qualified_data_source: Data source in format "server:source_id" (e.g. "atlas_rag:technical-docs").
            messages: List of message dictionaries.
            query: The explicit question to search for. When omitted it is
                derived from the last user message (v1 behaviour). v2 backends
                are sent this string and never the conversation, so callers
                that know what they are asking -- the ``atlas_rag_query`` tool,
                for one -- should pass it.
            mode: ``"raw"`` (evidence for our own LLM) or ``"synthesized"``
                (an answer from the backend). v2 only; defaults to the
                source's ``default_mode``. Ignored by v1 backends, which
                always synthesize.
            search_kwargs: v2-only retrieval knobs (``top_k_final``, ``rerank``,
                ...). Tunes how much is retrieved, never which sources are
                reachable, so it is safe to derive from tool arguments. Ignored
                by v1 backends.
            enforced_compliance_level: Overrides the ambient compliance context
                for this call. This must be a **trusted, server-derived** level
                (the selected model's configured level) -- never a client-supplied
                filter. Production callers leave this ``None`` and let
                ``ChatService`` establish the context for the whole turn; it
                exists for callers that run outside a chat turn, and for tests.
            _skip_hooks: When True, skip the RagCall/RagResponse hooks. Used by
                the agentic atlas_rag_query path, which fires its own single
                RagCall/RagResponse over all sources to avoid double-firing for
                HTTP sources that route through this method (GH #713).

        Returns:
            RAGResponse with content and metadata.

        Raises:
            DataSourcePermissionError: The source is disabled, out of group, or
                outside the active compliance boundary.
        """
        query_text = self._resolve_query(messages, query)
        span_attrs = {
            "data_source": qualified_data_source,
            "query_hash": hash_short(query_text),
            "query_chars": len(query_text),
            "user_hash": hash_short(username),
            "message_count": len(messages),
            "explicit_query": query is not None,
            # What the caller asked for; empty means "let the source decide".
            # The mode that actually ran is visible on the response attributes
            # as ``retrieval_method`` (``v2_raw`` / ``v2_synthesized``).
            "requested_mode": mode or "",
            "batch": False,
        }
        token = None
        if enforced_compliance_level is not None:
            token = set_active_compliance_context(enforced_compliance_level, enforce=True)
        try:
            with start_span("rag.query", span_attrs) as span:
                # RagCall hook (GH #713): rewrite query / block retrieval.
                call_outcome = None if _skip_hooks else await self._fire_rag_call_hook(
                    query_text, [qualified_data_source], username
                )
                if call_outcome is not None and call_outcome.verdict == "deny":
                    set_attrs(span, {"rag.blocked_by_hook": True})
                    return RAGResponse(content="", metadata=None)
                if call_outcome is not None and call_outcome.modified:
                    new_q = call_outcome.payload.get("query")
                    if isinstance(new_q, str) and new_q:
                        messages = self._rewrite_query_messages(messages, new_q)
                        query_text = new_q

                response = await self._query_rag_impl(
                    username,
                    qualified_data_source,
                    messages,
                    query=query_text,
                    mode=mode,
                    search_kwargs=search_kwargs,
                )

                # RagResponse hook (GH #713): redact/filter before injection.
                if not _skip_hooks:
                    response = await self._fire_rag_response_hook(
                        query_text, [qualified_data_source], username, response
                    )
                set_attrs(span, _rag_response_attrs(response))
                return response
        finally:
            if token is not None:
                reset_active_compliance_context(token)

    async def _query_rag_impl(
        self,
        username: str,
        qualified_data_source: str,
        messages: List[Dict],
        query: Optional[str] = None,
        mode: Optional[str] = None,
        search_kwargs: Optional[Dict[str, Any]] = None,
    ) -> RAGResponse:
        """Internal RAG query implementation (span-free)."""
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

        await self._ensure_source_query_allowed(
            username, server_name, source_config, source_ids=[source_id]
        )

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
            response = await self._query_http_client(
                client,
                source_config,
                username,
                [source_id],
                messages,
                query=query,
                mode=mode,
                search_kwargs=search_kwargs,
            )
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

            # MCP RAG has always taken an explicit query; an explicit ``query``
            # argument simply replaces the last-user-message derivation.
            # ``mode`` is not plumbed here yet -- ``synthesize`` is the only
            # shape ``RAGMCPService`` returns through this path.
            mcp_query = self._resolve_query(messages, query)

            logger.debug(
                "[RAG] MCP RAG query: server=%s, source=%s, query_preview=%s...",
                server_name,
                source_id,
                sanitize_for_logging(mcp_query[:100]) if mcp_query else "(empty)",
            )

            # Call RAGMCPService.synthesize() for MCP sources
            qualified_sources = [qualified_data_source]  # Format: "server:source_id"
            mcp_response = await self.rag_mcp_service.synthesize(
                username=username,
                query=mcp_query,
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

    async def query_rag_batch(
        self,
        username: str,
        qualified_data_sources: List[str],
        messages: List[Dict],
        enforced_compliance_level: Optional[str] = None,
        query: Optional[str] = None,
        mode: Optional[str] = None,
        search_kwargs: Optional[Dict[str, Any]] = None,
        _skip_hooks: bool = False,
    ) -> RAGResponse:
        """Query multiple RAG sources on the same server in a single request.

        Sends a single batched request for multiple sources that share the same
        server, avoiding N separate HTTP calls when multiple corpora are selected.
        The caller (e.g. LiteLLMCaller._query_all_rag_sources) is responsible for
        grouping sources by server before calling this method.

        Args:
            username: The user making the query.
            qualified_data_sources: Data sources in format "server:source_id".
                All sources MUST belong to the same server.
            messages: List of message dictionaries.
            enforced_compliance_level: Overrides the ambient compliance context
                for this call. Must be a **trusted, server-derived** level, never
                a client-supplied filter. See :meth:`query_rag`.
            query: Explicit query text; see :meth:`query_rag`.
            mode: v2 response shape; see :meth:`query_rag`.
            search_kwargs: v2 retrieval knobs; see :meth:`query_rag`.
            _skip_hooks: When True, skip the RagCall/RagResponse hooks (agentic
                path fires its own; see :meth:`query_rag`).

        Returns:
            RAGResponse with content and metadata from the batched query.

        Raises:
            ValueError: If sources list is empty or sources span multiple servers.
            DataSourcePermissionError: The source is disabled, out of group, or
                outside the active compliance boundary.
        """
        query_text = self._resolve_query(messages, query)
        span_attrs = {
            "data_source": ",".join(qualified_data_sources or []),
            "query_hash": hash_short(query_text),
            "query_chars": len(query_text),
            "user_hash": hash_short(username),
            "message_count": len(messages),
            "explicit_query": query is not None,
            "requested_mode": mode or "",
            "batch": True,
            "batch_size": len(qualified_data_sources or []),
        }
        token = None
        if enforced_compliance_level is not None:
            token = set_active_compliance_context(enforced_compliance_level, enforce=True)
        try:
            with start_span("rag.query", span_attrs) as span:
                # RagCall hook (GH #713): rewrite query / narrow sources / block.
                # A hook may only NARROW the source list (drop entries); sources
                # it adds that were not in the original request are discarded so
                # a hook can never widen the retrieval boundary.
                call_outcome = None if _skip_hooks else await self._fire_rag_call_hook(
                    query_text, qualified_data_sources, username
                )
                if call_outcome is not None and call_outcome.verdict == "deny":
                    set_attrs(span, {"rag.blocked_by_hook": True})
                    return RAGResponse(content="", metadata=None)
                if call_outcome is not None and call_outcome.modified:
                    new_q = call_outcome.payload.get("query")
                    if isinstance(new_q, str) and new_q:
                        messages = self._rewrite_query_messages(messages, new_q)
                        query_text = new_q
                    new_sources = call_outcome.payload.get("qualified_data_sources")
                    if isinstance(new_sources, list):
                        allowed = set(qualified_data_sources)
                        narrowed = [s for s in new_sources if s in allowed]
                        if not narrowed:
                            set_attrs(span, {"rag.blocked_by_hook": True})
                            return RAGResponse(content="", metadata=None)
                        qualified_data_sources = narrowed

                response = await self._query_rag_batch_impl(
                    username,
                    qualified_data_sources,
                    messages,
                    query=query_text,
                    mode=mode,
                    search_kwargs=search_kwargs,
                )

                # RagResponse hook (GH #713)
                if not _skip_hooks:
                    response = await self._fire_rag_response_hook(
                        query_text, qualified_data_sources, username, response
                    )
                set_attrs(span, _rag_response_attrs(response))
                return response
        finally:
            if token is not None:
                reset_active_compliance_context(token)

    async def _query_rag_batch_impl(
        self,
        username: str,
        qualified_data_sources: List[str],
        messages: List[Dict],
        query: Optional[str] = None,
        mode: Optional[str] = None,
        search_kwargs: Optional[Dict[str, Any]] = None,
    ) -> RAGResponse:
        """Internal batched RAG query (span-free)."""
        if not qualified_data_sources:
            raise ValueError("No data sources provided for batch query")

        # Parse all qualified sources - they must all be from the same server
        source_ids: List[str] = []
        server_name = None
        for qs in qualified_data_sources:
            if ":" in qs:
                srv, src_id = qs.split(":", 1)
            else:
                raise ValueError(
                    f"Unqualified source '{qs}' passed to query_rag_batch. "
                    f"All sources must be qualified as 'server:source_id'."
                )

            if server_name is None:
                server_name = srv
            elif srv != server_name:
                raise ValueError(
                    f"All sources in a batch must be from the same server. "
                    f"Got {server_name} and {srv}"
                )
            source_ids.append(src_id)

        logger.info(
            "[RAG] Batch query: server=%s, sources=%s, user=%s",
            server_name, source_ids, sanitize_for_logging(username),
        )

        rag_config = self.config_manager.rag_sources_config
        source_config = rag_config.sources.get(server_name)

        if not source_config:
            raise ValueError(f"RAG source not found: {server_name}")

        # Every corpus in the batch comes from this one server and is rejected
        # together, so the denial message names all of them.
        await self._ensure_source_query_allowed(
            username, server_name, source_config, source_ids=source_ids
        )

        if source_config.type == "http":
            client = self._get_http_client(server_name, source_config)
            response = await self._query_http_client(
                client,
                source_config,
                username,
                source_ids,
                messages,
                query=query,
                mode=mode,
                search_kwargs=search_kwargs,
            )
            logger.debug(
                "[RAG] Batch HTTP response: content_length=%d, has_metadata=%s",
                len(response.content) if response.content else 0,
                response.metadata is not None,
            )
            return response

        elif source_config.type == "mcp":
            # MCP sources: delegate to synthesize with all sources
            if not self.rag_mcp_service:
                raise ValueError("RAGMCPService not configured for MCP RAG queries")

            query = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    query = msg.get("content", "")
                    break

            mcp_response = await self.rag_mcp_service.synthesize(
                username=username,
                query=query,
                sources=qualified_data_sources,
            )

            results = mcp_response.get("results", {})
            answer = results.get("answer", "No response from MCP RAG.")
            meta_data = mcp_response.get("meta_data", {})

            metadata = None
            if meta_data.get("providers"):
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
        self._corpus_metadata.invalidate(source_name)
        if source_name:
            if source_name in self._http_clients:
                del self._http_clients[source_name]
                logger.info("Invalidated HTTP client cache for source: %s", source_name)
        else:
            self._http_clients.clear()
            logger.info("Invalidated all HTTP client caches")


__all__ = ["UnifiedRAGService", "corpus_classifications"]
