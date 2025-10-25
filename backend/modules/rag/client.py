"""RAG Client for integrating with RAG mock endpoint."""

import logging
import os
from typing import Dict, List, Optional, Any
from fastapi import HTTPException
from pydantic import BaseModel

from core.http_client import create_rag_client

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


class RAGClient:
    """Client for communicating with RAG mock API."""
    
    def __init__(self):
        from modules.config import config_manager
        app_settings = config_manager.app_settings
        self.mock_mode = app_settings.mock_rag
        self.base_url = app_settings.rag_mock_url
        self.timeout = 30.0
        self.test_client = None
        self.http_client = create_rag_client(self.base_url, self.timeout)
        
        if self.mock_mode:
            self._setup_test_client()
            logger.info(f"RAG Client initialized in mock mode: {self.mock_mode}")
        else:
            logger.info(f"RAG Client initialized in HTTP mode, URL: {self.base_url}")
    
    def _setup_test_client(self):
        """Set up FastAPI TestClient for mock mode."""
        try:
            import sys
            import os
            # Add the repo-level mocks/rag-mock directory to the path
            # Current file: backend/modules/rag/client.py
            # Repo mock app: mocks/rag-mock/main_rag_mock.py
            rag_mock_path = os.path.abspath(
                os.path.join(os.path.dirname(__file__), "..", "..", "..", "mocks", "rag-mock")
            )
            logger.info(f"Adding RAG mock path to sys.path: {rag_mock_path}")

            if rag_mock_path not in sys.path:
                sys.path.insert(0, rag_mock_path)

            from fastapi.testclient import TestClient
            # Import the app from the RAG mock
            logger.info("Importing main_rag_mock module...")
            from main_rag_mock import app as rag_app

            self.test_client = TestClient(rag_app)
            logger.info("RAG TestClient initialized successfully")
        except Exception as exc:
            logger.error(f"Failed to setup RAG TestClient: {exc}", exc_info=True)
            # Fall back to HTTP mode
            self.mock_mode = False
        
    async def discover_data_sources(self, user_name: str) -> List[str]:
        """Discover data sources accessible by a user."""
        use_test_client = bool(self.mock_mode and self.test_client)
        logger.info(
            "discover_data_sources: user=%s strategy=%s mock_mode=%s test_client=%s",
            user_name,
            "TestClient" if use_test_client else "HTTP",
            self.mock_mode,
            self.test_client is not None,
        )

        if use_test_client:
            try:
                response = self.test_client.get(f"/v1/discover/datasources/{user_name}")
                response.raise_for_status()
                data = response.json()
                return data.get("accessible_data_sources", [])
            except Exception as exc:
                logger.error(f"TestClient error while discovering data sources for {user_name}: {exc}", exc_info=True)
                return []
        
        # HTTP mode using unified client
        try:
            data = await self.http_client.get(f"/v1/discover/datasources/{user_name}")
            return data.get("accessible_data_sources", [])
        except HTTPException as exc:
            logger.warning(f"HTTP error discovering data sources for {user_name}: {exc.detail}")
            # Return empty list for graceful degradation instead of raising
            return []
        except Exception as exc:
            logger.error(f"Unexpected error while discovering data sources for {user_name}: {exc}", exc_info=True)
            return []
    
    async def query_rag(self, user_name: str, data_source: str, messages: List[Dict]) -> RAGResponse:
        """Query RAG endpoint for a response with metadata."""
        payload = {
            "messages": messages,
            "user_name": user_name,
            "data_source": data_source,
            "model": "gpt-4-rag-mock",
            "stream": False
        }
        
        if self.mock_mode and self.test_client:
            try:
                logger.info(f"Using TestClient to query RAG for {user_name} with data source {data_source}")
                response = self.test_client.post("/v1/chat/completions", json=payload)
                response.raise_for_status()
                data = response.json()
                
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
                
            except Exception as exc:
                logger.error(f"TestClient error while querying RAG for {user_name}: {exc}", exc_info=True)
                if hasattr(exc, 'response') and hasattr(exc.response, 'status_code'):
                    if exc.response.status_code == 403:
                        raise HTTPException(status_code=403, detail="Access denied to data source")
                    elif exc.response.status_code == 404:
                        raise HTTPException(status_code=404, detail="Data source not found")
                raise HTTPException(status_code=500, detail="Internal server error")
        
        # HTTP mode using unified client
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
            logger.error(f"Unexpected error while querying RAG for {user_name}: {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")


def initialize_rag_client():
    """Initialize the global RAG client after environment variables are loaded."""
    global rag_client
    rag_client = RAGClient()
    return rag_client


# Global RAG client instance - will be initialized in main.py after env vars are loaded
rag_client = None