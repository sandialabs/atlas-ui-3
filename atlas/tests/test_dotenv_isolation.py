"""Regression tests for import-time ``.env`` loading in the test suite (issue #1149).

``conftest`` scrubs settings-derived variables from ``os.environ``, but
application imports used to load a ``.env`` straight back in afterwards:
``atlas/main.py`` calls ``load_dotenv("../.env")`` at import, ``litellm`` calls
a bare ``load_dotenv()`` whose upward walk can leave the checkout, and
subprocess tests that import the real app read ``AppSettings``' ``../.env``.
Results then depended on each machine's ``.env`` and on import order.

The harness now sets ``PYTHON_DOTENV_DISABLED=1`` (python-dotenv's own
opt-out) before any import, and Atlas's own ``.env`` reads honor the same
switch through ``atlas.core.dotenv_policy``. These tests pin that down,
including in fresh child processes, which is the only way to observe
collection-time and import-time behavior.
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock

import dotenv
import pytest
from dotenv import load_dotenv

from atlas.core.dotenv_policy import DOTENV_DISABLED_ENV_VAR, dotenv_loading_disabled
from atlas.modules.config.settings import AppSettings

# Recorded at collection: in a child session this tells the parent whether an
# earlier-collected module had already imported ``main`` (and run its
# import-time ``load_dotenv``) before this module was collected. Every test
# body sees ``main`` imported -- conftest's autouse middleware guard imports it
# -- so the collection-time state is the ordering that actually varies.
_MAIN_IMPORTED_AT_COLLECTION = "main" in sys.modules

_TESTS_DIR = Path(__file__).resolve().parent
_ATLAS_DIR = _TESTS_DIR.parent

# A non-settings variable: the scrub never touches it, so if it ever appears in
# a child's environment the only possible source is a ``.env`` file.
_SENTINEL_VAR = "ATLAS_1149_DOTENV_SENTINEL"

# A ``.env`` that would change test results in every way the issue lists: an
# external authorizer, a custom admin group, compliance-required defaults,
# production mode, the dev authorization bypass, and a placeholder secret that
# fails ``AppSettings`` validation outright.
_CONFLICTING_DOTENV = "\n".join(
    [
        f"{_SENTINEL_VAR}=loaded-from-dotenv",
        "AUTH_GROUP_CHECK_URL=http://127.0.0.1:9/never-contact-me",
        "AUTH_GROUP_CHECK_API_KEY=conflicting-dotenv-key",
        "ADMIN_GROUP=dotenv_admins",
        "AUTH_STATIC_GROUPS=dotenv_group:someone@example.com",
        "FEATURE_COMPLIANCE_LEVELS_ENABLED=true",
        "FEATURE_COMPLIANCE_LEVEL_REQUIRED=true",
        "COMPLIANCE_DEFAULT_LEVEL=Internal",
        "DEBUG_MODE=false",
        "SKIP_AUTHORIZATION_CHECKS=true",
        "CAPABILITY_TOKEN_SECRET=placeholder",
        "",
    ]
)


# --- In-process guarantees ------------------------------------------------


def test_dotenv_loading_is_disabled_for_the_session():
    """The harness switch is on, and Atlas's policy reads it the same way."""
    assert os.environ.get(DOTENV_DISABLED_ENV_VAR) == "1"
    assert dotenv_loading_disabled() is True


def test_load_dotenv_is_the_real_implementation():
    """Isolation must not depend on a stand-in installed over python-dotenv."""
    assert dotenv.load_dotenv is dotenv.main.load_dotenv
    assert dotenv.load_dotenv.__module__ == "dotenv.main"


