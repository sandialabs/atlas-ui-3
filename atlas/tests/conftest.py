import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

from sqlalchemy.engine import Engine

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
# Forced, not ``setdefault``: docs/getting-started/installation.md tells
# contributors to set APP_LOG_DIR to a path *inside* the checkout, so honoring
# an exported value is exactly the case that writes test telemetry and
# security-risk records into the repository's logs/.
os.environ["APP_LOG_DIR"] = _TELEMETRY_TMPDIR

# --- Persistent-store isolation ------------------------------------------
# Several stores resolve their location relative to the repository root and
# are opened as a side effect of *importing* the app: ``AppFactory()`` is
# constructed at import time in ``atlas/infrastructure/app_factory.py`` and
# calls ``get_session_factory()``, so merely importing ``main`` (which ~30
# test modules do) binds the process-wide chat-history engine to the real
# ``<project_root>/data/chat_history.db``. Measured consequences before this
# guard: ``test_security_header_injection`` inserted a real conversation row
# on every run (318 accumulated rows, all from test identities), the
# agent-portal e2e tests appended 1181 audit rows to ``data/agent_portal.db``
# plus ``data/agent_portal_audit.jsonl``, and a feedback route test dropped a
# JSON file into ``runtime/feedback/`` each run.
#
# That is developer-data corruption on its own, and it also makes any
# emptiness/count assertion depend on what previous tests -- and previous
# *runs* -- happened to write. Redirect every such store into a throwaway
# session directory. These are forced (not ``setdefault``) so an exported
# shell value cannot re-point the suite at a real database; tests that need a
# specific value still override them with ``monkeypatch``.
_STATE_TMPDIR = tempfile.mkdtemp(prefix="atlas-test-state-")
os.environ["CHAT_HISTORY_DB_URL"] = f"duckdb:///{_STATE_TMPDIR}/chat_history.db"
os.environ["AGENT_PORTAL_DB_URL"] = f"duckdb:///{_STATE_TMPDIR}/agent_portal.db"
os.environ["AGENT_PORTAL_AUDIT_PATH"] = f"{_STATE_TMPDIR}/agent_portal_audit.jsonl"
os.environ["RUNTIME_FEEDBACK_DIR"] = f"{_STATE_TMPDIR}/feedback"
os.environ["RUNTIME_CAPTURE_DIR"] = f"{_STATE_TMPDIR}/finetune_capture"
os.environ["MCP_TOKEN_STORAGE_DIR"] = f"{_STATE_TMPDIR}/tokens"

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

# --- External authorizer isolation ---------------------------------------
# ``core.auth.is_user_in_group`` prefers a configured external authorization
# service over its local group logic: when AUTH_GROUP_CHECK_URL and
# AUTH_GROUP_CHECK_API_KEY are both set, *every* membership decision becomes an
# outbound HTTPS POST. That is exactly the runtime behaviour we want in a real
# deployment, and exactly what must not leak into the suite -- a contributor or
# CI runner with those vars exported turns ~50 admin/authorization tests into
# calls against a live authorization service (or, more usually, into 403s and
# connection errors), for reasons that have nothing to do with their change.
#
# Clearing them for the session makes the local branch deterministic. Tests that
# specifically exercise the external path (see ``test_core_auth.py``) set both
# vars via monkeypatch, which takes precedence over this session-level clear, so
# the production path keeps real coverage. This is a test-isolation guard, not a
# product behavior change -- runtime authorization is untouched.
for _authorizer_var in ("AUTH_GROUP_CHECK_URL", "AUTH_GROUP_CHECK_API_KEY"):
    os.environ.pop(_authorizer_var, None)

# --- Authorization-bypass isolation --------------------------------------
# ``SKIP_AUTHORIZATION_CHECKS=true`` is a developer-local escape hatch that makes
# *every* authenticated user a member of *every* group, including admin. When a
# contributor has it exported in their shell, admin-route tests that expect a
# non-admin identity to be rejected silently become meaningless and fail with
# 200s/400s instead of 403s. Pin it off for the test session rather than removing
# it: ``atlas.main`` loads the repository .env during collection, and dotenv
# would otherwise restore a developer-local true value. Tests can still opt into
# the bypass via ``skip_auth_checks_env``.
os.environ["SKIP_AUTHORIZATION_CHECKS"] = "false"

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

# Pre-import the LiteLLM caller so the real module is the one every later
# import binds to. ``test_capability_tokens_and_injection`` used to install a
# fake in ``sys.modules`` at import time; anything imported while that fake was
# installed kept it for the rest of the session, which broke 86 tests across
# five files whenever collection order put the fake first. The fake is gone --
# tests that need a stand-in patch the attribute instead -- and this pre-import
# stays as a cheap guard against the pattern coming back.
import atlas.modules.llm.litellm_caller  # noqa: E402, F401

# Explicitly reference the module to satisfy static analyzers that flag unused imports.
# The import above is intentional: it pre-populates sys.modules with the real module.
_ = atlas.modules.llm.litellm_caller.LiteLLMCaller  # noqa: E402

