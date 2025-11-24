from unittest.mock import patch, MagicMock, AsyncMock

from starlette.testclient import TestClient

from main import app


def test_config_endpoint_smoke(monkeypatch):
    client = TestClient(app)
    resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})
    # Endpoint should not crash; tolerate 200 with minimal fields
    assert resp.status_code == 200
    data = resp.json()
    assert "app_name" in data
    assert "models" in data
    assert "tools" in data
    assert "prompts" in data
    assert "data_sources" in data


def test_rag_discovery_skipped_when_feature_disabled(monkeypatch):
    """Verify RAG discovery is not attempted when feature_rag_enabled is False."""
    # Create mock rag_client to track if discover_data_sources is called
    mock_rag_client = MagicMock()
    mock_rag_client.discover_data_sources = AsyncMock(return_value=[])

    # Create mock rag_mcp_service
    mock_rag_mcp = MagicMock()
    mock_rag_mcp.discover_data_sources = AsyncMock(return_value=[])
    mock_rag_mcp.discover_servers = AsyncMock(return_value=[])

    from infrastructure.app_factory import app_factory
    
    # Patch the app_factory methods
    with patch.object(app_factory, 'get_rag_client', return_value=mock_rag_client):
        with patch.object(app_factory, 'get_rag_mcp_service', return_value=mock_rag_mcp):
            # Ensure RAG feature is disabled
            config_manager = app_factory.get_config_manager()
            original_setting = config_manager.app_settings.feature_rag_enabled
            config_manager.app_settings.feature_rag_enabled = False

            try:
                client = TestClient(app)
                resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})
                assert resp.status_code == 200
                
                # Verify RAG discovery was NOT called when feature is disabled
                mock_rag_client.discover_data_sources.assert_not_called()
                mock_rag_mcp.discover_data_sources.assert_not_called()
                mock_rag_mcp.discover_servers.assert_not_called()
                
                # Verify response still has data_sources field (just empty)
                data = resp.json()
                assert "data_sources" in data
                assert data["data_sources"] == []
                assert "rag_servers" in data
                assert data["rag_servers"] == []
            finally:
                # Restore original setting
                config_manager.app_settings.feature_rag_enabled = original_setting