def test_import_time_style_load_cannot_restore_scrubbed_variables(tmp_path):
    """A ``load_dotenv`` like main's or litellm's must not refill the environment."""
    env_file = tmp_path / ".env"
    env_file.write_text(_CONFLICTING_DOTENV)

    with mock.patch.dict(os.environ):
        assert load_dotenv(dotenv_path=env_file) is False
        # The bare, path-less form litellm calls at import.
        assert dotenv.load_dotenv() is False
        assert os.environ.get(_SENTINEL_VAR) is None
        assert os.environ.get("AUTH_GROUP_CHECK_URL") is None
        assert os.environ.get("ADMIN_GROUP") is None


def test_settings_ignore_an_env_file_while_disabled(tmp_path, monkeypatch, caplog):
    """``AppSettings`` drops its dotenv source under the switch, and says so once."""
    from atlas.modules.config import settings as settings_module

    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_GROUP=dotenv_admins\n")
    monkeypatch.delenv("ADMIN_GROUP", raising=False)
    monkeypatch.setitem(settings_module._env_file_skip_state, "warned", False)

    with caplog.at_level("WARNING", logger=settings_module.__name__):
        assert AppSettings(_env_file=str(env_file)).admin_group == "admin"
        assert AppSettings(_env_file=str(env_file)).admin_group == "admin"

    skipped = [r for r in caplog.records if "PYTHON_DOTENV_DISABLED" in r.getMessage()]
    assert len(skipped) == 1
    assert str(env_file) in skipped[0].getMessage()


def test_settings_never_open_the_env_file_while_disabled(tmp_path, monkeypatch):
    """The file is skipped before it is read, so an undecodable one cannot fail construction."""
    env_file = tmp_path / ".env"
    env_file.write_bytes(b"ADMIN_GROUP=\xff\xfe\xfa not utf-8\n")
    monkeypatch.delenv("ADMIN_GROUP", raising=False)

    assert AppSettings(_env_file=str(env_file)).admin_group == "admin"


def test_settings_read_an_env_file_when_dotenv_is_enabled(
    tmp_path, monkeypatch, dotenv_loading_enabled
):
    """Without the switch the dotenv source is back: runtime behavior is unchanged."""
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_GROUP=dotenv_admins\n")
    monkeypatch.delenv("ADMIN_GROUP", raising=False)

    assert AppSettings(_env_file=str(env_file)).admin_group == "dotenv_admins"


def test_process_environment_still_overrides_the_env_file(
    tmp_path, monkeypatch, dotenv_loading_enabled
):
    """Explicit environment settings keep their priority over a ``.env``."""
    env_file = tmp_path / ".env"
    env_file.write_text("ADMIN_GROUP=dotenv_admins\n")
    monkeypatch.setenv("ADMIN_GROUP", "explicit_admins")

    assert AppSettings(_env_file=str(env_file)).admin_group == "explicit_admins"


def test_explicit_monkeypatch_overrides_still_apply(monkeypatch):
    """Tests opt into alternative settings through the environment, as before."""
    monkeypatch.setenv("ADMIN_GROUP", "explicit_admins")
    monkeypatch.setenv("FEATURE_COMPLIANCE_LEVELS_ENABLED", "true")

    settings = AppSettings()

    assert settings.admin_group == "explicit_admins"
    assert settings.feature_compliance_levels_enabled is True


def test_dotenv_loading_enabled_fixture_restores_real_loading(tmp_path, dotenv_loading_enabled):
    """Dotenv-specific tests can still load a file, and stay contained."""
    env_file = tmp_path / ".env"
    env_file.write_text(f"{_SENTINEL_VAR}=loaded\n")

    with mock.patch.dict(os.environ):
        assert load_dotenv(dotenv_path=env_file) is True
        assert os.environ[_SENTINEL_VAR] == "loaded"
    assert os.environ.get(_SENTINEL_VAR) is None


