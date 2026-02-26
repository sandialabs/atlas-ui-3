"""RAG Client for integrating with RAG mock endpoint."""

import logging
from typing import Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel

from atlas.core.http_client import create_rag_client


class DataSource(BaseModel):
    """Represents a RAG data source with compliance information."""
    id: str
    label: str
    compliance_level: str = "CUI"
    description: str = ""

logger = logging.getLogger(__name__)


class DocumentMetadata(BaseModel):
    """Metadata about a source document."""
    source: str
    content_type: str
    confidence_score: float
    chunk_id: Optional[str] = None
    last_modified: Optional[str] = None


class RAGMetadata(BaseModel):
    """Metadata about RAG query processing."""
    query_processing_time_ms: int
    total_documents_searched: int
    documents_found: List[DocumentMetadata]
    data_source_name: str
    retrieval_method: str
    query_embedding_time_ms: Optional[int] = None


class RAGResponse(BaseModel):
    """Combined response from RAG system including content and metadata."""
    content: str
    metadata: Optional[RAGMetadata] = None
    is_completion: bool = False  # True if content is already LLM-interpreted (from /rag/completions)


class RAGClient:
    """Legacy RAG client for the old rag-mock service.

    Note: This client is deprecated. Use UnifiedRAGService for RAG operations,
    which handles all RAG sources configured in rag-sources.json.
    """

    def __init__(self, base_url: str = "http://localhost:8001", timeout: float = 30.0):
        """Initialize the legacy RAG client.

        Args:
            base_url: Base URL for the RAG mock service.
            timeout: Request timeout in seconds.
        """
        self.base_url = base_url
        self.timeout = timeout
        self.test_client = None
        self.http_client = create_rag_client(self.base_url, self.timeout)
        logger.warning(
            "RAGClient is deprecated. Use UnifiedRAGService for RAG operations. "
            "Configure RAG sources in rag-sources.json."
        )
        logger.info("RAGClient initialized with URL: %s", self.base_url)

    async def discover_data_sources(self, user_name: str) -> List[DataSource]:
        """Discover data sources accessible by a user.

        Note: This method is deprecated. Use UnifiedRAGService.discover_data_sources() instead.
        """
        logger.info("discover_data_sources: user=%s (deprecated RAGClient)", user_name)

        try:
            data = await self.http_client.get(f"/v1/discover/datasources/{user_name}")
            # Support both v1 (accessible_data_sources) and v2 (data_sources) response formats
            sources_list = data.get("data_sources", data.get("accessible_data_sources", []))
        except HTTPException as exc:
            logger.warning("HTTP error discovering data sources for %s: %s", user_name, exc.detail)
            return []
        except Exception as exc:
            logger.error("Unexpected error while discovering data sources for %s: %s", user_name, exc, exc_info=True)
            return []

        return [DataSource(**source_data) for source_data in sources_list]

    async def query_rag(self, user_name: str, data_source: str, messages: List[Dict]) -> RAGResponse:
        """Query RAG endpoint for a response with metadata.

        Note: This method is deprecated. Use UnifiedRAGService.query_rag() instead.
        """
        payload = {
            "messages": messages,
            "user_name": user_name,
            "data_source": data_source,
            "model": "gpt-4-rag-mock",
            "stream": False
        }

        logger.info("query_rag: user=%s, source=%s (deprecated RAGClient)", user_name, data_source)

        try:
            data = await self.http_client.post("/v1/chat/completions", json_data=payload)

            # Extract the assistant message from the response
            content = "No response from RAG system."
            if "choices" in data and len(data["choices"]) > 0:
                choice = data["choices"][0]
                if "message" in choice and "content" in choice["message"]:
                    content = choice["message"]["content"]

            # Extract metadata if present
            metadata = None
            if "rag_metadata" in data and data["rag_metadata"]:
                try:
                    metadata = RAGMetadata(**data["rag_metadata"])
                except Exception as e:
                    logger.warning(f"Failed to parse RAG metadata: {e}")

            return RAGResponse(content=content, metadata=metadata)

        except HTTPException:
            # Re-raise HTTPExceptions from the unified client (they already have proper error handling)
            raise
        except Exception as exc:
            logger.error("Unexpected error while querying RAG for %s: %s", user_name, exc, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")