# --- Config singleton isolation -----------------------------------------
import pytest  # noqa: E402

from atlas.modules.config.config_manager import config_manager as _config_manager  # noqa: E402

# ConfigManager caches each lazily-built config on a private (underscore-prefixed)
# instance attribute. Rather than hand-maintain a list that silently drifts when a
# new cached config is added, snapshot every private attribute off the singleton
# so an eighth (or ninth ...) config can't accidentally escape isolation.


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
    saved = {
        attr: value
        for attr, value in vars(_config_manager).items()
        if attr.startswith("_")
    }
    try:
        yield
    finally:
        for attr, value in saved.items():
            setattr(_config_manager, attr, value)


# Process-wide singletons that app code memoizes in a module global. Tests that
# pin one (a temp-backed PortalStore, a HookManager, a ProcessManager) or that
# merely touch a lazy getter leave it populated for every later test, which is a
# silent channel between tests: a leaked ProcessManager carries its whole
# process table forward, and a leaked HookManager makes later turns fire hooks
# they never asked for. Both were observed in the suite before this fixture.
#
# Snapshot-and-restore rather than reset-to-None: restoring the prior value is
# correct whether the module global was empty or already legitimately populated.
_SINGLETON_GLOBALS = (
    ("atlas.modules.chat_history.database", "_engine"),
    ("atlas.modules.chat_history.database", "_session_factory"),
    ("atlas.modules.process_manager.manager", "_singleton"),
    ("atlas.modules.process_manager.manager", "_idle_sweeper_task"),
    ("atlas.modules.agent_portal.portal_store", "_singleton"),
    ("atlas.modules.agent_portal.presets_store", "_singleton"),
    ("atlas.modules.agent_portal.database", "_engine"),
    ("atlas.modules.agent_portal.database", "_session_factory"),
    ("atlas.modules.agent_portal.audit_log", "_resolved_path"),
    ("atlas.modules.mcp_tools.token_storage", "_token_storage"),
    ("atlas.modules.mcp_tools.wormhole_token_store", "_wormhole_store"),
    ("atlas.hooks.manager", "_hook_manager"),
    ("atlas.core.compliance", "_compliance_manager"),
    ("atlas.application.chat.approval_manager", "_approval_manager"),
    ("atlas.application.chat.elicitation_manager", "_elicitation_manager"),
    ("atlas.modules.file_storage.content_extractor", "_extractor_instance"),
    ("atlas.routes.telemetry_routes", "_reader_override"),
)


def _release(value) -> None:
    """Best-effort release of a resource-owning singleton we are about to drop.

    Restoring a global by plain assignment would otherwise strand whatever the
    test left there: a SQLAlchemy engine keeps its pooled DuckDB connection
    until garbage collection, and a sweeper task keeps running.

    Dispatch is by *type*, never by method name. ``ProcessManager.cancel`` is
    ``async def cancel(self, process_id, *, sigkill_after=3.0)`` -- a
    name-based ``value.cancel()`` would raise TypeError, and the surrounding
    ``except`` would hide it. Types not listed here are simply dropped, which
    is what restoring the previous value already did.
    """
    try:
        if isinstance(value, Engine):
            value.dispose()
        elif isinstance(value, asyncio.Task) and not value.done():
            # Best effort: the task's loop is usually already closed by the
            # time a test finishes, so this marks it cancelled without being
            # able to unwind the coroutine. Nothing here can await it.
            value.cancel()
    except Exception:  # pragma: no cover - teardown must not raise
        pass


@pytest.fixture(autouse=True)
def _isolate_module_singletons():
    """Restore app-level singleton module globals after every test.

    Only modules already imported are touched, so this never forces an import
    just to isolate it.
    """
    saved = []
    for module_name, attr in _SINGLETON_GLOBALS:
        module = sys.modules.get(module_name)
        if module is None:
            # Not imported yet. If the test imports it, the global it leaves
            # behind was created by that test, so the pristine value to restore
            # is the module's declared default -- ``None`` for every entry in
            # ``_SINGLETON_GLOBALS`` (they are all lazily-populated caches).
            # Without this branch a subset run leaks any singleton whose module
            # is first imported inside a test.
            saved.append((module_name, attr, None))
            continue
        if not hasattr(module, attr):
            continue
        saved.append((module_name, attr, getattr(module, attr)))
    try:
        yield
    finally:
        for module_name, attr, value in saved:
            module = sys.modules.get(module_name)
            if module is None:
                continue
            current = getattr(module, attr, None)
            if current is not value:
                _release(current)
            setattr(module, attr, value)


# Groups the debug-only mock table in ``core.auth`` grants to the *non-admin*
# baseline identity ``user@example.com`` (plus ``users``, which is granted to
# literally everyone before the table is even consulted).
_NON_ADMIN_BASELINE_GROUPS = frozenset({"users", "mcp_basic"})


