import os
import sys
import tempfile
from pathlib import Path

# Ensure the atlas package root is on sys.path for absolute imports like 'infrastructure.*'
atlas_root = Path(__file__).resolve().parents[1]
project_root = atlas_root.parent
if str(atlas_root) not in sys.path:
    sys.path.insert(0, str(atlas_root))
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

# --- Telemetry isolation -------------------------------------------------
# OpenTelemetry's ``trace.set_tracer_provider`` is first-writer-wins: once
# a provider has been registered (e.g. by ``OpenTelemetryConfig`` during
# app import), later calls are silently ignored. Without the two guards
# below, test-emitted spans flow through the app's real
# ``JSONLSpanExporter`` and pollute ``<project_root>/logs/spans.jsonl``
# with fixtures like ``unit.test`` / ``sidecar.off`` and tool-call spans
# containing MagicMock stand-ins (which later crash the analysis script).
#
#   1. Point ``APP_LOG_DIR`` at a throwaway session directory so that if
#      anything DOES initialize the app logging/telemetry config, its
#      file artifacts stay inside the tmpdir and never touch prod logs.
#   2. Pre-install a minimal TracerProvider before any test module or
#      app-code import runs. This locks in the test provider so later
#      ``OpenTelemetryConfig`` calls become no-ops for the duration of
#      the pytest session.
_TELEMETRY_TMPDIR = tempfile.mkdtemp(prefix="atlas-test-telemetry-")
os.environ.setdefault("APP_LOG_DIR", _TELEMETRY_TMPDIR)

# --- .env isolation ------------------------------------------------------
# ``AppSettings`` is configured to load ``../.env`` (see
# ``atlas/modules/config/settings.py``). During tests that file is the
# *developer's* local .env, whose contents must never influence the suite —
# otherwise results depend on each contributor's machine. The concrete bug
# this guards against: a developer with ``MCP_TOKEN_ENCRYPTION_KEY`` set in
# their .env made ``test_refuses_to_start_without_encryption_key`` (which
# deletes the env var, then expects construction to fail) pass locally for
# CI but fail on their machine, because pydantic-settings re-read the key
# from .env after monkeypatch cleared it from ``os.environ``.
#
# Disable env-file loading for the whole test session so AppSettings reads
# only the process environment, which tests fully control via monkeypatch /
# os.environ. This is a test-isolation guard, not a product behavior change.
from atlas.modules.config.settings import AppSettings  # noqa: E402

AppSettings.model_config["env_file"] = None

# MCP token storage now refuses to start without an explicit encryption key
# (previously a per-process ephemeral key was generated, which silently lost
# every stored token on restart). Provide a deterministic test key so module
# initialization and the get_token_storage() singleton work in the test
# environment without leaking a real production secret.
os.environ.setdefault(
    "MCP_TOKEN_ENCRYPTION_KEY",
    "atlas-test-suite-mcp-token-encryption-key-not-a-secret",
)

try:
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider

    _test_provider = TracerProvider(
        resource=Resource.create({"service.name": "atlas-tests"})
    )
    trace.set_tracer_provider(_test_provider)
except Exception:  # pragma: no cover — defensive, tests run without OTel too
    pass

# Pre-import critical modules before any test files can replace them with fakes.
# This prevents test pollution where one test file patches sys.modules and other
# tests import the fake instead of the real module.
# See test_capability_tokens_and_injection.py which patches LiteLLMCaller.
import atlas.modules.llm.litellm_caller  # noqa: E402, F401

# Explicitly reference the module to satisfy static analyzers that flag unused imports.
# The import above is intentional: it pre-populates sys.modules with the real module.
_ = atlas.modules.llm.litellm_caller.LiteLLMCaller  # noqa: E402

# --- Config singleton isolation -----------------------------------------
import pytest  # noqa: E402

from atlas.modules.config.config_manager import config_manager as _config_manager  # noqa: E402

# Every lazily-built config cached on the ConfigManager singleton.
_CONFIG_CACHE_ATTRS = (
    "_app_settings",
    "_llm_config",
    "_mcp_config",
    "_rag_mcp_config",
    "_rag_sources_config",
    "_tool_approvals_config",
    "_file_extractors_config",
)


