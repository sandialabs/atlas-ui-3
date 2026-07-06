from unittest.mock import AsyncMock, MagicMock, patch

from main import app
from starlette.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory


def _set_proxy_secret_on_app(secret="test-proxy-secret"):
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "AuthMiddleware":
            middleware.kwargs["proxy_secret"] = secret
            middleware.kwargs["proxy_secret_enabled"] = True
            return
    raise AssertionError("AuthMiddleware not found")


def _headers():
    return {"X-User-Email": "test@test.com", "X-Proxy-Secret": "test-proxy-secret"}


def test_config_endpoint_smoke(monkeypatch):
    _set_proxy_secret_on_app()
    client = TestClient(app)
    resp = client.get("/api/config", headers=_headers())
    assert resp.status_code == 200
    data = resp.json()
    assert "app_name" in data
    assert "models" in data
    assert "tools" in data
    assert "prompts" in data
    assert "data_sources" in data


def test_rag_discovery_skipped_when_feature_disabled(monkeypatch):
    """Verify RAG discovery is not attempted when feature_rag_enabled is False."""
    _set_proxy_secret_on_app()
    mock_unified_rag = MagicMock()
    mock_unified_rag.discover_data_sources = AsyncMock(return_value=[])

    mock_rag_mcp = MagicMock()
    mock_rag_mcp.discover_data_sources = AsyncMock(return_value=[])
    mock_rag_mcp.discover_servers = AsyncMock(return_value=[])

    with patch.object(app_factory, 'get_unified_rag_service', return_value=mock_unified_rag):
        with patch.object(app_factory, 'get_rag_mcp_service', return_value=mock_rag_mcp):
            config_manager = app_factory.get_config_manager()
            original_rag = config_manager.app_settings.feature_rag_enabled
            object.__setattr__(config_manager.app_settings, 'feature_rag_enabled', False)

            try:
                client = TestClient(app)
                resp = client.get("/api/config", headers=_headers())
                assert resp.status_code == 200

                mock_unified_rag.discover_data_sources.assert_not_called()
                mock_rag_mcp.discover_data_sources.assert_not_called()
                mock_rag_mcp.discover_servers.assert_not_called()

                data = resp.json()
                assert data["data_sources"] == []
                assert data["rag_servers"] == []
            finally:
                object.__setattr__(config_manager.app_settings, 'feature_rag_enabled', original_rag)


def test_atlas_rag_pseudo_server_skipped_when_dedicated_flag_disabled(monkeypatch):
    """atlas_rag MCP discovery/tool exposure should require its dedicated flag."""
    _set_proxy_secret_on_app()
    mock_unified_rag = MagicMock()
    mock_unified_rag.discover_data_sources = AsyncMock(return_value=[])

    mock_rag_mcp = MagicMock()
    mock_rag_mcp.discover_servers = AsyncMock(return_value=[{"server": "docsRag", "sources": []}])

    with patch.object(app_factory, 'get_unified_rag_service', return_value=mock_unified_rag):
        with patch.object(app_factory, 'get_rag_mcp_service', return_value=mock_rag_mcp):
            config_manager = app_factory.get_config_manager()
            original_rag = config_manager.app_settings.feature_rag_enabled
            original_atlas = config_manager.app_settings.feature_atlas_rag_tools_enabled
            original_tools = config_manager.app_settings.feature_tools_enabled
            object.__setattr__(config_manager.app_settings, 'feature_rag_enabled', True)
            object.__setattr__(config_manager.app_settings, 'feature_atlas_rag_tools_enabled', False)
            object.__setattr__(config_manager.app_settings, 'feature_tools_enabled', True)

            try:
                client = TestClient(app)
                resp = client.get("/api/config", headers=_headers())
                assert resp.status_code == 200
                data = resp.json()

                mock_unified_rag.discover_data_sources.assert_called_once()
                mock_rag_mcp.discover_servers.assert_not_called()
                assert all(tool.get("server") != "atlas_rag" for tool in data["tools"])
            finally:
                object.__setattr__(config_manager.app_settings, 'feature_rag_enabled', original_rag)
                object.__setattr__(config_manager.app_settings, 'feature_atlas_rag_tools_enabled', original_atlas)
                object.__setattr__(config_manager.app_settings, 'feature_tools_enabled', original_tools)
