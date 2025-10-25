"""RAG module for the chat backend.

This module provides:
- RAG query processing and context retrieval
- Document metadata and search capabilities  
- Integration with external RAG services
"""

from .client import RAGClient, DocumentMetadata, RAGMetadata, RAGResponse

# Create default instance
rag_client = RAGClient()

__all__ = [
    "RAGClient",
    "DocumentMetadata", 
    "RAGMetadata",
    "RAGResponse",
    "rag_client",
]