"""Debug bind and capability-secret fail-closed regression coverage."""

import asyncio
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from atlas.core.security_config import (
    _CAPABILITY_SECRET_PLACEHOLDERS,
    resolve_bind_host,
    validate_capability_secret,
    validate_debug_configuration,
)
from atlas.modules.config.settings import AppSettings


def _settings(**overrides):
    values = {
        "debug_mode": True,
        "environment": "development",
        "allow_debug_non_loopback": False,
        "allow_debug_production": False,
        "capability_token_secret": "a" * 32,
        "websocket_keepalive_interval_seconds": 20,
    }
    return SimpleNamespace(**(values | overrides))


@pytest.mark.parametrize("host", ["127.0.0.1", "127.10.0.1", "::1", "localhost"])
def test_local_development_debug_warns(host, caplog):
    validate_debug_configuration(_settings(), host)
    assert "DEBUG_MODE=true bypasses authentication" in caplog.text


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.2", "atlas.example.com"])
def test_nonloopback_debug_rejected(host, caplog):
    with pytest.raises(ValueError, match="ALLOW_DEBUG_NON_LOOPBACK"):
        validate_debug_configuration(_settings(), host)
    assert "DEBUG_MODE=true bypasses authentication" in caplog.text


@pytest.mark.parametrize("environment", ["production", "Production", " PRODUCTION "])
def test_production_debug_rejected_even_on_loopback(environment):
    with pytest.raises(ValueError, match="DEBUG_MODE"):
        validate_debug_configuration(_settings(environment=environment), "127.0.0.1")


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0", "::"])
@pytest.mark.parametrize("environment", ["production", "development"])
def test_explicit_override_allows_debug_with_warning(host, environment, caplog):
    validate_debug_configuration(
        _settings(
            environment=environment,
            allow_debug_non_loopback=True,
            allow_debug_production=True,
        ),
        host,
    )
    assert "bypasses authentication" in caplog.text


@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_production_refusal_is_independent_of_non_loopback_override(host):
    # ALLOW_DEBUG_NON_LOOPBACK must not silently permit a production debug bind.
    with pytest.raises(ValueError, match="ALLOW_DEBUG_PRODUCTION"):
        validate_debug_configuration(
            _settings(environment="production", allow_debug_non_loopback=True), host
        )


def test_debug_disabled_allows_public_production_without_debug_warning(caplog):
    validate_debug_configuration(
        _settings(debug_mode=False, environment="production"), "0.0.0.0"
    )
    assert "DEBUG_MODE=true" not in caplog.text


def test_override_is_off_by_default_and_loads_from_environment(monkeypatch):
    monkeypatch.delenv("ALLOW_DEBUG_NON_LOOPBACK", raising=False)
    assert not AppSettings(_env_file=None).allow_debug_non_loopback
    monkeypatch.setenv("ALLOW_DEBUG_NON_LOOPBACK", "true")
    assert AppSettings(_env_file=None).allow_debug_non_loopback


def test_production_override_is_off_by_default_and_loads_from_environment(monkeypatch):
    monkeypatch.delenv("ALLOW_DEBUG_PRODUCTION", raising=False)
    assert not AppSettings(_env_file=None).allow_debug_production
    monkeypatch.setenv("ALLOW_DEBUG_PRODUCTION", "true")
    assert AppSettings(_env_file=None).allow_debug_production


@pytest.mark.parametrize(
    "secret",
    [*_CAPABILITY_SECRET_PLACEHOLDERS, "x", "x" * 31, " " * 40, " " * 32 + "short"],
)
def test_unsafe_capability_secret_rejected_by_settings_and_resolution(secret, monkeypatch):
    from atlas.core import capabilities

    with pytest.raises(ValueError, match="CAPABILITY_TOKEN_SECRET"):
        AppSettings(_env_file=None, capability_token_secret=secret)
    monkeypatch.setattr(
        capabilities, "config_manager",
        SimpleNamespace(app_settings=_settings(capability_token_secret=secret)),
    )
    for operation in (
        capabilities._get_secret,
        lambda: capabilities.generate_file_token("user@example.com", "key"),
        lambda: capabilities.verify_file_token("invalid.token"),
    ):
        with pytest.raises(ValueError, match="CAPABILITY_TOKEN_SECRET"):
            operation()


@pytest.mark.parametrize("secret", ["", "x" * 32, "\u00e9" * 16])
def test_valid_secret_boundary_and_empty_ephemeral_option(secret):
    assert validate_capability_secret(secret) == secret
    assert AppSettings(_env_file=None, capability_token_secret=secret).capability_token_secret == secret


