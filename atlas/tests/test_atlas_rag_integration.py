"""Tests for AtlasRAGClient.

Unit tests mock HTTP responses so no external service is needed.
Integration tests that require the atlas-rag-api-mock are skipped when
the mock service is not available.
"""

import os
import subprocess
import sys
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Add paths for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from atlas.modules.rag.atlas_rag_client import AtlasRAGClient

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
        os.path.dirname(__file__), "..", "..", "mocks", "atlas-rag-api-mock", "main.py"
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
    """Create an AtlasRAGClient configured for the mock service."""
    return AtlasRAGClient(
        base_url=MOCK_URL,
        bearer_token=MOCK_TOKEN,
        default_model="test-model",
        top_k=4,
    )


def _mock_discover_response(sources):
    """Build an httpx.Response for a discover_data_sources call."""
    resp = httpx.Response(
        200,
        json={"data_sources": sources},
        request=httpx.Request("GET", f"{MOCK_URL}/discover/datasources"),
    )
    return resp


class TestDiscoverDataSourcesUnit:
    """Unit tests for discover_data_sources (no external service needed)."""

    @pytest.mark.asyncio
    async def test_discover_data_sources_success(self):
        """Test discovering data sources for a known user."""
        client = AtlasRAGClient(base_url=MOCK_URL, bearer_token=MOCK_TOKEN)
        mock_sources = [
            {"id": "company-policies", "label": "Company Policies", "compliance_level": "Internal", "description": "Internal company policies"},
            {"id": "technical-docs", "label": "Technical Docs", "compliance_level": "Internal", "description": "Technical documentation"},
            {"id": "product-knowledge", "label": "Product Knowledge", "compliance_level": "Public", "description": "Public product info"},
        ]
        mock_resp = _mock_discover_response(mock_sources)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            sources = await client.discover_data_sources("test@test.com")

        assert len(sources) > 0
        source_ids = [s.id for s in sources]
        assert "company-policies" in source_ids
        assert "technical-docs" in source_ids
        assert "product-knowledge" in source_ids

        for source in sources:
            assert source.compliance_level in ["Internal", "Public"]
            assert source.label
            assert source.description

    @pytest.mark.asyncio
    async def test_discover_data_sources_unknown_user(self):
        """Test discovering data sources for an unknown user returns only public sources."""
        client = AtlasRAGClient(base_url=MOCK_URL, bearer_token=MOCK_TOKEN)
        mock_sources = [
            {"id": "product-knowledge", "label": "Product Knowledge", "compliance_level": "Public", "description": "Public product info"},
        ]
        mock_resp = _mock_discover_response(mock_sources)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            sources = await client.discover_data_sources("unknown@example.com")

        source_ids = [s.id for s in sources]
        assert "product-knowledge" in source_ids
        assert "company-policies" not in source_ids
        assert "technical-docs" not in source_ids

    @pytest.mark.asyncio
    async def test_discover_data_sources_limited_access(self):
        """Test that users only see corpora they have access to."""
        client = AtlasRAGClient(base_url=MOCK_URL, bearer_token=MOCK_TOKEN)
        mock_sources = [
            {"id": "company-policies", "label": "Company Policies", "compliance_level": "Internal", "description": "Internal company policies"},
            {"id": "product-knowledge", "label": "Product Knowledge", "compliance_level": "Public", "description": "Public product info"},
        ]
        mock_resp = _mock_discover_response(mock_sources)

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            sources = await client.discover_data_sources("bob@example.com")

        source_ids = [s.id for s in sources]
        assert "company-policies" in source_ids
        assert "product-knowledge" in source_ids
        assert "technical-docs" not in source_ids

    @pytest.mark.asyncio
    async def test_discover_connection_error_returns_empty(self):
        """Test that connection errors return empty list gracefully."""
        client = AtlasRAGClient(base_url="http://localhost:99999", bearer_token=MOCK_TOKEN)
        sources = await client.discover_data_sources("test@test.com")
        assert sources == []

    @pytest.mark.asyncio
    async def test_discover_http_error_returns_empty(self):
        """Test that HTTP errors return empty list gracefully."""
        client = AtlasRAGClient(base_url=MOCK_URL, bearer_token=MOCK_TOKEN)
        mock_resp = httpx.Response(
            401,
            json={"detail": "Unauthorized"},
            request=httpx.Request("GET", f"{MOCK_URL}/discover/datasources"),
        )

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            sources = await client.discover_data_sources("test@test.com")

        assert sources == []


