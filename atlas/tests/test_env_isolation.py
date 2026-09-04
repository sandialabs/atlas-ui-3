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
from pathlib import Path

from dotenv import load_dotenv

from atlas.modules.config.settings import AppSettings


def test_env_file_loading_is_disabled_for_tests():
    """The session-wide guard in conftest must keep .env loading off."""
    assert AppSettings.model_config.get("env_file") is None


def test_skip_authorization_checks_is_disabled_for_tests():
    """The developer-local authorization bypass must not leak into the suite."""
    assert os.environ.get("SKIP_AUTHORIZATION_CHECKS") == "false"


def test_dotenv_cannot_reenable_skip_authorization_checks(tmp_path):
    """A later dotenv load must not re-enable the developer-local bypass."""
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("SKIP_AUTHORIZATION_CHECKS=true\n")

    load_dotenv(dotenv_path=dotenv_path)

    assert os.environ.get("SKIP_AUTHORIZATION_CHECKS") == "false"


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


def test_persistent_stores_are_redirected_off_the_repository():
    """Every store conftest redirects must resolve outside the checkout.

    These are the stores the suite used to write into for real: the developer's
    chat-history and agent-portal DuckDB files, the agent-portal audit log, the
    tool-approval audit log, the feedback and fine-tune-capture directories,
    and the encrypted MCP token directory. If a redirect is dropped, the
    corresponding store silently goes back to the repository copy, so assert on
    the resolved values rather than trusting the conftest to stay correct.
    """
    project_root = Path(__file__).resolve().parents[2].resolve()

    for var in (
        "CHAT_HISTORY_DB_URL",
        "AGENT_PORTAL_DB_URL",
        "AGENT_PORTAL_AUDIT_PATH",
        "TOOL_CALL_AUDIT_PATH",
        "RUNTIME_FEEDBACK_DIR",
        "RUNTIME_CAPTURE_DIR",
        "MCP_TOKEN_STORAGE_DIR",
        "APP_LOG_DIR",
    ):
        value = os.environ.get(var)
        assert value, f"{var} must be redirected for the test session"

        if "://" in value and not value.startswith("duckdb:///"):
            # A server-backed URL (``postgresql://...``) has no local path to
            # contain; ``_resolve_db_url`` only rewrites the file-backed form.
            continue

        # ``resolve()`` before comparing: an unresolved path hides both a
        # ``..`` component and a TMPDIR that symlinks back into the checkout,
        # which is precisely what this assertion exists to catch.
        path = Path(value.replace("duckdb:///", "")).resolve()
        assert path.is_absolute(), f"{var}={value!r} must be an absolute path"
        assert project_root not in path.parents and path != project_root, (
            f"{var}={value!r} resolves to {path}, inside the repository; the "
            "suite would read and write real developer state"
        )


def test_isolated_singletons_name_real_module_globals():
    """Each entry in ``_SINGLETON_GLOBALS`` must name an attribute that exists.

    A misspelled global is the failure mode this list exists to prevent: the
    agent-portal e2e fixture cleared ``_singleton_manager`` (no such attribute)
    for months, so its "fresh process manager per test" comment was false and
    the real singleton carried processes across tests. Importing the module and
    asserting on the attribute turns that typo into a test failure.
    """
    import importlib

    from conftest import _SINGLETON_GLOBALS  # the suite's own conftest

    missing = []
    for module_name, attr in _SINGLETON_GLOBALS:
        module = importlib.import_module(module_name)
        if not hasattr(module, attr):
            missing.append(f"{module_name}.{attr}")

    assert not missing, f"conftest isolates nonexistent globals: {missing}"
