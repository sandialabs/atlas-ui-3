"""ATLAS RAG Client for the ATLAS RAG API (OpenAPI v0.3.0.dev1+).

Implements the newest ATLAS RAG spec:

  - GET  /api/v1/discover/datasources?role=read|write&as_user={user}
  - POST /api/v1/rag/completions?as_user={user}

Request body (RagRequest):

    {"messages": [...], "stream": false, "corpora": "<id>" | ["<id>", ...]}

Response body (RagResponse):

    {
      "message":  {"role": "assistant", "content": "..."},
      "metadata": {
        "response_time": <int seconds>,
        "references": [
          {
            "citation": "IEEE format" | null,
            "document_ref": 1,
            "filename": "doc.pdf",
            "sections": [
              {"section_ref": 1, "text": "snippet...", "relevance": 0.92}
            ]
          },
          ...
        ]
      }
    }
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
    Section,
    URLCitation,
)

logger = logging.getLogger(__name__)


class AtlasRAGClient:
    """Client for the external ATLAS RAG API.

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
            default_model: Kept for backwards-compat configuration; the newest
                spec does not accept a model in the request body, so this is
                effectively unused at request time.
            top_k: Retained for legacy callers; the newest spec has no
                top_k field in RagRequest.
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

        Accepts either a bare list or ``{"data_sources": [...]}`` envelope —
        the OpenAPI spec returns a bare list; some servers still envelope it.
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

                if isinstance(data, list):
                    sources_list = data
                elif isinstance(data, dict):
                    sources_list = data.get("data_sources", [])
                else:
                    sources_list = []

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
        body. The newest spec uses ``corpora`` (string or list) at the top
        level — no ``hybrid_search_kwargs``, no ``model``, no ``top_k``.

        Args:
            user_name: The username making the query.
            data_source: Single data source (corpus). Used when
                ``data_sources`` is not provided.
            messages: Message history (role/content dicts).
            data_sources: Multiple data sources to query in one request.
                When provided, takes precedence over ``data_source``.
            hybrid_search_kwargs: Accepted for caller compatibility; the
                newest spec has no place to forward these fields and they
                are intentionally ignored.

        Returns:
            RAGResponse containing the assistant content and parsed metadata
            (including per-reference section snippets).
        """
        if data_sources:
            corpora: Optional[Any] = list(data_sources)
        elif data_source:
            corpora = data_source
        else:
            corpora = None

        user_name = self._resolve_username(user_name)

        if hybrid_search_kwargs:
            logger.debug(
                "[HTTP-RAG] Ignoring hybrid_search_kwargs (not part of newest spec): %s",
                hybrid_search_kwargs,
            )

        logger.info(
            "[HTTP-RAG] query_rag called: user=%s, corpora=%s, message_count=%d",
            user_name,
            corpora,
            len(messages),
        )

        payload: Dict[str, Any] = {
            "messages": messages,
            "stream": False,
        }
        if corpora is not None:
            payload["corpora"] = corpora

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
                    list(data.keys()) if isinstance(data, dict) else type(data).__name__,
                )

                content, message_dict = self._extract_message(data)
                metadata = self._parse_response_metadata(data, data_source)
                annotations = self._parse_annotations(message_dict)

                logger.info(
                    "[HTTP-RAG] query_rag complete: user=%s, source=%s, "
                    "content_length=%d, has_metadata=%s, references=%d",
                    user_name,
                    data_source,
                    len(content),
                    metadata is not None,
                    len(metadata.documents_found) if metadata else 0,
                )
                return RAGResponse(
                    content=content,
                    metadata=metadata,
                    is_completion=True,
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
    def _extract_message(data: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
        """Pull assistant content from either the newest or legacy response shape.

        Newest spec: ``{"message": {"role": "assistant", "content": "..."}, ...}``.
        Legacy: ``{"choices": [{"message": {"content": "..."}}], ...}``.
        """
        content = "No response from RAG system."
        message: Dict[str, Any] = {}

        if isinstance(data.get("message"), dict):
            message = data["message"]
            msg_content = message.get("content")
            if msg_content:
                content = msg_content
            return content, message

        choices = data.get("choices") or []
        if choices:
            first = choices[0] or {}
            message = first.get("message") or {}
            msg_content = message.get("content")
            if msg_content:
                content = msg_content
        return content, message

    @staticmethod
    def _parse_annotations(message: Dict[str, Any]) -> List[URLCitation]:
        """Extract url_citation annotations from a message (legacy compat).

        The newest spec does not include url_citation annotations — they
        were specific to the prior OpenAI chat.completion envelope. Parsing
        is kept for backwards-compat with older mock instances during the
        roll-forward window.
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

    def _parse_response_metadata(
        self,
        data: Dict[str, Any],
        data_source: str,
    ) -> Optional[RAGMetadata]:
        """Parse the response metadata block.

        Prefers the newest-spec shape (``metadata.references`` + per-
        reference ``sections``). Falls back to the legacy ``rag_metadata``
        + ``documents_found`` shape so existing servers keep working
        during a rolling migration.
        """
        if isinstance(data.get("metadata"), dict) and "references" in data["metadata"]:
            return self._parse_metadata_newest(data["metadata"], data_source)

        if "rag_metadata" in data and data["rag_metadata"]:
            return self._parse_rag_metadata_legacy(data, data_source)

        return None

    def _parse_metadata_newest(
        self,
        metadata: Dict[str, Any],
        data_source: str,
    ) -> Optional[RAGMetadata]:
        """Parse the newest-spec metadata block.

        Maps each ``Reference`` into a ``DocumentMetadata`` whose
        ``sections`` carry the actual snippet text matched to the query.
        The reference's top section relevance becomes ``confidence_score``
        so existing UI/sorting code still works without changes.
        """
        try:
            references_raw = metadata.get("references") or []
            documents_found: List[DocumentMetadata] = []

            for ref in references_raw:
                if not isinstance(ref, dict):
                    continue
                sections_raw = ref.get("sections") or []
                sections: List[Section] = []
                for sec in sections_raw:
                    if not isinstance(sec, dict):
                        continue
                    try:
                        sections.append(Section(**sec))
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Skipping malformed section: %s", exc)

                top_relevance = max((s.relevance for s in sections), default=0.0)
                filename = ref.get("filename") or ""

                try:
                    doc_metadata = DocumentMetadata(
                        source=data_source or filename,
                        content_type="atlas-search",
                        confidence_score=top_relevance,
                        chunk_id=None,
                        last_modified=None,
                        title=filename or None,
                        url=None,
                        citation=ref.get("citation"),
                        document_ref=ref.get("document_ref"),
                        sections=sections,
                    )
                    documents_found.append(doc_metadata)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Skipping malformed reference (filename=%s): %s",
                        filename,
                        exc,
                    )

            response_time = metadata.get("response_time", 0) or 0
            # Spec describes response_time in seconds; surface as ms for the
            # existing UI footer that says "Xms".
            processing_ms = int(response_time * 1000)

            return RAGMetadata(
                query_processing_time_ms=processing_ms,
                total_documents_searched=len(documents_found),
                documents_found=documents_found,
                data_source_name=data_source or "",
                retrieval_method="similarity",
            )

        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to parse newest RAG metadata: %s", str(e))
            return None

    def _parse_rag_metadata_legacy(
        self,
        data: Dict[str, Any],
        data_source: str,
    ) -> Optional[RAGMetadata]:
        """Parse the legacy ``rag_metadata`` + ``documents_found`` shape.

        Retained so this client can still talk to older mock instances
        while the spec rolls out. Newest-spec parsing in
        ``_parse_metadata_newest`` is the primary path.
        """
        try:
            rm = data["rag_metadata"]

            documents_found: List[DocumentMetadata] = []
            for doc in rm.get("documents_found", []):
                ds = doc.get("data_source") if isinstance(doc.get("data_source"), dict) else {}
                source = ds.get("id") or doc.get("corpus_id") or ""
                title = doc.get("title") or ds.get("label")
                url = doc.get("url")

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

        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to parse legacy RAG metadata: %s", str(e))
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