@pytest.fixture
def distinct_admin_group():
    """Return the configured admin group, skipping if it is not actually exclusive.

    Allow/deny pairs that tag a resource with ``ADMIN_GROUP`` and then assert a
    non-admin identity is refused only mean something when the configured admin
    group is one the denial identity lacks. Point ``ADMIN_GROUP`` at ``users``
    (which ``core.auth`` grants to everyone unconditionally) or ``mcp_basic``,
    and the resource becomes reachable by the very identity the test expects to
    be turned away -- the assertion inverts and the suite reports a failure that
    says nothing about the code under test.

    Such a deployment has no working admin gate at all, so this is a broken
    configuration rather than a case worth supporting. Skip with the reason
    stated plainly instead of failing (a spurious red) or silently passing
    (a test that no longer tests anything).
    """
    admin_group = _config_manager.app_settings.admin_group
    if admin_group in _NON_ADMIN_BASELINE_GROUPS:
        pytest.skip(
            f"ADMIN_GROUP={admin_group!r} is a group every non-admin identity "
            "already has, so admin allow/deny pairs cannot be distinguished."
        )
    return admin_group


@pytest.fixture
def mock_admin_authorization(monkeypatch):
    """Enable the debug-only mock group table so admin routes are reachable.

    ``core.auth.is_user_in_group`` consults its mock group table (which is what
    makes the configured ``admin_test_user`` / ``test_user`` an admin) only when
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


@pytest.fixture
def test_user():
    """Return the configured non-admin test identity."""
    return _config_manager.app_settings.test_user


@pytest.fixture
def admin_test_user():
    """Return the configured admin test identity."""
    return _config_manager.app_settings.admin_test_user


@pytest.fixture
def admin_group():
    """Return the configured admin group name (the group admin-route checks test against)."""
    return _config_manager.app_settings.admin_group


@pytest.fixture
def test_user_headers(test_user):
    """Return auth headers for the configured non-admin test identity."""
    return {"X-User-Email": test_user}


@pytest.fixture
def admin_test_user_headers(admin_test_user):
    """Return auth headers for the configured admin test identity."""
    return {"X-User-Email": admin_test_user}


# Env vars that the dev-only authorization bypass touches. Tests that need the
# bypass must use ``skip_auth_checks_env`` rather than hand-rolling
# ``monkeypatch.setenv`` + ``reload_configs()``, so that *every* env var the test
# mutates is saved and restored -- not just one -- and the ConfigManager cache
# is reset on the way out. The earlier manual approach only cleared
# ``SKIP_AUTHORIZATION_CHECKS`` in its ``finally`` block, leaving
# ``DEBUG_MODE=true`` patched into ``os.environ`` when ``reload_configs()`` ran,
# which leaked the bypass into later tests (Copilot review on PR #758).
#
# ``ENVIRONMENT`` is included because the validator refuses the flag when
# ``ENVIRONMENT=production``; the fixture pins it to ``development`` so the
# bypass can actually take effect, then restores the prior value on teardown.
_SKIP_AUTH_ENV_VARS = ("DEBUG_MODE", "SKIP_AUTHORIZATION_CHECKS", "ENVIRONMENT")


@pytest.fixture
def skip_auth_checks_env():
    """Enable DEBUG_MODE + SKIP_AUTHORIZATION_CHECKS, then fully restore both
    env vars and clear the ConfigManager cache on exit.

    Saves the prior values of *all* bypass-relevant env vars, sets them to
    dev-mode values (``DEBUG_MODE=true``, ``SKIP_AUTHORIZATION_CHECKS=true``,
    ``ENVIRONMENT=development``), reloads the config singleton, and materializes
    ``app_settings`` while the env is still patched (``reload_configs()`` only
    clears the cache; it does not rebuild). On teardown every env var is
    restored to its saved value *before* ``reload_configs()`` runs, so the
    cleared cache can never be rebuilt with the test's values by a later test.
    The autouse ``_isolate_config_cache`` fixture is a second layer of defense
    that snapshots/restores the cache attributes themselves.

    All setup work -- env mutation, reload, and the materialization asserts --
    lives inside the ``try`` block so a failure during setup still triggers the
    ``finally`` teardown, never leaving the bypass half-armed for the rest of
    the session (AGENT-REVIEW-BOT-3 review on PR #758).
    """
    saved = {key: os.environ.get(key) for key in _SKIP_AUTH_ENV_VARS}
    try:
        os.environ["DEBUG_MODE"] = "true"
        os.environ["SKIP_AUTHORIZATION_CHECKS"] = "true"
        os.environ["ENVIRONMENT"] = "development"
        _config_manager.reload_configs()
        settings = _config_manager.app_settings
        assert settings.debug_mode is True
        assert settings.skip_authorization_checks is True
        yield settings
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
        _config_manager.reload_configs()


def pytest_sessionfinish(session, exitstatus):
    """Remove the session's temp directories.

    ``mkdtemp`` has to run at import time -- the redirects above must be in
    place before any test module imports app code -- so pytest's own
    ``tmp_path_factory`` is not available yet, and the cleanup pytest would
    normally do for us is not either. Without this every run orphans two
    directories under the system temp dir.
    """
    for path in (_STATE_TMPDIR, _TELEMETRY_TMPDIR):
        shutil.rmtree(path, ignore_errors=True)
