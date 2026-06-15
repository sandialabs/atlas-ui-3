"""Regression tests for developer .env leakage into the test suite.

``AppSettings`` is configured to read ``../.env`` (relative to the process
working directory). If that file were honored during tests, the suite's
results would depend on each contributor's local .env — most visibly,
``test_token_storage.TestRequiresEncryptionKey`` would flip between pass and
fail depending on whether the developer happened to have
``MCP_TOKEN_ENCRYPTION_KEY`` set locally.

``tests/conftest.py`` disables env-file loading for the whole session
(``AppSettings.model_config["env_file"] = None``). These tests lock that
guard in place so it cannot be silently removed.
"""

import os

from atlas.modules.config.settings import AppSettings


def test_env_file_loading_is_disabled_for_tests():
    """The session-wide guard in conftest must keep .env loading off."""
    assert AppSettings.model_config.get("env_file") is None


def test_dotenv_on_disk_does_not_leak_into_settings(tmp_path, monkeypatch):
    """A ``.env`` at the location AppSettings would read must be ignored.

    This reproduces the original leak mechanism end to end: AppSettings'
    configured ``env_file`` is ``../.env`` relative to the working directory,
    so we place a sentinel .env one level above a working dir, chdir into it,
    clear the variable from the process environment, and confirm the on-disk
    value does not reappear.
    """
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    sentinel = "sentinel-dotenv-value-must-not-leak-into-tests"
    (tmp_path / ".env").write_text(f"MCP_TOKEN_ENCRYPTION_KEY={sentinel}\n")

    monkeypatch.delenv("MCP_TOKEN_ENCRYPTION_KEY", raising=False)
    monkeypatch.chdir(work_dir)

    settings = AppSettings()

    assert settings.mcp_token_encryption_key != sentinel
    assert os.environ.get("MCP_TOKEN_ENCRYPTION_KEY") is None
