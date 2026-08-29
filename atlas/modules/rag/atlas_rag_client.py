"""ATLAS RAG Client for the ATLAS RAG API (OpenAPI v0.8.0).

Implements the ATLAS RAG v2 contract:

  - GET  /api/v2/discover/datasources?role=read|write&as_user={user}
  - POST /api/v2/rag/query?as_user={user}

v2 sends an explicit ``query`` string and a ``search_kwargs`` block instead
of the conversation. The backend always returns a synthesized ``response``
string plus the ``references`` it was built from (see
:meth:`AtlasRAGClient.query_v2`). ``mode`` is a client-side knob that decides
whether ATLAS uses the response verbatim (``synthesized``) or rebuilds an
evidence block from the reference snippets (``raw``); it is never sent on
the wire.

The v1 contract is kept for backward compatibility:

  - GET  /api/v1/discover/datasources?role=read|write&as_user={user}
  - POST /api/v1/rag/completions?as_user={user}

v1 request body (RagRequest):

    {"messages": [...], "stream": false, "corpora": "<id>" | ["<id>", ...]}

v1 response body (RagResponse):

    {
      "message":  {"role": "assistant", "content": "..."},
      "metadata": {
        "response_time": <int seconds>,
        "references": [
          {
            "citation": "IEEE format" | null,
            "reference": "human-readable source line" | null,
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
from typing import Any, Dict, List, Optional, Union

import httpx
from fastapi import HTTPException

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.modules.rag.client import (
    RAG_MODE_RAW,
    RAG_MODE_SYNTHESIZED,
    RAG_MODES,
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

    DEFAULT_DISCOVERY_PATH_V2 = "/api/v2/discover/datasources"
    DEFAULT_QUERY_PATH_V2 = "/api/v2/rag/query"

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
        api_version: str = "v1",
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
                Defaults to the discovery path of ``api_version``.
            query_path: Override for the query endpoint path. Defaults to the
                query path of ``api_version``.
            api_version: Which ATLAS RAG contract this backend speaks --
                ``"v1"`` (conversation + completion) or ``"v2"`` (explicit
                query + ``raw``/``synthesized`` mode). Only the default paths
                and which query method the caller may use depend on it;
                authentication and impersonation are identical.
        """
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.default_model = default_model
        self.top_k = top_k
        self.timeout = timeout
        self.strip_domain = strip_domain
        self.api_version = "v2" if str(api_version).lower() == "v2" else "v1"
        is_v2 = self.api_version == "v2"
        self.discovery_path = discovery_path or (
            self.DEFAULT_DISCOVERY_PATH_V2 if is_v2 else self.DEFAULT_DISCOVERY_PATH
        )
        self.query_path = query_path or (
            self.DEFAULT_QUERY_PATH_V2 if is_v2 else self.DEFAULT_QUERY_PATH
        )

        logger.info(
            "AtlasRAGClient initialized: url=%s, api_version=%s, model=%s, top_k=%d, "
            "strip_domain=%s, discovery=%s, query=%s",
            self.base_url,
            self.api_version,
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

    async def query_v2(
        self,
        user_name: str,
        query: str,
        corpora: Union[str, List[str]],
        mode: str = RAG_MODE_RAW,
        top_k: Optional[int] = None,
        filters: Optional[Dict[str, Any]] = None,
        synthesis_params: Optional[Dict[str, Any]] = None,
        search_kwargs: Optional[Dict[str, Any]] = None,
    ) -> RAGResponse:
        """Query the v2 endpoint with an explicit query string.

        Calls ``POST {query_path}?as_user={user_name}`` with a v0.8.0 request
        body: ``{query, corpora, search_kwargs}``. The conversation is never
        sent -- only ``query`` -- so a v2 backend receives the question and
        nothing else.

        ``mode`` is a **client-side** interpretation knob, not a wire field:
        the v0.8.0 schema has no ``mode``. The backend always returns a
        synthesized ``response`` string plus the ``references`` behind it.
        The caller decides how ATLAS consumes that:

        * ``"raw"`` -- build an evidence block from the reference snippets so
          ATLAS's own LLM can reason over it (``is_completion=False``).
        * ``"synthesized"`` -- use the backend's ``response`` verbatim
          (``is_completion=True``).

        Args:
            user_name: The username making the query.
            query: The specific question to ask. Must be non-empty.
            corpora: Corpus id or list of corpus ids to search.
            mode: ``"raw"`` (evidence for our LLM) or ``"synthesized"`` (an
                answer from the backend). Client-side only; never sent.
            top_k: Max results per corpus. Mapped to
                ``search_kwargs.top_k_final`` when ``search_kwargs`` is not
                supplied. Falls back to the client's configured ``top_k``.
            filters: Accepted for backwards compatibility; the v0.8.0 schema
                has no filters field, so this is not sent.
            synthesis_params: Accepted for backwards compatibility; not sent.
            search_kwargs: Raw ``search_kwargs`` dict forwarded as-is. When
                provided, takes precedence over ``top_k``.

        Returns:
            RAGResponse whose ``content`` is the evidence block (``raw``) or
            the synthesized answer (``synthesized``), with the retrieved
            documents in ``metadata``.

        Raises:
            ValueError: ``query`` is empty/blank or ``mode`` is not one of
                ``raw``/``synthesized``. Both are caller bugs, not backend
                failures, so they are not mapped to an HTTPException.
            HTTPException: The backend rejected or failed the request.
        """
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        if mode not in RAG_MODES:
            raise ValueError(
                f"mode must be one of {sorted(RAG_MODES)}, got {mode!r}"
            )

        corpora_list = [corpora] if isinstance(corpora, str) else list(corpora or [])
        if not corpora_list:
            raise ValueError("corpora must name at least one corpus")

        user_name = self._resolve_username(user_name)

        # Build search_kwargs: an explicit dict wins; otherwise map top_k.
        if search_kwargs is not None:
            kwargs_out: Dict[str, Any] = dict(search_kwargs)
        else:
            kwargs_out = {}
            effective_top_k = self.top_k if top_k is None else top_k
            if effective_top_k is not None:
                kwargs_out["top_k_final"] = effective_top_k

        payload: Dict[str, Any] = {
            "query": query,
            "corpora": corpora if isinstance(corpora, str) else corpora_list,
        }
        if kwargs_out:
            payload["search_kwargs"] = kwargs_out

        if filters or synthesis_params:
            logger.debug(
                "[HTTP-RAG-v2] Ignoring filters/synthesis_params "
                "(not in v0.8.0 schema)"
            )

        # The query text itself is never logged -- v2 exists partly to keep
        # user text away from places it does not need to be.
        logger.info(
            "[HTTP-RAG-v2] query called: user=%s, corpora=%s, mode=%s, query_chars=%d",
            user_name,
            corpora_list,
            mode,
            len(query),
        )

        # The corpus label on the parsed metadata: a single corpus names
        # itself, several are joined so the UI footer says what was searched.
        data_source_label = (
            corpora_list[0] if len(corpora_list) == 1 else ", ".join(corpora_list)
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

                if not isinstance(data, dict):
                    logger.error(
                        "[HTTP-RAG-v2] Unexpected response type: %s",
                        type(data).__name__,
                    )
                    raise HTTPException(status_code=500, detail="RAG service error")

                content, documents = self._parse_v2_results(
                    data, mode, data_source_label
                )
                metadata = self._build_v2_metadata(
                    data.get("metadata") or {}, documents, data_source_label, mode
                )

                logger.info(
                    "[HTTP-RAG-v2] query complete: user=%s, corpora=%s, mode=%s, "
                    "content_length=%d, documents=%d",
                    user_name,
                    corpora_list,
                    mode,
                    len(content),
                    len(documents),
                )
                return RAGResponse(
                    content=content,
                    metadata=metadata,
                    is_completion=(mode == RAG_MODE_SYNTHESIZED),
                )

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                # The response body is deliberately not logged: a backend can
                # echo the query (or other user text) into its error detail,
                # and v2 exists to keep that text out of places it does not
                # need to be. The status and endpoint are what diagnose this.
                logger.error(
                    "HTTP error querying RAG v2 for %s: status %d from %s",
                    user_name,
                    status_code,
                    self.query_path,
                )
                if status_code == 400:
                    raise HTTPException(
                        status_code=400, detail="Invalid RAG query request"
                    )
                elif status_code == 403:
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
                    "Request error querying RAG v2 for %s: %s",
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
                    "Unexpected error querying RAG v2 for %s: %s",
                    user_name,
                    str(exc),
                    exc_info=True,
                )
                raise HTTPException(status_code=500, detail="Internal server error")

    @classmethod
    def _parse_v2_results(
        cls,
        data: Dict[str, Any],
        mode: str,
        data_source: str,
    ) -> tuple[str, List[DocumentMetadata]]:
        """Turn a v0.8.0 response into content plus document metadata.

        The backend always returns ``response`` (a synthesized answer) and
        ``metadata.references`` (the evidence). ``mode`` decides which ATLAS
        uses: ``synthesized`` takes the response verbatim; ``raw`` builds an
        evidence block from the reference snippets.
        """
        references_raw = (data.get("metadata") or {}).get("references") or []
        documents = cls._parse_v2_documents(references_raw, data_source)

        if mode == RAG_MODE_SYNTHESIZED:
            answer = data.get("response")
            content = answer if isinstance(answer, str) and answer else (
                "No response from RAG system."
            )
            return content, documents

        return cls._format_raw_evidence(documents), documents

    @staticmethod
    def _parse_v2_documents(
        entries: Any,
        data_source: str,
    ) -> List[DocumentMetadata]:
        """Map v0.8.0 ``references`` entries to ``DocumentMetadata``.

        Each reference carries ``filename``, ``sections`` (text + relevance),
        and ``reference`` (a human-readable label). The label becomes the
        document title so the UI renders something meaningful.
        """
        documents: List[DocumentMetadata] = []
        if not isinstance(entries, list):
            return documents

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            sections: List[Section] = []
            for sec in entry.get("sections") or []:
                if not isinstance(sec, dict):
                    continue
                try:
                    sections.append(Section(**sec))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Skipping malformed v2 section: %s", exc)

            filename = entry.get("filename") or ""
            reference_label = entry.get("reference")
            if not isinstance(reference_label, str) or not reference_label.strip():
                reference_label = None
            confidence = max((s.relevance for s in sections), default=0.0)

            try:
                documents.append(
                    DocumentMetadata(
                        source=data_source or filename,
                        content_type="atlas-search",
                        confidence_score=confidence,
                        chunk_id=None,
                        last_modified=None,
                        title=reference_label or filename or None,
                        url=entry.get("url"),
                        citation=entry.get("citation"),
                        document_ref=entry.get("document_ref"),
                        sections=sections,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Skipping malformed v2 reference (filename=%s): %s",
                    filename,
                    exc,
                )

        return documents

    @staticmethod
    def _format_raw_evidence(documents: List[DocumentMetadata]) -> str:
        """Render retrieved evidence as the text an LLM reasons over.

        ``raw`` mode returns chunks, not prose, so the client composes the
        block the caller injects as context. Snippets keep their ``[N]``
        document markers, which is what the existing citation pipeline in the
        UI matches on -- so a v2 raw answer cites exactly like a v1 one.
        When the backend does not supply ``document_ref`` (the v0.8.0 schema
        does not), references are numbered sequentially so the markers stay
        stable and unique.
        """
        if not documents:
            return "No relevant documents were retrieved."

        parts: List[str] = [
            f"Retrieved {len(documents)} document(s):",
            "",
        ]
        for idx, doc in enumerate(documents, start=1):
            ref = doc.document_ref
            marker = f"[{ref}] " if ref is not None else f"[{idx}] "
            heading = doc.title or doc.source or "document"
            parts.append(f"{marker}{heading}")
            for section in doc.sections:
                parts.append(f"  - {section.text}")
            if not doc.sections and doc.citation:
                parts.append(f"  - {doc.citation}")
            parts.append("")
        return "\n".join(parts).rstrip()

    @staticmethod
    def _build_v2_metadata(
        metadata: Dict[str, Any],
        documents: List[DocumentMetadata],
        data_source: str,
        mode: str,
    ) -> Optional[RAGMetadata]:
        """Build ``RAGMetadata`` from the v0.8.0 ``metadata`` block.

        v0.8.0 reports ``response_time`` in whole seconds (not ms).
        ``corpora_searched`` is no longer part of the schema, so the data
        source label passed by the caller is used directly.
        """
        if not documents and not metadata:
            return None

        raw_seconds = metadata.get("response_time") if isinstance(metadata, dict) else None
        try:
            seconds = int(raw_seconds) if raw_seconds is not None else 0
        except (TypeError, ValueError):
            logger.warning("Ignoring non-numeric v2 response_time: %r", raw_seconds)
            seconds = 0
        processing_ms = max(0, seconds * 1000)

        return RAGMetadata(
            query_processing_time_ms=processing_ms,
            total_documents_searched=len(documents),
            documents_found=documents,
            data_source_name=data_source or "",
            retrieval_method=f"v2_{mode}",
        )

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

        The displayed label prefers the backend's ``reference`` string and
        falls back to ``filename``, so backends that only send a filename
        render exactly as before.
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
                # Newer backends send a human-readable ``reference`` string
                # (e.g. an IEEE-style source line) in place of the older
                # ``citation`` field. Prefer it as the displayed label and
                # fall back to the filename when it is absent.
                reference = ref.get("reference")
                if not isinstance(reference, str) or not reference.strip():
                    reference = None

                try:
                    doc_metadata = DocumentMetadata(
                        source=data_source or filename,
                        content_type="atlas-search",
                        confidence_score=top_relevance,
                        chunk_id=None,
                        last_modified=None,
                        title=reference or filename or None,
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
