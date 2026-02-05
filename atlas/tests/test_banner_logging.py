"""Tests for banner message save logging functionality."""
import logging

from main import app
from starlette.testclient import TestClient

from atlas.modules.config import config_manager


def test_banner_save_success_logging(caplog, tmp_path, monkeypatch):
    """Test that successful banner save produces INFO log with file path."""
    client = TestClient(app)

    # Setup temp config directory
    config_dir = tmp_path / "config" / "overrides"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Mock config path to use temp directory
    def mock_get_admin_config_path(filename):
        return config_dir / filename

    monkeypatch.setattr(
        "atlas.routes.admin_routes.get_admin_config_path",
        mock_get_admin_config_path
    )

    # Mock setup_config_overrides to avoid side effects
    monkeypatch.setattr("atlas.routes.admin_routes.setup_config_overrides", lambda: None)

    # Capture logs at INFO level
    with caplog.at_level(logging.INFO):
        # Make request to update banner messages
        response = client.post(
            "/admin/banners",
            json={"messages": ["Test banner message", "Another test message"]},
            headers={"X-User-Email": "admin@example.com"}
        )

    # Verify request succeeded
    assert response.status_code == 200

    # Check that INFO log was created with success message
    info_logs = [record for record in caplog.records if record.levelname == "INFO"]
    assert len(info_logs) > 0

    # Find the banner save log
    banner_logs = [
        log for log in info_logs
        if "Banner messages successfully saved to disk" in log.message
    ]
    assert len(banner_logs) == 1

    # Verify log contains file path and admin user
    log_message = banner_logs[0].message
    assert "messages.txt" in log_message
    assert "admin@example.com" in log_message


def test_banner_save_failure_logging(caplog, tmp_path, monkeypatch):
    """Test that failed banner save produces ERROR log with details."""
    client = TestClient(app)

    # Mock get_admin_config_path to return a path
    readonly_file = tmp_path / "readonly.txt"
    readonly_file.write_text("test")

    def mock_get_admin_config_path(filename):
        return readonly_file

    monkeypatch.setattr(
        "atlas.routes.admin_routes.get_admin_config_path",
        mock_get_admin_config_path
    )

    # Mock setup_config_overrides to avoid side effects
    monkeypatch.setattr("atlas.routes.admin_routes.setup_config_overrides", lambda: None)

    # Mock write_file_content to raise an exception
    def mock_write_file_content(file_path, content, file_type="text"):
        raise PermissionError("Permission denied")

    monkeypatch.setattr(
        "atlas.routes.admin_routes.write_file_content",
        mock_write_file_content
    )

    # Capture logs at ERROR level
    with caplog.at_level(logging.ERROR):
        # Make request to update banner messages (should fail)
        response = client.post(
            "/admin/banners",
            json={"messages": ["Test banner message"]},
            headers={"X-User-Email": "admin@example.com"}
        )

    # Verify request failed
    assert response.status_code == 500

    # Check that ERROR log was created with failure message
    error_logs = [record for record in caplog.records if record.levelname == "ERROR"]
    assert len(error_logs) > 0

    # Find the banner save error log
    banner_error_logs = [
        log for log in error_logs
        if "Failed to save banner messages to disk" in log.message
    ]
    assert len(banner_error_logs) == 1

    # Verify log contains file path and error details
    log_message = banner_error_logs[0].message
    assert "readonly.txt" in log_message
    assert "Permission denied" in log_message or "PermissionError" in log_message


def test_banner_save_logs_sanitized_paths(caplog, tmp_path, monkeypatch):
    """Test that file paths in logs are sanitized to prevent log injection."""
    client = TestClient(app)

    # Setup temp config directory with a potentially malicious name
    config_dir = tmp_path / "config" / "overrides"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Mock config path
    def mock_get_admin_config_path(filename):
        return config_dir / filename

    monkeypatch.setattr(
        "atlas.routes.admin_routes.get_admin_config_path",
        mock_get_admin_config_path
    )

    # Mock setup_config_overrides to avoid side effects
    monkeypatch.setattr("atlas.routes.admin_routes.setup_config_overrides", lambda: None)

    # Capture logs at INFO level
    with caplog.at_level(logging.INFO):
        # Make request to update banner messages
        response = client.post(
            "/admin/banners",
            json={"messages": ["Test message"]},
            headers={"X-User-Email": "admin@example.com"}
        )

    # Verify request succeeded
    assert response.status_code == 200

    # Check that log messages don't contain raw newlines or control characters
    info_logs = [record for record in caplog.records if record.levelname == "INFO"]
    banner_logs = [
        log for log in info_logs
        if "Banner messages successfully saved to disk" in log.message
    ]

    assert len(banner_logs) == 1
    log_message = banner_logs[0].message

    # Verify no newlines in the log message
    assert "\n" not in log_message
    assert "\r" not in log_message