@pytest.fixture(autouse=True)
def _isolate_config_cache():
    """Restore the ConfigManager singleton's cached config after every test.

    ``config_manager`` is process-wide and ``reload_configs()`` only *clears* its
    cache -- each config is rebuilt lazily on next access. So a test that
    monkeypatches env vars and calls ``reload_configs()`` leaves the singleton
    holding (or about to build) settings derived from that test's environment,
    and every later test in the session silently inherits them.

    This is not hypothetical. ``test_is_user_in_group_debug_admin`` sets
    DEBUG_MODE=true, reloads, then reads ``app_settings`` -- pinning
    ``debug_mode=True`` for the rest of the session. That leak was the only
    reason ~39 admin-route tests passed in the DEBUG_MODE=false CI leg: mock
    admin group membership is debug-only, so those tests were never actually
    exercising production mode. Snapshot and restore so config state cannot
    leak across tests in either direction.
    """
    saved = {attr: getattr(_config_manager, attr) for attr in _CONFIG_CACHE_ATTRS}
    try:
        yield
    finally:
        for attr, value in saved.items():
            setattr(_config_manager, attr, value)


@pytest.fixture
def mock_admin_authorization(monkeypatch):
    """Enable the debug-only mock group table so admin routes are reachable.

    ``core.auth.is_user_in_group`` consults its mock group table (which is what
    makes ``admin_test_user`` / ``test@test.com`` an admin) only when
    ``debug_mode`` is on; with DEBUG_MODE=false no user can be an admin unless an
    external auth endpoint is configured. Admin-route tests assert real 200
    responses, so they need debug mode declared explicitly rather than inherited
    by accident from leaked global state -- see ``_isolate_config_cache``.

    Non-admin identities such as ``user@example.com`` remain non-admin under the
    mock table, so tests asserting 302/403 denial stay meaningful.
    """
    monkeypatch.setenv("DEBUG_MODE", "true")
    _config_manager.reload_configs()
    # Materialize the settings *now*, while DEBUG_MODE is still patched.
    # reload_configs() only clears the cache, so leaving it empty would defer the
    # rebuild until after monkeypatch has restored the real environment.
    settings = _config_manager.app_settings
    assert settings.debug_mode is True
    return settings


# Env vars that the dev-only authorization bypass touches. Tests that need the
# bypass must use ``skip_auth_checks_env`` rather than hand-rolling
# ``monkeypatch.setenv`` + ``reload_configs()``, so that *every* env var the test
# mutates is saved and restored -- not just one -- and the ConfigManager cache
# is reset on the way out. The earlier manual approach only cleared
# ``SKIP_AUTHORIZATION_CHECKS`` in its ``finally`` block, leaving
# ``DEBUG_MODE=true`` patched into ``os.environ`` when ``reload_configs()`` ran,
# which leaked the bypass into later tests (Copilot review on PR #758).
_SKIP_AUTH_ENV_VARS = ("DEBUG_MODE", "SKIP_AUTHORIZATION_CHECKS")


@pytest.fixture
def skip_auth_checks_env():
    """Enable DEBUG_MODE + SKIP_AUTHORIZATION_CHECKS, then fully restore both
    env vars and clear the ConfigManager cache on exit.

    Saves the prior values of *both* env vars, sets them to ``"true"``, reloads
    the config singleton, and materializes ``app_settings`` while the env is
    still patched (``reload_configs()`` only clears the cache; it does not
    rebuild). On teardown both env vars are restored to their saved values
    *before* ``reload_configs()`` runs, so the cleared cache can never be
    rebuilt with the test's values by a later test. The autouse
    ``_isolate_config_cache`` fixture is a second layer of defense that
    snapshots/restores the cache attributes themselves.
    """
    saved = {key: os.environ.get(key) for key in _SKIP_AUTH_ENV_VARS}
    for key in _SKIP_AUTH_ENV_VARS:
        os.environ[key] = "true"
    _config_manager.reload_configs()
    settings = _config_manager.app_settings
    assert settings.debug_mode is True
    assert settings.skip_authorization_checks is True
    try:
        yield settings
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        _config_manager.reload_configs()
