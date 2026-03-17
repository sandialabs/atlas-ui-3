from pathlib import Path

import pytest

from atlas.core.runtime_path_validation import ensure_duckdb_parent_writable, ensure_writable_directory


def test_ensure_writable_directory_creates_directory(tmp_path):
    target = tmp_path / "logs"

    resolved = ensure_writable_directory(
        target,
        setting_name="APP_LOG_DIR",
        purpose="Application logging",
    )

    assert resolved == target
    assert target.is_dir()


def test_ensure_writable_directory_surfaces_permission_guidance(tmp_path, monkeypatch):
    target = tmp_path / "logs"

    def _raise_permission_error(*args, **kwargs):
        raise PermissionError("Permission denied")

    monkeypatch.setattr(Path, "mkdir", _raise_permission_error)

    with pytest.raises(RuntimeError) as exc_info:
        ensure_writable_directory(
            target,
            setting_name="APP_LOG_DIR",
            purpose="Application logging",
        )

    message = str(exc_info.value)
    assert "APP_LOG_DIR" in message
    assert "bind mount or PVC" in message


def test_ensure_duckdb_parent_writable_uses_project_relative_parent(tmp_path, monkeypatch):
    captured: dict[str, Path | str] = {}

    def _capture(directory: Path, *, setting_name: str, purpose: str) -> Path:
        captured["directory"] = directory
        captured["setting_name"] = setting_name
        captured["purpose"] = purpose
        return directory

    monkeypatch.setattr("atlas.core.runtime_path_validation.ensure_writable_directory", _capture)

    ensure_duckdb_parent_writable("duckdb:///data/chat_history.db", project_root=tmp_path)

    assert captured["directory"] == tmp_path / "data"
    assert captured["setting_name"] == "CHAT_HISTORY_DB_URL"
    assert "chat_history.db" in str(captured["purpose"])
