"""Fresh and repeated atlas-init runs produce safe, independent secrets."""

import stat

import pytest
from dotenv import dotenv_values

from atlas import init_cli
from atlas.core.security_config import validate_capability_secret


@pytest.fixture(autouse=True)
def isolate_init(monkeypatch, tmp_path):
    monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)
    monkeypatch.setattr(init_cli, "get_config_dir", lambda: tmp_path / "missing-config")


def _initialize(target, minimal):
    args = init_cli.build_parser().parse_args(
        ["--target", str(target), "--force"] + (["--minimal"] if minimal else [])
    )
    assert init_cli.run_init(args) == 0
    return dotenv_values(target / ".env")


@pytest.mark.parametrize("minimal", [False, True])
def test_fresh_install_debug_off_with_independent_strong_secrets(tmp_path, minimal):
    first = _initialize(tmp_path / "first", minimal)
    second = _initialize(tmp_path / "second", minimal)
    assert first["DEBUG_MODE"] == "false"
    assert first.get("ALLOW_DEBUG_NON_LOOPBACK") != "true"
    secret = first["CAPABILITY_TOKEN_SECRET"]
    assert validate_capability_secret(secret) == secret
    assert len(secret) >= 43
    assert secret != first["MCP_TOKEN_ENCRYPTION_KEY"]
    assert secret != second["CAPABILITY_TOKEN_SECRET"]
    assert stat.S_IMODE((tmp_path / "first" / ".env").stat().st_mode) == 0o600


@pytest.mark.parametrize("minimal", [False, True])
def test_rerun_preserves_existing_capability_secret_across_modes(tmp_path, minimal):
    original = _initialize(tmp_path, minimal)["CAPABILITY_TOKEN_SECRET"]
    assert _initialize(tmp_path, minimal)["CAPABILITY_TOKEN_SECRET"] == original
    assert _initialize(tmp_path, not minimal)["CAPABILITY_TOKEN_SECRET"] == original


@pytest.mark.parametrize("minimal", [False, True])
@pytest.mark.parametrize(
    "old_secret",
    ["", "replace-with-openssl-rand-hex-32", "your-random-string-at-least-32-chars", "short"],
)
def test_rerun_replaces_unsafe_capability_secret(tmp_path, minimal, old_secret):
    (tmp_path / ".env").write_text(f"CAPABILITY_TOKEN_SECRET={old_secret}\n")
    secret = _initialize(tmp_path, minimal)["CAPABILITY_TOKEN_SECRET"]
    assert secret != old_secret
    assert validate_capability_secret(secret) == secret
    assert len(secret) >= 43


@pytest.mark.parametrize("minimal", [False, True])
def test_existing_exported_quoted_secret_is_preserved(tmp_path, minimal):
    secret = "an-existing-valid-capability-signing-secret"
    (tmp_path / ".env").write_text(f'export CAPABILITY_TOKEN_SECRET="{secret}" # keep\n')
    assert _initialize(tmp_path, minimal)["CAPABILITY_TOKEN_SECRET"] == secret


def test_full_init_fallback_has_safe_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(init_cli, "get_env_example_path", lambda: tmp_path / "missing-template")
    values = _initialize(tmp_path, False)
    assert values["DEBUG_MODE"] == "false"
    assert len(values["CAPABILITY_TOKEN_SECRET"]) >= 43
    assert values["CAPABILITY_TOKEN_SECRET"] != values["MCP_TOKEN_ENCRYPTION_KEY"]


def test_full_init_replaces_legacy_template_debug_and_placeholder(tmp_path, monkeypatch):
    template = tmp_path / "legacy.env"
    template.write_text(
        "DEBUG_MODE=true\nCAPABILITY_TOKEN_SECRET=replace-with-openssl-rand-hex-32\n"
    )
    monkeypatch.setattr(init_cli, "get_env_example_path", lambda: template)
    values = _initialize(tmp_path / "install", False)
    assert values["DEBUG_MODE"] == "false"
    assert len(values["CAPABILITY_TOKEN_SECRET"]) >= 43


def test_direct_minimal_env_rerun_preserves_both_keys(tmp_path):
    env_path = tmp_path / ".env"
    assert init_cli.create_minimal_env(env_path, force=True)
    original = dotenv_values(env_path)
    assert init_cli.create_minimal_env(env_path, force=True)
    assert dotenv_values(env_path) == original


def test_env_example_does_not_enable_debug_or_ship_capability_placeholder():
    values = dotenv_values(init_cli.get_env_example_path())
    assert values.get("DEBUG_MODE") is None
    assert not values.get("CAPABILITY_TOKEN_SECRET")
