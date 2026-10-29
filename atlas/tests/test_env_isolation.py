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

Issue #1070 added the second half of that isolation: variables *exported in
the invoking shell* (a terminal that inherited a deployment's environment)
reach ``AppSettings`` just as a .env would. ``conftest`` scrubs every
settings-derived variable at import time; the tests here lock that scrub in
place too, including an end-to-end subprocess run with the issue's exports
present.
"""

import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest
from dotenv import load_dotenv

from atlas.modules.config.settings import AppSettings


def test_env_file_loading_is_disabled_for_tests():
    """The session-wide guard in conftest must keep .env loading off."""
    assert AppSettings.model_config.get("env_file") is None


def test_skip_authorization_checks_is_disabled_for_tests():
    """The developer-local authorization bypass must not leak into the suite."""
    assert os.environ.get("SKIP_AUTHORIZATION_CHECKS") == "false"


def test_compliance_required_mode_does_not_leak_into_the_suite():
    """The compliance-required policy must not leak into unrelated tests.

    ``conftest`` pins ``FEATURE_COMPLIANCE_LEVELS_ENABLED`` and
    ``FEATURE_COMPLIANCE_LEVEL_REQUIRED`` to an explicit ``false`` and the
    default level to an explicit empty string, because a contributor with those
    exported would otherwise make every chat-service test that omits
    ``compliance_level`` fail with a ``ValidationError``. Compliance-specific
    tests opt back in via ``monkeypatch``, which overrides the session pin.

    Assert the *effective* settings rather than the raw environment: ``main``
    imports ``load_dotenv`` (``override=False``), so the environment can be
    refilled from a developer's .env during collection -- pinning values, not
    popping them, is what makes the effective policy deterministic.
    """
    assert os.environ.get("FEATURE_COMPLIANCE_LEVELS_ENABLED") == "false"
    assert os.environ.get("FEATURE_COMPLIANCE_LEVEL_REQUIRED") == "false"
    settings = AppSettings()
    assert settings.compliance_level_required_effective is False
    assert not settings.compliance_default_level


def test_dotenv_cannot_reenable_compliance_required_mode(tmp_path):
    """A later dotenv load must not re-enable compliance-required mode.

    ``load_dotenv`` with its default ``override=False`` only fills keys that are
    absent, so the conftest pins survive a .env that would otherwise restore the
    policy. Mirrors ``test_dotenv_cannot_reenable_skip_authorization_checks``,
    with ``mock.patch.dict`` wrapping the load so that if the pins ever regress
    the leaked values are restored and do not bury every later test under a
    spurious required-compliance failure.
    """
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text(
        "FEATURE_COMPLIANCE_LEVELS_ENABLED=true\n"
        "FEATURE_COMPLIANCE_LEVEL_REQUIRED=true\n"
        "COMPLIANCE_DEFAULT_LEVEL=Internal\n"
    )

    with mock.patch.dict(os.environ):
        load_dotenv(dotenv_path=dotenv_path)

        assert os.environ.get("FEATURE_COMPLIANCE_LEVELS_ENABLED") == "false"
        assert os.environ.get("FEATURE_COMPLIANCE_LEVEL_REQUIRED") == "false"
        assert os.environ.get("COMPLIANCE_DEFAULT_LEVEL") == ""
        settings = AppSettings()
        assert settings.compliance_level_required_effective is False
        assert not settings.compliance_default_level


def test_dotenv_cannot_reenable_skip_authorization_checks(tmp_path):
    """A later dotenv load must not re-enable the developer-local bypass."""
    dotenv_path = tmp_path / ".env"
    dotenv_path.write_text("SKIP_AUTHORIZATION_CHECKS=true\n")

    with mock.patch.dict(os.environ):
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
    chat-history DuckDB file, the feedback and fine-tune-capture directories,
    and the encrypted MCP token directory. If a redirect is dropped, the
    corresponding store silently goes back to the repository copy, so assert on
    the resolved values rather than trusting the conftest to stay correct.
    """
    project_root = Path(__file__).resolve().parents[2].resolve()

    for var in (
        "CHAT_HISTORY_DB_URL",
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

    A misspelled global is the failure mode this list exists to prevent: a
    fixture that clears a name with no such attribute leaves its "fresh
    per test" comment false and the real singleton carries state across tests.
    Importing the module and asserting on the attribute turns that typo into a
    test failure.
    """
    import importlib

    from conftest import _SINGLETON_GLOBALS  # the suite's own conftest

    missing = []
    for module_name, attr in _SINGLETON_GLOBALS:
        module = importlib.import_module(module_name)
        if not hasattr(module, attr):
            missing.append(f"{module_name}.{attr}")

    assert not missing, f"conftest isolates nonexistent globals: {missing}"


# --- Shell-environment scrub (issue #1070) --------------------------------

# The variables the issue's reproduce block exports, plus representative
# members of each family conftest derives from the AppSettings model.
_DEPLOYMENT_STYLE_VARS = (
    "AUTH_STATIC_GROUPS",
    "ADMIN_USERS",
    "ADMIN_GROUP",
    "ADMIN_TEST_USER",
    "APP_CONFIG_DIR",
    "AUTH_GROUP_CHECK_URL",
    "AUTH_USER_HEADER",
    "OIDC_ISSUER",
    "OIDC_CLIENT_SECRET",
    "FEATURE_HEADER_AUTH_ENABLED",
    "FEATURE_PROXY_SECRET_ENABLED",
    "SKIP_AUTHORIZATION_CHECKS",
    "CHAT_HISTORY_DB_URL",
    "MCP_TOKEN_ENCRYPTION_KEY",
)


def test_scrub_derived_names_cover_deployment_style_variables():
    """The model-derived name set must include the variables #1070 names.

    The scrub list is derived from ``AppSettings.model_fields`` rather than
    hand-maintained, so it cannot drift as settings are added — but the
    derivation itself could regress (e.g. an ``AliasChoices`` variant that
    stops being unwrapped). Assert the named offenders and one representative
    of each alias form are all covered, and that the keep-list stays exactly
    the three variables the test harness deliberately provides.
    """
    from conftest import (  # the suite's own conftest
        _HARNESS_PROVIDED_ENV_VARS,
        _appsettings_env_names,
    )

    names = _appsettings_env_names()
    uncovered = [var for var in _DEPLOYMENT_STYLE_VARS if var not in names]
    assert not uncovered, f"AppSettings env-name derivation misses: {uncovered}"

    assert _HARNESS_PROVIDED_ENV_VARS == {"DEBUG_MODE", "ENVIRONMENT", "MCP_CONFIG_FILE"}


def test_scrub_removes_planted_deployment_variables():
    """Re-running the scrub must remove planted deployment-style variables.

    Runs the conftest guard's helper over a planted environment — including
    the issue's exports and an exported-but-empty harness variable, which
    otherwise fails collection with a pydantic bool-parse error — and asserts
    every planted value is gone while a harness-provided value survives.
    """
    from conftest import _scrub_session_environment  # the suite's own conftest

    planted = {var: f"leaked-{var.lower()}" for var in _DEPLOYMENT_STYLE_VARS}
    planted.update(
        {
            "ATLAS_ENV_FILE": "/srv/atlas/deployment.env",
            "DEBUG_MODE": "",
            "ENVIRONMENT": "",
            "MCP_CONFIG_FILE": "",
        }
    )
    with mock.patch.dict(os.environ, planted):
        _scrub_session_environment()

        for var in _DEPLOYMENT_STYLE_VARS:
            assert os.environ.get(var) is None, f"{var} survived the scrub"
        assert os.environ.get("ATLAS_ENV_FILE") is None
        assert os.environ.get("DEBUG_MODE") is None
        assert os.environ.get("ENVIRONMENT") is None
        assert os.environ.get("MCP_CONFIG_FILE") is None

    # Outside the patch the session's own state is untouched: the harness
    # values CI/run_tests.sh provide are still in place.
    assert os.environ.get("MCP_TOKEN_ENCRYPTION_KEY"), "conftest's test key vanished"
    assert os.environ.get("CHAT_HISTORY_DB_URL"), "conftest's store redirect vanished"


def test_scrub_removes_case_variant_exports():
    """Lowercase/mixed-case exports must not bypass the scrub.

    pydantic-settings matches environment keys case-insensitively (observed
    live: an exported ``auth_static_groups`` was read as
    ``AUTH_STATIC_GROUPS``, and a lowercase ``debug_mode=not-a-bool`` failed
    ``AppSettings()`` the same way an empty upper-case one does), so matching
    only the exact upper-case names leaves a case-variant export live. The
    keep-list, in contrast, stays exact-case: the harness provides
    ``DEBUG_MODE``/``ENVIRONMENT``/``MCP_CONFIG_FILE`` spelled exactly as
    listed, so a case variant of those is a foreign export and must go.
    """
    from conftest import _scrub_session_environment  # the suite's own conftest

    planted = {
        "auth_static_groups": "leakgroup:leak@example.com",
        "Admin_Users": "leakadmin@example.com",
        "app_config_dir": "/srv/deploy-config",
        "aTlAs_eNv_fIlE": "/srv/atlas/deployment.env",
        "chat_history_db_url": "duckdb:///srv/deploy/history.duckdb",
        # Case variants of the keep-listed harness variables, both shapes.
        "debug_mode": "true",
        "Environment": "production",
        "mcp_config_file": "/srv/deploy/mcp.json",
    }
    with mock.patch.dict(os.environ, planted):
        _scrub_session_environment()

        for var in planted:
            assert os.environ.get(var) is None, f"{var} survived the scrub"

    # A harness-provided value in the exact keep-listed spelling survives the
    # same scrub that removes its case-variant shadow.
    with mock.patch.dict(os.environ, {"ENVIRONMENT": "cicd", "Environment": "production"}):
        _scrub_session_environment()

        assert os.environ.get("ENVIRONMENT") == "cicd"
        assert os.environ.get("Environment") is None


def test_scrub_keeps_project_root_pythonpath_entries_and_drops_foreign():
    """PYTHONPATH entries inside the checkout survive; foreign ones do not.

    ``docs/developer/test-isolation.md`` tells contributors to export
    ``$ROOT:$ROOT/scripts`` for the order/parallel runs, so filtering must keep
    those; an entry pointing at another checkout is exactly what must not reach
    test-spawned subprocesses.
    """
    from conftest import _scrub_session_environment  # the suite's own conftest

    project_root = str(Path(__file__).resolve().parents[2])
    foreign = "/opt/some-other-atlas-checkout"
    with mock.patch.dict(
        os.environ,
        {"PYTHONPATH": os.pathsep.join([foreign, project_root, f"{project_root}/scripts"])},
    ):
        _scrub_session_environment()

        entries = os.environ["PYTHONPATH"].split(os.pathsep)
        assert foreign not in entries
        assert str(Path(project_root).resolve()) in entries
        assert str(Path(project_root, "scripts").resolve()) in entries

    with mock.patch.dict(os.environ, {"PYTHONPATH": foreign}):
        _scrub_session_environment()

        assert os.environ.get("PYTHONPATH") is None


def test_deployment_shell_exports_do_not_change_test_results(tmp_path):
    """End-to-end acceptance for #1070: the issue's exports must not matter.

    Runs the two tests the issue observed failing under a child pytest whose
    environment carries the deployment-style exports. The child imports the
    suite's conftest, so this passes only while the scrub is in place —
    removing the guard fails here, with the same assertions the issue reported.

    Two of the exports are planted as lowercase/mixed-case variants:
    pydantic-settings matches environment keys case-insensitively, so those
    reach ``AppSettings`` exactly as the upper-case forms would and the scrub
    must remove them just the same (removing the scrub, or regressing it to
    exact-case matching, fails here).
    """
    deployment_exports = {
        "ADMIN_GROUP": "deploy_admins",
        "APP_CONFIG_DIR": str(tmp_path / "deploy-config"),
        "ATLAS_ENV_FILE": str(tmp_path / "deploy.env"),
        "auth_static_groups": "somegroup:user@example.com",
        "Admin_Users": "deployadmin@example.com",
    }
    env = {**os.environ, **deployment_exports}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/test_core_auth.py::test_admin_users_sugar_targets_the_configured_admin_group",
            "tests/test_core_auth.py::test_warns_when_no_authorization_source_is_configured",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        "the issue's exported variables changed test results; the conftest "
        f"shell-environment scrub regressed:\n{result.stdout}\n{result.stderr}"
    )
    assert "2 passed" in result.stdout


def test_conftest_import_window_guard_is_restored():
    """After conftest import, dotenv must be the real implementation again.

    conftest guards ``load_dotenv`` only while its own imports run (litellm
    loads whatever ``find_dotenv`` walks up to at import time); the guard must
    be lifted before test modules import, so CLI env-file tests and anything
    else exercising dotenv see the real behavior.
    """
    import dotenv
    from conftest import _guarded_load_dotenv  # the suite's own conftest

    assert dotenv.main.load_dotenv is not _guarded_load_dotenv
    assert dotenv.load_dotenv is not _guarded_load_dotenv
    # Not merely "not the guard": the genuine python-dotenv function, so a
    # test that swapped in a stand-in cannot leave it installed unnoticed.
    assert dotenv.load_dotenv is dotenv.main.load_dotenv
    assert dotenv.load_dotenv.__module__ == "dotenv.main"


def test_stale_guard_binding_passes_through_after_the_window(tmp_path):
    """A ``from dotenv import load_dotenv`` binding made during the window is inert.

    Restoring ``dotenv.load_dotenv`` cannot reach a module that copied the
    guarded function into its own namespace while conftest imported
    (``litellm.proxy.proxy_cli`` does), so the guard itself must stop refusing
    once the window has closed.
    """
    from conftest import _guarded_load_dotenv  # the suite's own conftest

    foreign = tmp_path / "foreign.env"
    foreign.write_text("ATLAS_1070_STALE_BINDING_SENTINEL=loaded\n")

    with mock.patch.dict(os.environ):
        os.environ.pop("ATLAS_1070_STALE_BINDING_SENTINEL", None)
        assert _guarded_load_dotenv(dotenv_path=foreign) is True
        assert os.environ.get("ATLAS_1070_STALE_BINDING_SENTINEL") == "loaded"


def test_foreign_dotenv_is_refused_during_the_import_window(tmp_path):
    """A .env outside the checkout must not load while conftest imports.

    This is what litellm's import-time bare ``load_dotenv()`` does when
    ``find_dotenv``'s parent-directory walk leaves the checkout; values loaded
    that way re-enter ``os.environ`` after the scrub (observed live: a foreign
    ``CAPABILITY_TOKEN_SECRET`` failed conftest import outright).
    """
    import dotenv
    from conftest import _block_foreign_dotenv_loads, _restore_dotenv_loads

    poison = tmp_path / "poison.env"
    poison.write_text("AUTH_STATIC_GROUPS=evil:evil@example.com\n")

    with mock.patch.dict(os.environ):
        _block_foreign_dotenv_loads()
        try:
            # Resolve through the module like litellm's import-time call does
            # (``import dotenv as _dotenv; _dotenv.load_dotenv()``).
            assert dotenv.load_dotenv(dotenv_path=poison) is False
            assert os.environ.get("AUTH_STATIC_GROUPS") is None
        finally:
            _restore_dotenv_loads()


def test_checkout_dotenv_still_loads_during_the_import_window(tmp_path, monkeypatch):
    """The checkout's own .env stays load-bearing while conftest imports.

    CI bakes ``/app/.env`` and the app-import chain inside conftest reads
    values like ``DEBUG_MODE`` from it during those imports (``AppSettings``
    itself has env-file loading disabled, so the dotenv refill is the only
    path that reaches settings). Point the guard's checkout root at tmp_path
    and confirm an in-root file still loads while the guard is installed.
    """
    import dotenv
    from conftest import _block_foreign_dotenv_loads, _restore_dotenv_loads

    # Patch via the module object; ``import conftest`` alongside the
    # ``from conftest import`` lines trips CodeQL's mixed-import check.
    conftest = sys.modules["conftest"]
    in_root = tmp_path / ".env"
    in_root.write_text("FEATURE_BANNER_ENABLED=true\n")
    monkeypatch.setattr(conftest, "project_root", tmp_path)

    with mock.patch.dict(os.environ):
        _block_foreign_dotenv_loads()
        try:
            assert dotenv.load_dotenv(dotenv_path=in_root) is True
            assert os.environ.get("FEATURE_BANNER_ENABLED") == "true"
        finally:
            _restore_dotenv_loads()


def _call_bare_load_dotenv_from(caller_file: Path):
    """Run ``dotenv.load_dotenv()`` with no path from code attributed to *caller_file*.

    The call is compiled with that filename, so it looks to python-dotenv
    exactly like litellm's import-time bare call from inside ``site-packages``.
    """
    import dotenv

    code = compile("result = dotenv.load_dotenv()", str(caller_file), "exec")
    namespace = {"dotenv": dotenv}
    exec(code, namespace)
    return namespace["result"]


def test_bare_load_dotenv_resolves_the_checkout_env_from_any_caller(tmp_path, monkeypatch):
    """A no-argument ``load_dotenv()`` resolves the checkout's ``.env``.

    The guard's ``find_dotenv`` walk starts at conftest's directory, not the
    caller's, so a caller outside the checkout (litellm in a venv that lives
    elsewhere) still gets the checkout's own ``.env`` -- which the mock-admin
    tests rely on for ``DEBUG_MODE``. A caller-origin walk would instead pick
    up the caller's nearest ancestor ``.env``; pin the current behavior.

    The real loader is replaced by a recorder, so the test asserts which file
    the guard resolved without depending on what the developer's ``.env``
    contains.
    """
    from conftest import _block_foreign_dotenv_loads, _restore_dotenv_loads

    conftest = sys.modules["conftest"]
    checkout_env = Path(conftest.project_root) / ".env"
    if not checkout_env.is_file():
        pytest.skip("checkout has no .env to resolve")
    foreign = tmp_path / "site-packages" / "lib"
    foreign.mkdir(parents=True)
    (tmp_path / ".env").write_text("ATLAS_1070_FOREIGN_SENTINEL=loaded\n")
    caller_file = foreign / "__init__.py"
    caller_file.write_text("")
    import dotenv

    real_load_dotenv = dotenv.main.load_dotenv
    # ``_restore_dotenv_loads`` reinstalls ``conftest._real_load_dotenv``, which
    # is the recorder below while this test runs -- register the real dotenv
    # attributes with monkeypatch first so its (LIFO) teardown puts them back.
    monkeypatch.setattr(dotenv, "load_dotenv", real_load_dotenv)
    monkeypatch.setattr(dotenv.main, "load_dotenv", real_load_dotenv)
    loaded = []
    monkeypatch.setattr(
        conftest, "_real_load_dotenv", lambda *a, **kw: loaded.append(kw.get("dotenv_path")) or True
    )

    _block_foreign_dotenv_loads()
    try:
        assert _call_bare_load_dotenv_from(caller_file) is True
    finally:
        _restore_dotenv_loads()
    assert [Path(p).resolve() for p in loaded] == [checkout_env.resolve()]


def test_bare_load_dotenv_refuses_an_ancestor_env_outside_the_checkout(tmp_path, monkeypatch):
    """Without a checkout ``.env``, a bare call's walk lands outside and is refused.

    This is the clean-worktree case the guard exists for: the upward walk
    passes the checkout root and finds a foreign ancestor's ``.env``. The walk
    result is pinned to a planted foreign file so the refusal is exercised on
    every machine, whether or not a real ancestor ``.env`` exists.
    """
    from conftest import _block_foreign_dotenv_loads, _restore_dotenv_loads

    conftest = sys.modules["conftest"]
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    foreign_env = tmp_path / ".env"
    foreign_env.write_text("ATLAS_1070_FOREIGN_SENTINEL=loaded\n")
    caller_file = tmp_path / "caller.py"
    caller_file.write_text("")
    monkeypatch.setattr(conftest, "project_root", checkout)
    monkeypatch.setattr(conftest, "_real_find_dotenv", lambda **kw: str(foreign_env))

    with mock.patch.dict(os.environ):
        os.environ.pop("ATLAS_1070_FOREIGN_SENTINEL", None)
        _block_foreign_dotenv_loads()
        try:
            assert _call_bare_load_dotenv_from(caller_file) is False
        finally:
            _restore_dotenv_loads()
        assert os.environ.get("ATLAS_1070_FOREIGN_SENTINEL") is None