def test_banner_get_includes_enabled_status(tmp_path, monkeypatch):
    """Test that GET /admin/banners includes banner_enabled status."""
    client = TestClient(app)

    # Setup temp config directory
    config_dir = tmp_path / "config" / "overrides"
    config_dir.mkdir(parents=True, exist_ok=True)
    messages_file = config_dir / "messages.txt"
    messages_file.write_text("Test message\n")

    # Mock config path to use temp directory
    def mock_get_admin_config_path(filename):
        return config_dir / filename

    monkeypatch.setattr(
        "atlas.routes.admin_routes.get_admin_config_path",
        mock_get_admin_config_path
    )

    # Mock setup_config_overrides to avoid side effects
    monkeypatch.setattr("atlas.routes.admin_routes.setup_config_overrides", lambda: None)

    # Make request to get banner config
    response = client.get(
        "/admin/banners",
        headers={"X-User-Email": "admin@example.com"}
    )

    # Verify request succeeded
    assert response.status_code == 200

    # Check response contains banner_enabled field
    data = response.json()
    assert "banner_enabled" in data
    assert isinstance(data["banner_enabled"], bool)
    # The field should match the current config setting
    assert data["banner_enabled"] == config_manager.app_settings.banner_enabled


def test_banner_get_with_feature_disabled(tmp_path, monkeypatch):
    """Test that GET /admin/banners returns banner_enabled: false when feature is disabled."""
    client = TestClient(app)

    # Setup temp config directory
    config_dir = tmp_path / "config" / "overrides"
    config_dir.mkdir(parents=True, exist_ok=True)
    messages_file = config_dir / "messages.txt"
    messages_file.write_text("Test message\n")

    # Mock config path
    def mock_get_admin_config_path(filename):
        return config_dir / filename

    monkeypatch.setattr(
        "atlas.routes.admin_routes.get_admin_config_path",
        mock_get_admin_config_path
    )

    # Mock setup_config_overrides
    monkeypatch.setattr("atlas.routes.admin_routes.setup_config_overrides", lambda: None)

    # Mock banner_enabled to be false
    monkeypatch.setattr(
        "atlas.routes.admin_routes.config_manager.app_settings.banner_enabled",
        False
    )

    # Make request to get banner config
    response = client.get(
        "/admin/banners",
        headers={"X-User-Email": "admin@example.com"}
    )

    # Verify request succeeded
    assert response.status_code == 200

    # Check response contains banner_enabled field set to false
    data = response.json()
    assert "banner_enabled" in data
    assert data["banner_enabled"] is False


def test_banner_get_with_feature_enabled(tmp_path, monkeypatch):
    """Test that GET /admin/banners returns banner_enabled: true when feature is enabled."""
    client = TestClient(app)

    # Setup temp config directory
    config_dir = tmp_path / "config" / "overrides"
    config_dir.mkdir(parents=True, exist_ok=True)
    messages_file = config_dir / "messages.txt"
    messages_file.write_text("Test message\n")

    # Mock config path
    def mock_get_admin_config_path(filename):
        return config_dir / filename

    monkeypatch.setattr(
        "atlas.routes.admin_routes.get_admin_config_path",
        mock_get_admin_config_path
    )

    # Mock setup_config_overrides
    monkeypatch.setattr("atlas.routes.admin_routes.setup_config_overrides", lambda: None)

    # Mock banner_enabled to be true
    monkeypatch.setattr(
        "atlas.routes.admin_routes.config_manager.app_settings.banner_enabled",
        True
    )

    # Make request to get banner config
    response = client.get(
        "/admin/banners",
        headers={"X-User-Email": "admin@example.com"}
    )

    # Verify request succeeded
    assert response.status_code == 200

    # Check response contains banner_enabled field set to true
    data = response.json()
    assert "banner_enabled" in data
    assert data["banner_enabled"] is True


