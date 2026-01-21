"""RAG module for the chat backend.

This module provides:
- RAG query processing and context retrieval
- Document metadata and search capabilities
- Integration with ATLAS RAG API
"""

from .client import RAGClient, DataSource, DocumentMetadata, RAGMetadata, RAGResponse
from .atlas_rag_client import AtlasRAGClient, create_atlas_rag_client_from_config

# Create default instance
rag_client = RAGClient()

__all__ = [
    "RAGClient",
    "AtlasRAGClient",
    "create_atlas_rag_client_from_config",
    "DataSource",
    "DocumentMetadata",
    "RAGMetadata",
    "RAGResponse",
    "rag_client",
]