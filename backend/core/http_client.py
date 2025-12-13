"""HTTP client for RAG service communication."""

import logging
from typing import Any, Dict, Optional
import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)


class RAGHTTPClient:
    """HTTP client for RAG service API calls."""
    
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout)
        logger.info(f"RAGHTTPClient initialized with base_url={self.base_url}, timeout={timeout}")
    
    async def get(self, path: str) -> Dict[str, Any]:
        """Execute GET request to RAG service."""
        url = f"{self.base_url}{path}"
        try:
            logger.debug(f"GET {url}")
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code} from {url}: {e.response.text}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"RAG service error: {e.response.text}"
            )
        except httpx.RequestError as e:
            logger.error(f"Request failed to {url}: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"RAG service unavailable: {str(e)}"
            )
    
    async def post(self, path: str, json_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Execute POST request to RAG service."""
        url = f"{self.base_url}{path}"
        try:
            logger.debug(f"POST {url}")
            response = await self.client.post(url, json=json_data)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error {e.response.status_code} from {url}: {e.response.text}")
            raise HTTPException(
                status_code=e.response.status_code,
                detail=f"RAG service error: {e.response.text}"
            )
        except httpx.RequestError as e:
            logger.error(f"Request failed to {url}: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"RAG service unavailable: {str(e)}"
            )
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


def create_rag_client(base_url: str = "", timeout: float = 30.0) -> RAGHTTPClient:
    """Create a RAG HTTP client for communicating with RAG service."""
    return RAGHTTPClient(base_url, timeout)