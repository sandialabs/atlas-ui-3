"""ATLAS RAG Client for integrating with the ATLAS RAG API (OpenAPI v0.3.x).

This client implements the same interface as ``RAGClient`` but speaks the
ATLAS RAG API described at:

  - GET  /api/v1/discover/datasources?role=read|write&as_user={user}
  - POST /api/v1/rag/completions?as_user={user}

Responses follow the OpenAI ``chat.completion`` schema with an extra
``rag_metadata`` block and per-message ``annotations`` (``url_citation``)
that link character ranges in the assistant content to source documents.
"""

import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import HTTPException

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.modules.rag.client import (
    DataSource,
    DocumentMetadata,
    RAGMetadata,
    RAGResponse,
    URLCitation,
)

logger = logging.getLogger(__name__)


class AtlasRAGClient:
    """Client for communicating with external ATLAS RAG API.

    Implements the same interface as ``RAGClient`` for seamless substitution.
    Uses Bearer token authentication with user impersonation via the ``as_user``
    query parameter.
    """

    DEFAULT_DISCOVERY_PATH = "/api/v1/discover/datasources"
    DEFAULT_QUERY_PATH = "/api/v1/rag/completions"

    def __init__(
        self,
        base_url: str,
        bearer_token: Optional[str] = None,
        default_model: str = "openai/gpt-oss-120b",
        top_k: int = 4,
        timeout: float = 60.0,
        strip_domain: bool = False,
        discovery_path: Optional[str] = None,
        query_path: Optional[str] = None,
    ):
        """Initialize the external RAG client.

        Args:
            base_url: Base URL for the external RAG API.
            bearer_token: Bearer token for API authentication.
            default_model: Default model to use for RAG queries.
            top_k: Default number of documents to retrieve.
            timeout: Request timeout in seconds.
            strip_domain: If True, strip ``@domain`` from usernames before
                sending to the RAG API (``user@corp.com`` -> ``user``).
            discovery_path: Override for the discovery endpoint path.
                Defaults to ``/api/v1/discover/datasources``.
            query_path: Override for the completions endpoint path.
                Defaults to ``/api/v1/rag/completions``.
        """
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.default_model = default_model
        self.top_k = top_k
        self.timeout = timeout
        self.strip_domain = strip_domain
        self.discovery_path = discovery_path or self.DEFAULT_DISCOVERY_PATH
        self.query_path = query_path or self.DEFAULT_QUERY_PATH

        logger.info(
            "AtlasRAGClient initialized: url=%s, model=%s, top_k=%d, "
            "strip_domain=%s, discovery=%s, query=%s",
            self.base_url,
            self.default_model,
            self.top_k,
            self.strip_domain,
            self.discovery_path,
            self.query_path,
        )

    def _resolve_username(self, user_name: str) -> str:
        """Resolve the username to send to the RAG API.

        If ``strip_domain`` is enabled, strips the ``@domain`` portion from
        email addresses.
        """
        if self.strip_domain and "@" in user_name:
            stripped = user_name.split("@", 1)[0]
            logger.debug(
                "Stripped domain from username: %s -> %s",
                sanitize_for_logging(user_name),
                sanitize_for_logging(stripped),
            )
            return stripped
        return user_name

    def _get_headers(self) -> Dict[str, str]:
        """Build HTTP headers for API requests."""
        headers = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    async def discover_data_sources(
        self,
        user_name: str,
        role: str = "read",
    ) -> List[DataSource]:
        """Discover data sources accessible by a user.

        Calls ``GET {discovery_path}?role={role}&as_user={user_name}``.

        Args:
            user_name: The username to discover data sources for.
            role: Access role — ``"read"`` or ``"write"``.

        Returns:
            List of DataSource objects the user can access.
        """
        user_name = self._resolve_username(user_name)
        logger.info("Discovering data sources for user: %s (role=%s)", user_name, role)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(
                    f"{self.base_url}{self.discovery_path}",
                    headers=self._get_headers(),
                    params={"role": role, "as_user": user_name},
                )
                response.raise_for_status()
                data = response.json()

                sources_list = data.get("data_sources", [])
                data_sources = [DataSource(**src) for src in sources_list]

                logger.info(
                    "Discovered %d data sources for user %s",
                    len(data_sources),
                    user_name,
                )
                return data_sources

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "HTTP error discovering data sources for %s: %s (status %d)",
                    user_name,
                    exc.response.text,
                    exc.response.status_code,
                )
                return []

            except httpx.RequestError as exc:
                logger.error(
                    "Request error discovering data sources for %s: %s",
                    user_name,
                    str(exc),
                )
                return []

            except Exception as exc:
                logger.error(
                    "Unexpected error discovering data sources for %s: %s",
                    user_name,
                    str(exc),
                    exc_info=True,
                )
                return []

    async def query_rag(
        self,
        user_name: str,
        data_source: str,
        messages: List[Dict],
        data_sources: Optional[List[str]] = None,
        hybrid_search_kwargs: Optional[Dict[str, Any]] = None,
    ) -> RAGResponse:
        """Query RAG endpoint for a response with metadata.

        Calls ``POST {query_path}?as_user={user_name}`` with a ``RagRequest``
        body matching the ATLAS RAG OpenAPI spec (v0.3.x).

        Args:
            user_name: The username making the query.
            data_source: A single data source (corpus) to query. Ignored when
                ``data_sources`` is provided.
            messages: List of message dictionaries with role and content.
            data_sources: Multiple data sources (corpora) to query in a single
                request. When provided, all sources are sent as one batched
                request via ``hybrid_search_kwargs.corpora``.
            hybrid_search_kwargs: Additional search parameters forwarded to the
                HybridSearch backend. ``top_k`` and ``corpora`` are injected
                automatically when not present.

        Returns:
            RAGResponse containing content, metadata, and url_citation
            annotations.

        Raises:
            HTTPException: On API errors (403, 404, 500).
        """
        corpora = data_sources if data_sources else ([data_source] if data_source else None)
        user_name = self._resolve_username(user_name)

        logger.info(
            "[HTTP-RAG] query_rag called: user=%s, corpora=%s, message_count=%d",
            user_name,
            corpora,
            len(messages),
        )

        user_query = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    user_query = content[:100]
                break
        logger.debug("[HTTP-RAG] Query preview: %s...", user_query)

        merged_kwargs: Dict[str, Any] = {}
        if hybrid_search_kwargs:
            merged_kwargs.update(hybrid_search_kwargs)
        merged_kwargs.setdefault("top_k", self.top_k)
        if corpora is not None:
            merged_kwargs["corpora"] = corpora

        payload = {
            "messages": messages,
            "stream": False,
            "model": self.default_model,
            "hybrid_search_kwargs": merged_kwargs,
        }

        logger.debug(
            "[HTTP-RAG] Request payload: model=%s, hybrid_search_kwargs=%s",
            payload["model"],
            merged_kwargs,
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}{self.query_path}",
                    headers=self._get_headers(),
                    params={"as_user": user_name},
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()

                logger.debug(
                    "[HTTP-RAG] Response received: status=%d, keys=%s",
                    response.status_code,
                    list(data.keys()),
                )

                is_completion = data.get("object") == "chat.completion"

                content = "No response from RAG system."
                message: Dict[str, Any] = {}
                if "choices" in data and data["choices"]:
                    choice = data["choices"][0]
                    message = choice.get("message", {}) or {}
                    msg_content = message.get("content")
                    if msg_content:
                        content = msg_content

                annotations = self._parse_annotations(message)
                metadata = self._parse_rag_metadata(data, data_source, annotations)

                logger.info(
                    "[HTTP-RAG] query_rag complete: user=%s, source=%s, "
                    "content_length=%d, has_metadata=%s, annotations=%d, is_completion=%s",
                    user_name,
                    data_source,
                    len(content),
                    metadata is not None,
                    len(annotations),
                    is_completion,
                )
                return RAGResponse(
                    content=content,
                    metadata=metadata,
                    is_completion=is_completion,
                    annotations=annotations,
                )

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                logger.error(
                    "HTTP error querying RAG for %s: %s (status %d)",
                    user_name,
                    exc.response.text,
                    status_code,
                )
                if status_code == 403:
                    raise HTTPException(
                        status_code=403, detail="Access denied to data source"
                    )
                elif status_code == 404:
                    raise HTTPException(
                        status_code=404, detail="Data source not found"
                    )
                else:
                    raise HTTPException(status_code=500, detail="RAG service error")

            except httpx.RequestError as exc:
                logger.error(
                    "Request error querying RAG for %s: %s",
                    user_name,
                    str(exc),
                )
                raise HTTPException(
                    status_code=500, detail="Failed to connect to RAG service"
                )

            except HTTPException:
                raise

            except Exception as exc:
                logger.error(
                    "Unexpected error querying RAG for %s: %s",
                    user_name,
                    str(exc),
                    exc_info=True,
                )
                raise HTTPException(status_code=500, detail="Internal server error")

    @staticmethod
    def _parse_annotations(message: Dict[str, Any]) -> List[URLCitation]:
        """Extract url_citation annotations from a ChatCompletionMessage.

        Spec: ``message.annotations`` is an optional list of ``Annotation``
        objects with ``type == "url_citation"`` and a ``url_citation`` payload.
        Unknown types are ignored.
        """
        raw = message.get("annotations") or []
        citations: List[URLCitation] = []
        for ann in raw:
            if not isinstance(ann, dict):
                continue
            if ann.get("type") != "url_citation":
                continue
            payload = ann.get("url_citation")
            if not isinstance(payload, dict):
                continue
            try:
                citations.append(URLCitation(**payload))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Skipping malformed url_citation annotation: %s",
                    exc,
                )
        return citations

    def _parse_rag_metadata(
        self,
        data: Dict[str, Any],
        data_source: str,
        annotations: Optional[List[URLCitation]] = None,
    ) -> Optional[RAGMetadata]:
        """Parse rag_metadata from API response into RAGMetadata model.

        Spec notes: ``DocumentMetadata.data_source`` is a nested object and
        ``text`` carries the snippet.  Title/URL are not on the document —
        they live on the corresponding ``url_citation`` annotation (matched
        by index order).  Back-compat fields (``corpus_id``, ``title``,
        ``url``) are still read when present.

        Args:
            data: The full API response dictionary.
            data_source: The data source used in the query (fallback label).
            annotations: Parsed url_citation annotations to enrich documents
                with title/url by index pairing.

        Returns:
            RAGMetadata if present in response, None otherwise.
        """
        if "rag_metadata" not in data or not data["rag_metadata"]:
            return None

        try:
            rm = data["rag_metadata"]
            citations = annotations or []

            documents_found: List[DocumentMetadata] = []
            for i, doc in enumerate(rm.get("documents_found", [])):
                ds = doc.get("data_source") if isinstance(doc.get("data_source"), dict) else {}

                source = ds.get("id") or doc.get("corpus_id") or ""

                citation = citations[i] if i < len(citations) else None
                title = (
                    (citation.title if citation else None)
                    or doc.get("title")
                    or ds.get("label")
                )
                url = (
                    (citation.url if citation and citation.url else None)
                    or doc.get("url")
                )

                doc_metadata = DocumentMetadata(
                    source=source,
                    content_type=doc.get("content_type", "atlas-search"),
                    confidence_score=doc.get("confidence_score", 0.0),
                    chunk_id=(str(doc.get("id")) if doc.get("id") is not None else None),
                    last_modified=doc.get("last_modified"),
                    title=title,
                    url=url,
                )
                documents_found.append(doc_metadata)

            data_sources_list = rm.get("data_sources", [])
            if data_sources_list:
                first = data_sources_list[0]
                if isinstance(first, dict):
                    data_source_name = first.get("label") or first.get("id") or data_source
                else:
                    data_source_name = first
            else:
                data_source_name = data_source

            return RAGMetadata(
                query_processing_time_ms=rm.get("query_processing_time_ms", 0),
                total_documents_searched=len(documents_found),
                documents_found=documents_found,
                data_source_name=data_source_name,
                retrieval_method=rm.get("retrieval_method", "similarity"),
            )

        except Exception as e:
            logger.warning("Failed to parse RAG metadata: %s", str(e))
            return None


def create_atlas_rag_client_from_config(config_manager) -> AtlasRAGClient:
    """Factory function to create AtlasRAGClient from ConfigManager.

    Args:
        config_manager: ConfigManager instance with app_settings.

    Returns:
        Configured AtlasRAGClient instance.
    """
    settings = config_manager.app_settings
    return AtlasRAGClient(
        base_url=settings.external_rag_url,
        bearer_token=settings.external_rag_bearer_token,
        default_model=settings.external_rag_default_model,
        top_k=settings.external_rag_top_k,
    )