def test_configuration_error_does_not_fall_back_to_ephemeral(monkeypatch):
    from atlas.core import capabilities

    class BrokenConfig:
        @property
        def app_settings(self):
            raise ValueError("invalid configuration")

    monkeypatch.setattr(capabilities, "config_manager", BrokenConfig())
    fallback = Mock()
    monkeypatch.setattr(capabilities, "_get_ephemeral_secret", fallback)
    with pytest.raises(ValueError, match="invalid configuration"):
        capabilities._get_secret()
    fallback.assert_not_called()


@pytest.mark.parametrize(
    "settings, host, message",
    [
        (_settings(environment="production"), "127.0.0.1", "DEBUG_MODE"),
        (_settings(), "0.0.0.0", "DEBUG_MODE"),
        (_settings(debug_mode=False, capability_token_secret="short"), "127.0.0.1",
         "CAPABILITY_TOKEN_SECRET"),
    ],
)
def test_lifespan_rejects_unsafe_config_before_initialization(monkeypatch, settings, host, message):
    from atlas import main as atlas_main

    monkeypatch.setenv("ATLAS_HOST", host)
    monkeypatch.setattr(
        atlas_main.app_factory, "get_config_manager",
        lambda: SimpleNamespace(app_settings=settings),
    )
    mcp = Mock(side_effect=AssertionError("MCP initialization reached"))
    monkeypatch.setattr(atlas_main.app_factory, "get_mcp_manager", mcp)

    async def enter():
        async with atlas_main.lifespan(atlas_main.app):
            pytest.fail("Unsafe startup succeeded")

    with pytest.raises(ValueError, match=message):
        asyncio.run(enter())
    mcp.assert_not_called()


@pytest.mark.parametrize("reload", [False, True])
def test_server_cli_checks_effective_host_not_environment(monkeypatch, reload):
    import uvicorn

    from atlas import server_cli
    from atlas.modules.config import config_manager

    monkeypatch.setenv("ATLAS_HOST", "127.0.0.1")
    monkeypatch.setitem(sys.modules, "atlas.main", SimpleNamespace(app=object()))
    monkeypatch.setattr(type(config_manager), "app_settings", property(lambda self: _settings()))
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)
    args = server_cli.build_parser().parse_args(
        ["--host", "0.0.0.0"] + (["--reload"] if reload else [])
    )
    with pytest.raises(ValueError, match="DEBUG_MODE"):
        server_cli.run_server(args)
    run.assert_not_called()
    assert os.environ["ATLAS_HOST"] == "0.0.0.0"


def test_server_cli_local_host_overrides_nonloopback_environment(monkeypatch):
    import uvicorn

    from atlas import server_cli
    from atlas.modules.config import config_manager

    monkeypatch.setenv("ATLAS_HOST", "0.0.0.0")
    monkeypatch.setitem(sys.modules, "atlas.main", SimpleNamespace(app=object()))
    monkeypatch.setattr(type(config_manager), "app_settings", property(lambda self: _settings()))
    run = Mock()
    monkeypatch.setattr(uvicorn, "run", run)
    args = server_cli.build_parser().parse_args(["--host", "127.0.0.1"])
    assert server_cli.run_server(args) == 0
    assert run.call_args.kwargs["host"] == "127.0.0.1"
    assert os.environ["ATLAS_HOST"] == "127.0.0.1"