@pytest.mark.skipif(
    not os.environ.get("RUN_RAG_INTEGRATION"),
    reason="Requires atlas-rag-api-mock service; set RUN_RAG_INTEGRATION=1 to run",
)
class TestAtlasRAGIntegration:
    """Integration tests that require the atlas-rag-api-mock service.

    Skipped by default. Run with: RUN_RAG_INTEGRATION=1 pytest ...
    """

    @pytest.mark.asyncio
    async def test_discover_data_sources_success(self, mock_service, client):
        """Test discovering data sources for a known user against live mock."""
        sources = await client.discover_data_sources("test@test.com")

        assert len(sources) > 0
        source_ids = [s.id for s in sources]
        assert "company-policies" in source_ids
        assert "technical-docs" in source_ids
        assert "product-knowledge" in source_ids

        for source in sources:
            assert source.compliance_level in ["Internal", "Public"]
            assert source.label
            assert source.description

    @pytest.mark.asyncio
    async def test_discover_data_sources_unknown_user(self, mock_service, client):
        """Test discovering data sources for an unknown user against live mock."""
        sources = await client.discover_data_sources("unknown@example.com")
        source_ids = [s.id for s in sources]
        assert "product-knowledge" in source_ids
        assert "company-policies" not in source_ids
        assert "technical-docs" not in source_ids

    @pytest.mark.asyncio
    async def test_discover_data_sources_limited_access(self, mock_service, client):
        """Test limited access against live mock."""
        sources = await client.discover_data_sources("bob@example.com")

        source_ids = [s.id for s in sources]
        assert "company-policies" in source_ids  # requires employee
        assert "product-knowledge" in source_ids  # Public
        # Should NOT have access to technical-docs (requires engineering or devops)
        assert "technical-docs" not in source_ids

    @pytest.mark.asyncio
    async def test_query_rag_success(self, mock_service, client):
        """Test successful RAG query."""
        messages = [{"role": "user", "content": "What is the API authentication?"}]

        response = await client.query_rag(
            user_name="test@test.com",
            data_source="technical-docs",
            messages=messages,
        )

        assert response.content is not None
        assert len(response.content) > 0
        # Should contain information about API or authentication
        assert "API" in response.content or "authentication" in response.content.lower()

        # Check metadata
        assert response.metadata is not None
        assert response.metadata.query_processing_time_ms >= 0
        assert response.metadata.data_source_name == "technical-docs"
        assert response.metadata.retrieval_method == "keyword-search"
        assert len(response.metadata.documents_found) > 0

    @pytest.mark.asyncio
    async def test_query_rag_with_metadata(self, mock_service, client):
        """Test that RAG query returns document metadata."""
        messages = [{"role": "user", "content": "Tell me about deployment pipeline"}]

        response = await client.query_rag(
            user_name="charlie@example.com",  # Has employee, engineering, devops groups
            data_source="technical-docs",
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
        messages = [{"role": "user", "content": "Show me technical docs"}]

        with pytest.raises(Exception) as exc_info:
            await client.query_rag(
                user_name="bob@example.com",  # Has employee, sales - no engineering/devops
                data_source="technical-docs",  # Requires engineering or devops
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


@pytest.mark.skipif(
    not os.environ.get("RUN_RAG_INTEGRATION"),
    reason="Requires atlas-rag-api-mock service; set RUN_RAG_INTEGRATION=1 to run",
)
class TestAtlasRAGAuthFailures:
    """Test authentication failure scenarios (requires live mock)."""

    @pytest.mark.asyncio
    async def test_missing_token(self, mock_service):
        """Test that requests without token fail with 401."""
        client = AtlasRAGClient(
            base_url=MOCK_URL,
            bearer_token=None,  # No token
        )

        # Discovery should return empty list on auth failure (graceful degradation)
        sources = await client.discover_data_sources("test@test.com")
        assert sources == []

    @pytest.mark.asyncio
    async def test_invalid_token(self, mock_service):
        """Test that requests with invalid token fail with 401."""
        client = AtlasRAGClient(
            base_url=MOCK_URL,
            bearer_token="invalid-token",
        )

        # Discovery should return empty list on auth failure (graceful degradation)
        sources = await client.discover_data_sources("test@test.com")
        assert sources == []