@pytest.mark.parametrize(
    ("value", "disabled"),
    [
        (None, False),
        ("", False),
        ("0", False),
        ("false", False),
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("t", True),
        ("yes", True),
        ("Y", True),
    ],
)
def test_policy_matches_python_dotenv_parsing(monkeypatch, value, disabled):
    """Atlas's reads and ``load_dotenv`` must agree on every value of the switch."""
    if value is None:
        monkeypatch.delenv(DOTENV_DISABLED_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(DOTENV_DISABLED_ENV_VAR, value)

    assert dotenv_loading_disabled() is disabled
    assert dotenv.main._load_dotenv_disabled() is disabled


# --- Fresh-process guarantees ---------------------------------------------


def _effective_configuration() -> dict:
    """Configuration a test sees after the real app has been imported."""
    import main  # noqa: F401  -- the import-time load_dotenv under test

    from atlas.core import auth
    from atlas.modules.config.config_manager import config_manager

    config_manager.reload_configs()
    settings = config_manager.app_settings

    posts = []

    async def _record_post(*args, **kwargs):  # pragma: no cover - must not run
        posts.append(args)
        raise AssertionError("external authorizer contacted")

    with mock.patch("httpx.AsyncClient.post", _record_post):
        is_admin = asyncio.run(auth.is_user_in_group("test@test.com", settings.admin_group))

    return {
        "sentinel": os.environ.get(_SENTINEL_VAR),
        "auth_group_check_url": settings.auth_group_check_url,
        "admin_group": settings.admin_group,
        "auth_static_groups": settings.auth_static_groups,
        "compliance_required": settings.compliance_level_required_effective,
        "compliance_default_level": settings.compliance_default_level,
        "debug_mode": settings.debug_mode,
        "skip_authorization_checks": settings.skip_authorization_checks,
        "external_authorizer_posts": len(posts),
        "is_admin_in_debug_mock": is_admin,
        # Not part of the configuration: lets the parent verify that the
        # "main imported at collection" ordering really happened.
        "main_imported_at_collection": _MAIN_IMPORTED_AT_COLLECTION,
    }


@pytest.mark.skipif(
    not os.environ.get("ATLAS_1149_PROBE_OUT"),
    reason="probe for test_child_sessions_ignore_a_conflicting_dotenv; runs only in its child",
)
def test_probe_effective_configuration():
    """Write the child session's effective configuration for the parent to compare."""
    Path(os.environ["ATLAS_1149_PROBE_OUT"]).write_text(
        json.dumps(_effective_configuration(), sort_keys=True)
    )


def _run_child_session(tmp_path: Path, name: str, *, with_dotenv: bool, order: list) -> dict:
    """Run the probe in a fresh pytest process and return what it observed.

    The child's working directory is ``<root>/work``, so ``main``'s
    ``load_dotenv("../.env")`` and ``AppSettings``' ``env_file="../.env"`` both
    resolve to ``<root>/.env`` -- the conflicting file when ``with_dotenv``.
    ``order`` lists whole test files, collected in that order, so import order
    can be varied (a module that imports ``main`` at collection, before the
    probe, or the probe alone). Files rather than ``file::test`` node ids,
    because pytest does not keep a node id in argument order; ``-k`` then
    deselects every test except the probe (the other files are still
    imported at collection, which is all the ordering needs).
    """
    root = tmp_path / name
    work = root / "work"
    work.mkdir(parents=True)
    if with_dotenv:
        (root / ".env").write_text(_CONFLICTING_DOTENV)
    out = root / "probe.json"

    env = {
        key: value
        for key, value in os.environ.items()
        # A fresh session as a developer would start one: the harness switch
        # and conftest's pins come from conftest itself, not the parent.
        if key not in (DOTENV_DISABLED_ENV_VAR, "DEBUG_MODE", "PYTEST_ADDOPTS")
        and not key.startswith("PYTEST_XDIST")
    }
    env["ATLAS_1149_PROBE_OUT"] = str(out)
    project_root = str(_ATLAS_DIR.parent)
    env["PYTHONPATH"] = os.pathsep.join(
        [project_root] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    )

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "-k",
            "test_probe_effective_configuration",
            *order,
        ],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"child pytest failed:\n{result.stdout}\n{result.stderr}"
    return json.loads(out.read_text())