@pytest.mark.parametrize(
    "environ, argv, expected",
    [
        ({"ATLAS_HOST": "0.0.0.0"}, ["uvicorn", "main:app"], "0.0.0.0"),
        ({"UVICORN_HOST": "::"}, ["uvicorn", "main:app"], "::"),
        ({}, ["uvicorn", "main:app", "--host", "0.0.0.0"], "0.0.0.0"),
        ({}, ["uvicorn", "main:app", "--host=0.0.0.0"], "0.0.0.0"),
        ({}, ["uvicorn", "main:app"], None),
        # The last --host wins, and a CLI flag overrides a loopback env value.
        ({}, ["uvicorn", "main:app", "--host", "127.0.0.1", "--host", "0.0.0.0"],
         "0.0.0.0"),
        ({"ATLAS_HOST": "127.0.0.1", "UVICORN_HOST": "127.0.0.1"},
         ["uvicorn", "main:app", "--host", "0.0.0.0"], "0.0.0.0"),
        # Gunicorn/Hypercorn bind flags are recognized too (:port is allowed).
        ({"ATLAS_HOST": "127.0.0.1"},
         ["gunicorn", "-b", "0.0.0.0:8000", "main:app"], "0.0.0.0:8000"),
        # Every bind hint is checked: a loopback value cannot mask a public one.
        ({}, ["gunicorn", "-b", "0.0.0.0:8000", "-b", "127.0.0.1:8001", "main:app"],
         "0.0.0.0:8000"),
        # Attached -bHOST:PORT form.
        ({}, ["gunicorn", "-b0.0.0.0:8000", "main:app"], "0.0.0.0:8000"),
        # All loopback hints pass and the last is reported.
        ({"ATLAS_HOST": "127.0.0.1"},
         ["uvicorn", "main:app", "--host", "127.0.0.1"], "127.0.0.1"),
    ],
)
def test_resolve_bind_host_sees_env_and_uvicorn_flags(monkeypatch, environ, argv, expected):
    for name in ("ATLAS_HOST", "UVICORN_HOST"):
        monkeypatch.delenv(name, raising=False)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(sys, "argv", argv)
    assert resolve_bind_host() == expected


def test_unknown_bind_host_fails_closed(monkeypatch):
    for name in ("ATLAS_HOST", "UVICORN_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "atlas.main:app"])
    assert resolve_bind_host() is None
    with pytest.raises(ValueError, match="ALLOW_DEBUG_NON_LOOPBACK"):
        validate_debug_configuration(_settings(), resolve_bind_host())


def test_gunicorn_public_bind_cannot_bypass_debug_guard(monkeypatch):
    # ATLAS_HOST=127.0.0.1 in .env must not mask an actual public bind.
    monkeypatch.setenv("ATLAS_HOST", "127.0.0.1")
    monkeypatch.delenv("UVICORN_HOST", raising=False)
    monkeypatch.setattr(sys, "argv", ["gunicorn", "-b", "0.0.0.0:8000", "main:app"])
    with pytest.raises(ValueError, match="ALLOW_DEBUG_NON_LOOPBACK"):
        validate_debug_configuration(_settings(), resolve_bind_host())


def test_multiple_bind_flags_must_all_be_loopback(monkeypatch):
    # A trailing loopback bind must not mask an earlier public one.
    for name in ("ATLAS_HOST", "UVICORN_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        sys, "argv",
        ["gunicorn", "-b", "0.0.0.0:8000", "-b", "127.0.0.1:8001", "main:app"],
    )
    with pytest.raises(ValueError, match="ALLOW_DEBUG_NON_LOOPBACK"):
        validate_debug_configuration(_settings(), resolve_bind_host())


def test_direct_uvicorn_public_bind_cannot_bypass_debug_guard(monkeypatch):
    # `uvicorn main:app --host 0.0.0.0` sets neither ATLAS_HOST nor the app's
    # own CLI host, which previously let the lifespan default to 127.0.0.1.
    for name in ("ATLAS_HOST", "UVICORN_HOST"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["uvicorn", "atlas.main:app", "--host", "0.0.0.0"])
    with pytest.raises(ValueError, match="DEBUG_MODE"):
        validate_debug_configuration(_settings(), resolve_bind_host())


@pytest.mark.parametrize(
    "environment, host, override, prod_override, succeeds",
    [
        ("production", "127.0.0.1", "false", "false", False),
        ("development", "0.0.0.0", "false", "false", False),
        ("development", "127.0.0.1", "false", "false", True),
        ("production", "0.0.0.0", "true", "false", False),
        ("production", "0.0.0.0", "true", "true", True),
    ],
)
def test_main_entry_checks_bind_before_uvicorn(environment, host, override, prod_override, succeeds):
    env = os.environ | {
        "DEBUG_MODE": "true",
        "ENVIRONMENT": environment,
        "ATLAS_HOST": host,
        "ALLOW_DEBUG_NON_LOOPBACK": override,
        "ALLOW_DEBUG_PRODUCTION": prod_override,
        "CAPABILITY_TOKEN_SECRET": "x" * 32,
        "SKIP_AUTHORIZATION_CHECKS": "false",
    }
    script = (
        "import runpy, uvicorn; "
        "uvicorn.run = lambda *a, **k: print('UVICORN_REACHED'); "
        "runpy.run_module('atlas.main', run_name='__main__')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert (result.returncode == 0) is succeeds, result.stderr
    assert ("UVICORN_REACHED" in result.stdout) is succeeds
    if not succeeds:
        assert "DEBUG_MODE=true" in result.stderr
