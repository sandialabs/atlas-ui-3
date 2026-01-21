"""Integration tests for ExternalRAGClient with the mock service.

These tests require the external-rag-mock service to be running.
They can be skipped if the mock service is not available.
"""

import asyncio
import subprocess
import time
import sys
import os

import pytest
import httpx

# Add paths for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.rag.external_rag_client import ExternalRAGClient


MOCK_URL = "http://localhost:8002"
MOCK_TOKEN = "test-atlas-rag-token"
MOCK_STARTUP_TIMEOUT = 10


def is_mock_running() -> bool:
    """Check if the mock service is running."""
    try:
        response = httpx.get(f"{MOCK_URL}/health", timeout=2.0)
        return response.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module")
def mock_service():
    """Start the mock service if not already running."""
    if is_mock_running():
        yield MOCK_URL
        return

    # Try to start the mock service
    mock_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "mocks", "external-rag-mock", "main.py"
    )
    mock_path = os.path.abspath(mock_path)

    if not os.path.exists(mock_path):
        pytest.skip(f"Mock service not found at {mock_path}")

    process = subprocess.Popen(
        [sys.executable, mock_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for service to start
    start_time = time.time()
    while time.time() - start_time < MOCK_STARTUP_TIMEOUT:
        if is_mock_running():
            break
        time.sleep(0.5)
    else:
        process.terminate()
        pytest.skip("Could not start mock service")

    yield MOCK_URL

    # Cleanup
    process.terminate()
    process.wait(timeout=5)


@pytest.fixture
def client():
    """Create an ExternalRAGClient configured for the mock service."""
    return ExternalRAGClient(
        base_url=MOCK_URL,
        bearer_token=MOCK_TOKEN,
        default_model="test-model",
        top_k=4,
    )


class TestExternalRAGIntegration:
    """Integration tests for ExternalRAGClient with the mock service."""

    @pytest.mark.asyncio
    async def test_discover_data_sources_success(self, mock_service, client):
        """Test discovering data sources for a known user."""
        sources = await client.discover_data_sources("test@test.com")

        assert len(sources) > 0
        # test@test.com has engineering, finance, admin groups
        source_names = [s.name for s in sources]
        assert "engineering-docs" in source_names
        assert "financial-reports" in source_names
        assert "company-wiki" in source_names  # Public

        # Check compliance levels are returned
        for source in sources:
            assert source.compliance_level in ["Internal", "CUI", "Public"]

    @pytest.mark.asyncio
    async def test_discover_data_sources_unknown_user(self, mock_service, client):
        """Test discovering data sources for an unknown user returns empty list."""
        sources = await client.discover_data_sources("unknown@example.com")
        assert sources == []

    @pytest.mark.asyncio
    async def test_discover_data_sources_limited_access(self, mock_service, client):
        """Test that users only see corpora they have access to."""
        # bob@example.com only has sales and marketing groups
        sources = await client.discover_data_sources("bob@example.com")

        source_names = [s.name for s in sources]
        assert "sales-playbook" in source_names
        assert "company-wiki" in source_names  # Public
        # Should NOT have access to engineering or finance corpora
        assert "engineering-docs" not in source_names
        assert "financial-reports" not in source_names

    @pytest.mark.asyncio
    async def test_query_rag_success(self, mock_service, client):
        """Test successful RAG query."""
        messages = [{"role": "user", "content": "What is the API Gateway?"}]

        response = await client.query_rag(
            user_name="test@test.com",
            data_source="engineering-docs",
            messages=messages,
        )

        assert response.content is not None
        assert len(response.content) > 0
        assert "API Gateway" in response.content or "engineering" in response.content.lower()

        # Check metadata
        assert response.metadata is not None
        assert response.metadata.query_processing_time_ms >= 0
        assert response.metadata.data_source_name == "engineering-docs"
        assert response.metadata.retrieval_method == "similarity"
        assert len(response.metadata.documents_found) > 0

    @pytest.mark.asyncio
    async def test_query_rag_with_metadata(self, mock_service, client):
        """Test that RAG query returns document metadata."""
        messages = [{"role": "user", "content": "Tell me about Kubernetes"}]

        response = await client.query_rag(
            user_name="charlie@example.com",  # Has engineering, devops groups
            data_source="kubernetes-runbooks",
            messages=messages,
        )

        assert response.metadata is not None
        assert len(response.metadata.documents_found) > 0

        # Check document metadata structure
        doc = response.metadata.documents_found[0]
        assert doc.source is not None
        assert doc.confidence_score > 0
        assert doc.content_type is not None

    @pytest.mark.asyncio
    async def test_query_rag_access_denied(self, mock_service, client):
        """Test that RAG query returns 403 for unauthorized access."""
        messages = [{"role": "user", "content": "Show me financial data"}]

        with pytest.raises(Exception) as exc_info:
            await client.query_rag(
                user_name="bob@example.com",  # No finance access
                data_source="financial-reports",
                messages=messages,
            )

        # Should raise HTTPException with 403
        assert "403" in str(exc_info.value) or "access" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_query_rag_corpus_not_found(self, mock_service, client):
        """Test that RAG query returns 404 for non-existent corpus."""
        messages = [{"role": "user", "content": "Search something"}]

        with pytest.raises(Exception) as exc_info:
            await client.query_rag(
                user_name="test@test.com",
                data_source="non-existent-corpus",
                messages=messages,
            )

        # Should raise HTTPException with 404
        assert "404" in str(exc_info.value) or "not found" in str(exc_info.value).lower()


class TestExternalRAGAuthFailures:
    """Test authentication failure scenarios."""

    @pytest.mark.asyncio
    async def test_missing_token(self, mock_service):
        """Test that requests without token fail with 401."""
        client = ExternalRAGClient(
            base_url=MOCK_URL,
            bearer_token=None,  # No token
        )

        # Discovery should return empty list on auth failure (graceful degradation)
        sources = await client.discover_data_sources("test@test.com")
        assert sources == []

    @pytest.mark.asyncio
    async def test_invalid_token(self, mock_service):
        """Test that requests with invalid token fail with 401."""
        client = ExternalRAGClient(
            base_url=MOCK_URL,
            bearer_token="invalid-token",
        )

        # Discovery should return empty list on auth failure (graceful degradation)
        sources = await client.discover_data_sources("test@test.com")
        assert sources == []
