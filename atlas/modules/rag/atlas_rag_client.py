"""ATLAS RAG Client for integrating with the ATLAS RAG API.

This client implements the same interface as RAGClient but translates
requests to the ATLAS RAG API format.

ATLAS RAG API:
- Discovery: GET /discover/datasources?as_user={user}
- Query: POST /rag/completions?as_user={user}
"""

import logging
from typing import Dict, List, Optional

import httpx
from fastapi import HTTPException

from atlas.modules.rag.client import DataSource, DocumentMetadata, RAGMetadata, RAGResponse

logger = logging.getLogger(__name__)


class AtlasRAGClient:
    """Client for communicating with external ATLAS RAG API.

    Implements the same interface as RAGClient for seamless substitution.
    Uses Bearer token authentication with user impersonation via as_user param.
    """

    def __init__(
        self,
        base_url: str,
        bearer_token: Optional[str] = None,
        default_model: str = "openai/gpt-oss-120b",
        top_k: int = 4,
        timeout: float = 60.0,
    ):
        """Initialize the external RAG client.

        Args:
            base_url: Base URL for the external RAG API.
            bearer_token: Bearer token for API authentication.
            default_model: Default model to use for RAG queries.
            top_k: Default number of documents to retrieve.
            timeout: Request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.bearer_token = bearer_token
        self.default_model = default_model
        self.top_k = top_k
        self.timeout = timeout

        logger.info(
            "AtlasRAGClient initialized: url=%s, model=%s, top_k=%d",
            self.base_url,
            self.default_model,
            self.top_k,
        )

    def _get_headers(self) -> Dict[str, str]:
        """Build HTTP headers for API requests."""
        headers = {"Content-Type": "application/json"}
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        return headers

    async def discover_data_sources(self, user_name: str) -> List[DataSource]:
        """Discover data sources accessible by a user.

        Calls GET /discover/datasources?as_user={user_name}

        Args:
            user_name: The username to discover data sources for.

        Returns:
            List of DataSource objects the user can access.
        """
        logger.info("Discovering data sources for user: %s", user_name)

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(
                    f"{self.base_url}/discover/datasources",
                    headers=self._get_headers(),
                    params={"as_user": user_name},
                )
                response.raise_for_status()
                data = response.json()

                # Response format: {data_sources: [{id, label, compliance_level, description}]}
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
        self, user_name: str, data_source: str, messages: List[Dict]
    ) -> RAGResponse:
        """Query RAG endpoint for a response with metadata.

        Calls POST /rag/completions?as_user={user_name}

        Args:
            user_name: The username making the query.
            data_source: The data source (corpus) to query.
            messages: List of message dictionaries with role and content.

        Returns:
            RAGResponse containing content and optional metadata.

        Raises:
            HTTPException: On API errors (403, 404, 500).
        """
        logger.info(
            "[HTTP-RAG] query_rag called: user=%s, data_source=%s, message_count=%d",
            user_name,
            data_source,
            len(messages),
        )

        # Extract user query for logging
        user_query = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                user_query = msg.get("content", "")[:100]
                break
        logger.debug(
            "[HTTP-RAG] Query preview: %s...",
            user_query,
        )

        # Build request payload matching RagRequest format
        payload = {
            "messages": messages,
            "stream": False,
            "model": self.default_model,
            "top_k": self.top_k,
            "corpora": [data_source] if data_source else None,
            "threshold": None,
            "expanded_window": [0, 0],
        }

        logger.debug(
            "[HTTP-RAG] Request payload: model=%s, top_k=%d, corpora=%s",
            payload["model"],
            payload["top_k"],
            payload["corpora"],
        )

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/rag/completions",
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

                # Check if this is a chat completion (already LLM-interpreted)
                is_completion = data.get("object") == "chat.completion"

                # Extract content from OpenAI ChatCompletion format
                content = "No response from RAG system."
                if "choices" in data and len(data["choices"]) > 0:
                    choice = data["choices"][0]
                    if "message" in choice and "content" in choice["message"]:
                        content = choice["message"]["content"]

                logger.debug(
                    "[HTTP-RAG] Extracted content: length=%d, is_completion=%s, preview=%s...",
                    len(content),
                    is_completion,
                    content[:300] if content else "(empty)",
                )

                # Map rag_metadata to RAGMetadata
                metadata = self._parse_rag_metadata(data, data_source)

                logger.info(
                    "[HTTP-RAG] query_rag complete: user=%s, source=%s, content_length=%d, has_metadata=%s, is_completion=%s",
                    user_name,
                    data_source,
                    len(content),
                    metadata is not None,
                    is_completion,
                )
                return RAGResponse(content=content, metadata=metadata, is_completion=is_completion)

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
                    raise HTTPException(
                        status_code=500, detail="RAG service error"
                    )

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
                # Re-raise HTTPExceptions
                raise

            except Exception as exc:
                logger.error(
                    "Unexpected error querying RAG for %s: %s",
                    user_name,
                    str(exc),
                    exc_info=True,
                )
                raise HTTPException(status_code=500, detail="Internal server error")

    def _parse_rag_metadata(
        self, data: Dict, data_source: str
    ) -> Optional[RAGMetadata]:
        """Parse rag_metadata from API response into RAGMetadata model.

        Args:
            data: The full API response dictionary.
            data_source: The data source used in the query.

        Returns:
            RAGMetadata if present in response, None otherwise.
        """
        if "rag_metadata" not in data or not data["rag_metadata"]:
            return None

        try:
            rm = data["rag_metadata"]

            # Map documents_found to DocumentMetadata list
            documents_found = []
            for doc in rm.get("documents_found", []):
                doc_metadata = DocumentMetadata(
                    source=doc.get("corpus_id", ""),
                    content_type=doc.get("content_type", "atlas-search"),
                    confidence_score=doc.get("confidence_score", 0.0),
                    chunk_id=str(doc.get("id")) if doc.get("id") else None,
                    last_modified=doc.get("last_modified"),
                )
                documents_found.append(doc_metadata)

            # Determine data source name from response or fallback
            data_sources_list = rm.get("data_sources", [])
            data_source_name = (
                data_sources_list[0] if data_sources_list else data_source
            )

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
