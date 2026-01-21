"""RAG interface protocols."""

from typing import Dict, List, Protocol, runtime_checkable

from modules.rag.client import DataSource, RAGResponse


@runtime_checkable
class RAGClientProtocol(Protocol):
    """Protocol for RAG client implementations.

    Defines the interface that all RAG clients must implement,
    enabling dependency injection and easier testing.
    """

    async def discover_data_sources(self, user_name: str) -> List[DataSource]:
        """Discover data sources accessible by a user.

        Args:
            user_name: The username to discover data sources for.

        Returns:
            List of DataSource objects the user can access.
        """
        ...

    async def query_rag(
        self, user_name: str, data_source: str, messages: List[Dict]
    ) -> RAGResponse:
        """Query RAG endpoint for a response with metadata.

        Args:
            user_name: The username making the query.
            data_source: The data source to query.
            messages: List of message dictionaries with role and content.

        Returns:
            RAGResponse containing content and optional metadata.
        """
        ...
