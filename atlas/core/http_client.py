"""
Minimal HTTP client stub for basic chat functionality.
This is a temporary implementation for testing.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def create_rag_client(base_url: str = "", timeout: float = 30.0) -> Any:
    """
    Create a simple RAG client stub.
    For basic chat, this just returns a mock client.
    """
    class MockRAGClient:
        def __init__(self):
            pass
        
        async def query(self, *args, **kwargs):
            """Mock RAG query - returns empty result."""
            return {
                "content": "RAG not available in basic chat mode",
                "metadata": {}
            }
    
    return MockRAGClient()