def test_child_sessions_ignore_a_conflicting_dotenv(tmp_path):
    """Fresh sessions agree with and without a conflicting ``.env``, in any order.

    This is the issue's acceptance check: the conflicting file sits exactly
    where ``main``'s import-time ``load_dotenv("../.env")`` and ``AppSettings``
    look, yet the effective configuration -- including whether the external
    authorizer is contacted -- must match a session with no ``.env`` at all,
    whether the probe runs first or after a module that imports ``main`` at
    collection time.
    """
    probe = str(_TESTS_DIR / "test_dotenv_isolation.py")
    # ``from main import app`` at module level, so main is imported at collection.
    imports_main_first = f"{_TESTS_DIR / 'test_config_shell_endpoint.py'}"

    baseline = _run_child_session(tmp_path, "clean", with_dotenv=False, order=[probe])
    observations = {
        "conflicting .env, probe first": _run_child_session(
            tmp_path, "dotenv-probe-first", with_dotenv=True, order=[probe]
        ),
        "conflicting .env, main imported at collection": _run_child_session(
            tmp_path, "dotenv-main-first", with_dotenv=True, order=[imports_main_first, probe]
        ),
    }

    # The orderings really differ: an earlier-collected module had imported
    # main before this module was collected in one child and not in the others.
    collection_order = {
        label: observed.pop("main_imported_at_collection")
        for label, observed in [("clean", baseline), *observations.items()]
    }
    assert collection_order == {
        "clean": False,
        "conflicting .env, probe first": False,
        "conflicting .env, main imported at collection": True,
    }

    assert baseline["sentinel"] is None
    assert baseline["auth_group_check_url"] is None
    assert baseline["external_authorizer_posts"] == 0
    # The suite's explicit default, not whatever a .env says.
    assert baseline["debug_mode"] is True
    for label, observed in observations.items():
        assert observed == baseline, f"{label}: {observed} != {baseline}"


@pytest.mark.parametrize(
    ("switch", "expected"),
    [
        (None, ["loaded-from-dotenv", "dotenv_admins"]),
        ("1", [None, "admin"]),
    ],
    ids=["switch-unset-reads-dotenv", "switch-set-reads-nothing"],
)
def test_plain_app_import_honors_the_switch(tmp_path, switch, expected):
    """Conftest-free control for the child sessions above.

    A plain interpreter (no pytest, so no conftest and no
    ``model_config["env_file"] = None``) imports ``main`` from the same
    ``<root>/work`` layout. Without the switch it loads the planted
    ``<root>/.env`` into ``os.environ`` and ``AppSettings`` -- proving the file
    sits where the app reads it, so the isolation test above passes because a
    real read is blocked. With the switch, ``main``'s ``load_dotenv`` and the
    ``AppSettings`` hook alone must keep both out; this is the only check that
    fails if the ``AppSettings`` hook regresses.
    """
    root = tmp_path / "control"
    work = root / "work"
    work.mkdir(parents=True)
    (root / ".env").write_text(f"{_SENTINEL_VAR}=loaded-from-dotenv\nADMIN_GROUP=dotenv_admins\n")

    env = {k: v for k, v in os.environ.items() if k not in (DOTENV_DISABLED_ENV_VAR, "ADMIN_GROUP")}
    if switch is not None:
        env[DOTENV_DISABLED_ENV_VAR] = switch
    env["PYTHONPATH"] = os.pathsep.join([str(_ATLAS_DIR.parent), str(_ATLAS_DIR)])
    code = (
        "import json, os, main\n"
        "from atlas.modules.config.config_manager import config_manager\n"
        f"print(json.dumps([os.environ.get({_SENTINEL_VAR!r}), config_manager.app_settings.admin_group]))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=work, env=env, capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == expected
