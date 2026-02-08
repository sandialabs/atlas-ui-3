"""Tests for file extraction config exposure in /api/config endpoint."""

from unittest.mock import patch

from main import app
from starlette.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config.config_manager import FileExtractorConfig, FileExtractorsConfig


def test_config_endpoint_includes_file_extraction_feature_flag():
    """Config endpoint should include file_content_extraction in features."""
    client = TestClient(app)
    resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})

    assert resp.status_code == 200
    data = resp.json()
    assert "features" in data
    assert "file_content_extraction" in data["features"]
    assert isinstance(data["features"]["file_content_extraction"], bool)


def test_config_endpoint_includes_file_extraction_config():
    """Config endpoint should include file_extraction configuration."""
    client = TestClient(app)
    resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})

    assert resp.status_code == 200
    data = resp.json()
    assert "file_extraction" in data
    file_extraction = data["file_extraction"]
    assert "enabled" in file_extraction
    assert "default_behavior" in file_extraction
    assert "supported_extensions" in file_extraction


def test_file_extraction_disabled_when_feature_flag_off():
    """File extraction should be disabled when feature flag is off."""
    config_manager = app_factory.get_config_manager()
    original_setting = config_manager.app_settings.feature_file_content_extraction_enabled

    try:
        config_manager.app_settings.feature_file_content_extraction_enabled = False

        client = TestClient(app)
        resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["features"]["file_content_extraction"] is False
        assert data["file_extraction"]["enabled"] is False
        assert data["file_extraction"]["default_behavior"] == "none"
        assert data["file_extraction"]["supported_extensions"] == []
    finally:
        config_manager.app_settings.feature_file_content_extraction_enabled = original_setting


def test_file_extraction_enabled_with_correct_extensions():
    """File extraction should show supported extensions when enabled."""
    config_manager = app_factory.get_config_manager()
    original_feature = config_manager.app_settings.feature_file_content_extraction_enabled
    original_extractors = config_manager._file_extractors_config

    try:
        # Enable feature and set up test config
        config_manager.app_settings.feature_file_content_extraction_enabled = True
        config_manager._file_extractors_config = FileExtractorsConfig(
            enabled=True,
            default_behavior="extract",
            extractors={
                "pdf-text": FileExtractorConfig(url="http://localhost/pdf", enabled=True),
                "image-vision": FileExtractorConfig(url="http://localhost/img", enabled=False)
            },
            extension_mapping={
                ".pdf": "pdf-text",
                ".png": "image-vision"
            }
        )

        client = TestClient(app)
        resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})

        assert resp.status_code == 200
        data = resp.json()
        assert data["features"]["file_content_extraction"] is True
        assert data["file_extraction"]["enabled"] is True
        assert data["file_extraction"]["default_behavior"] == "full"
        # Only .pdf should be in supported_extensions since image-vision is disabled
        assert ".pdf" in data["file_extraction"]["supported_extensions"]
        assert ".png" not in data["file_extraction"]["supported_extensions"]
    finally:
        config_manager.app_settings.feature_file_content_extraction_enabled = original_feature
        config_manager._file_extractors_config = original_extractors


def test_file_extraction_handles_config_errors_gracefully():
    """File extraction config should handle errors gracefully."""
    config_manager = app_factory.get_config_manager()
    original_feature = config_manager.app_settings.feature_file_content_extraction_enabled
    original_extractors = config_manager._file_extractors_config

    try:
        config_manager.app_settings.feature_file_content_extraction_enabled = True

        # Force an error by setting invalid config that will cause exception
        config_manager._file_extractors_config = None

        # Patch the property to raise an exception
        with patch.object(
            type(config_manager),
            'file_extractors_config',
            property(lambda self: (_ for _ in ()).throw(Exception("Config error")))
        ):
            client = TestClient(app)
            resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})

            assert resp.status_code == 200
            data = resp.json()
            # Should return safe defaults on error
            assert data["file_extraction"]["enabled"] is False
            assert data["file_extraction"]["default_behavior"] == "none"
            assert data["file_extraction"]["supported_extensions"] == []
    finally:
        config_manager.app_settings.feature_file_content_extraction_enabled = original_feature
        config_manager._file_extractors_config = original_extractors


def test_file_extraction_extensions_sorted():
    """File extraction supported extensions should be sorted."""
    config_manager = app_factory.get_config_manager()
    original_feature = config_manager.app_settings.feature_file_content_extraction_enabled
    original_extractors = config_manager._file_extractors_config

    try:
        config_manager.app_settings.feature_file_content_extraction_enabled = True
        config_manager._file_extractors_config = FileExtractorsConfig(
            enabled=True,
            extractors={
                "pdf-text": FileExtractorConfig(url="http://localhost/pdf", enabled=True)
            },
            extension_mapping={
                ".pdf": "pdf-text",
                ".doc": "pdf-text",
                ".txt": "pdf-text"
            }
        )

        client = TestClient(app)
        resp = client.get("/api/config", headers={"X-User-Email": "test@test.com"})

        assert resp.status_code == 200
        data = resp.json()
        extensions = data["file_extraction"]["supported_extensions"]
        assert extensions == sorted(extensions)
    finally:
        config_manager.app_settings.feature_file_content_extraction_enabled = original_feature
        config_manager._file_extractors_config = original_extractors
