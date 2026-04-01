"""Tests for the /api/config/shell fast config endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch

from main import app
from starlette.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory


def test_config_shell_endpoint_returns_200():
    """Shell endpoint should return 200 with required fields."""
    client = TestClient(app)
    resp = client.get("/api/config/shell", headers={"X-User-Email": "test@test.com"})
    assert resp.status_code == 200
    data = resp.json()

    # Must include fast UI shell fields
    assert "app_name" in data
    assert "models" in data
    assert "user" in data
    assert "features" in data
    assert "agent_mode_available" in data
    assert "is_in_admin_group" in data
    assert "file_extraction" in data


def test_config_shell_does_not_include_slow_fields():
    """Shell endpoint must NOT include tools, prompts, or RAG data."""
    client = TestClient(app)
    resp = client.get("/api/config/shell", headers={"X-User-Email": "test@test.com"})
    assert resp.status_code == 200
    data = resp.json()

    # These fields require slow MCP/RAG discovery - must be absent
    assert "tools" not in data
    assert "prompts" not in data
    assert "data_sources" not in data
    assert "rag_servers" not in data
    assert "authorized_servers" not in data
    assert "tool_approvals" not in data


def test_config_shell_does_not_call_mcp_discovery():
    """Shell endpoint must not trigger MCP tool/prompt discovery."""
    mock_mcp_manager = MagicMock()
    mock_mcp_manager.get_authorized_servers = AsyncMock(return_value=[])

    with patch.object(app_factory, 'get_mcp_manager', return_value=mock_mcp_manager):
        client = TestClient(app)
        resp = client.get("/api/config/shell", headers={"X-User-Email": "test@test.com"})
        assert resp.status_code == 200

        # MCP discovery should NOT be called
        mock_mcp_manager.get_authorized_servers.assert_not_called()


def test_config_shell_does_not_call_rag_discovery():
    """Shell endpoint must not trigger RAG source discovery."""
    mock_unified_rag = MagicMock()
    mock_unified_rag.discover_data_sources = AsyncMock(return_value=[])

    mock_rag_mcp = MagicMock()
    mock_rag_mcp.discover_servers = AsyncMock(return_value=[])

    with patch.object(app_factory, 'get_unified_rag_service', return_value=mock_unified_rag):
        with patch.object(app_factory, 'get_rag_mcp_service', return_value=mock_rag_mcp):
            client = TestClient(app)
            resp = client.get("/api/config/shell", headers={"X-User-Email": "test@test.com"})
            assert resp.status_code == 200

            mock_unified_rag.discover_data_sources.assert_not_called()
            mock_rag_mcp.discover_servers.assert_not_called()


def test_config_shell_includes_feature_flags():
    """Shell endpoint should include all feature flags."""
    client = TestClient(app)
    resp = client.get("/api/config/shell", headers={"X-User-Email": "test@test.com"})
    data = resp.json()
    features = data["features"]

    expected_keys = [
        "workspaces", "rag", "tools", "marketplace", "files_panel",
        "chat_history", "compliance_levels", "splash_screen",
        "file_content_extraction", "globus_auth"
    ]
    for key in expected_keys:
        assert key in features, f"Missing feature flag: {key}"


def test_config_shell_models_have_names():
    """Shell endpoint should return models with name fields."""
    client = TestClient(app)
    resp = client.get("/api/config/shell", headers={"X-User-Email": "test@test.com"})
    data = resp.json()

    for model in data["models"]:
        assert "name" in model
        assert "description" in model


def test_config_shell_user_matches_header():
    """Shell endpoint should return the authenticated user."""
    client = TestClient(app)
    resp = client.get("/api/config/shell", headers={"X-User-Email": "alice@example.com"})
    data = resp.json()
    assert data["user"] == "alice@example.com"


def test_config_shell_feature_flags_match_full_config():
    """Feature flags from /api/config/shell should match /api/config."""
    client = TestClient(app)
    headers = {"X-User-Email": "test@test.com"}

    shell_resp = client.get("/api/config/shell", headers=headers)
    full_resp = client.get("/api/config", headers=headers)

    shell_features = shell_resp.json()["features"]
    full_features = full_resp.json()["features"]

    # Every feature in shell should match full config
    for key, value in shell_features.items():
        assert key in full_features, f"Shell has feature '{key}' not in full config"
        assert value == full_features[key], (
            f"Feature '{key}' mismatch: shell={value}, full={full_features[key]}"
        )


def test_config_shell_includes_capability_fields_when_set():
    """Shell endpoint should include capability metadata fields when configured on a model."""
    from atlas.modules.config.config_manager import ModelConfig, LLMConfig

    test_model = ModelConfig(
        model_name="test-model",
        model_url="http://localhost:8080",
        description="Test model with capabilities",
        supports_vision=True,
        supports_tools=True,
        supports_reasoning=False,
        context_window=128000,
        model_card_url="https://example.com/model-card",
    )
    test_llm_config = LLMConfig(models={"test-model": test_model})

    config_manager = app_factory.get_config_manager()

    with patch.object(config_manager, '_llm_config', test_llm_config):
        client = TestClient(app)
        resp = client.get("/api/config/shell", headers={"X-User-Email": "test@test.com"})
        assert resp.status_code == 200
        data = resp.json()

        model = data["models"][0]
        assert model["name"] == "test-model"
        assert model["supports_vision"] is True
        assert model["supports_tools"] is True
        assert model["supports_reasoning"] is False
        assert model["context_window"] == 128000
        assert model["model_card_url"] == "https://example.com/model-card"


def test_config_shell_omits_capability_fields_when_none():
    """Shell endpoint should NOT include capability fields when they are None."""
    from atlas.modules.config.config_manager import ModelConfig, LLMConfig

    test_model = ModelConfig(
        model_name="basic-model",
        model_url="http://localhost:8080",
        description="Basic model without capabilities",
    )
    test_llm_config = LLMConfig(models={"basic-model": test_model})

    config_manager = app_factory.get_config_manager()

    with patch.object(config_manager, '_llm_config', test_llm_config):
        client = TestClient(app)
        resp = client.get("/api/config/shell", headers={"X-User-Email": "test@test.com"})
        assert resp.status_code == 200
        data = resp.json()

        model = data["models"][0]
        assert model["name"] == "basic-model"
        assert model["supports_vision"] is False  # bool field, always present
        assert "supports_tools" not in model
        assert "supports_reasoning" not in model
        assert "context_window" not in model
        assert "model_card_url" not in model
