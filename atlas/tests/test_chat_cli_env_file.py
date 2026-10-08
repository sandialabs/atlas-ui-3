"""Tests for atlas-chat env-file resolution (--env-file / ATLAS_ENV_FILE).

Regression coverage for tilde (``~``) expansion: server_cli and init_cli both
call ``.expanduser()`` on the resolved path, and atlas-chat must match so a
quoted ``ATLAS_ENV_FILE="~/.atlasrc"`` (or ``--env-file="~/..."``) resolves the
same way across every entry point.
"""

from pathlib import Path


def _load_resolver(tmp_path, monkeypatch):
    """Import atlas_chat_cli with a valid env file so module init succeeds.

    The module resolves the env file at import time and exits if an explicitly
    requested file is missing, so point it at a real file before importing,
    then return the resolver for direct testing.

    ``ATLAS_ENV_FILE`` is set through monkeypatch so it is unset again at
    teardown. Setting it directly on ``os.environ`` left the whole rest of the
    session pointed at this test's ``tmp_path`` -- a path pytest later deletes,
    and one that every subprocess-spawning CLI test inherits.
    """
    real_env = tmp_path / "real.env"
    real_env.write_text("OPENAI_API_KEY=test\n")
    monkeypatch.setenv("ATLAS_ENV_FILE", str(real_env))
    from atlas.atlas_chat_cli import _get_env_file_from_args

    return _get_env_file_from_args


class TestChatCliEnvFile:
    def test_env_var_tilde_is_expanded(self, tmp_path, monkeypatch):
        resolver = _load_resolver(tmp_path, monkeypatch)
        monkeypatch.setattr("sys.argv", ["atlas-chat"])
        monkeypatch.setenv("ATLAS_ENV_FILE", "~/.atlasrc")

        path, is_custom = resolver()

        assert is_custom is True
        assert "~" not in str(path)
        assert path == Path("~/.atlasrc").expanduser()

    def test_flag_tilde_is_expanded(self, tmp_path, monkeypatch):
        resolver = _load_resolver(tmp_path, monkeypatch)
        monkeypatch.setattr("sys.argv", ["atlas-chat", "--env-file", "~/.atlasrc"])
        monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)

        path, is_custom = resolver()

        assert is_custom is True
        assert "~" not in str(path)
        assert path == Path("~/.atlasrc").expanduser()

    def test_flag_equals_form_tilde_is_expanded(self, tmp_path, monkeypatch):
        resolver = _load_resolver(tmp_path, monkeypatch)
        monkeypatch.setattr("sys.argv", ["atlas-chat", "--env-file=~/.atlasrc"])
        monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)

        path, is_custom = resolver()

        assert is_custom is True
        assert path == Path("~/.atlasrc").expanduser()


def test_legacy_approval_file_option_is_a_deprecated_no_op(tmp_path, monkeypatch, capsys):
    _load_resolver(tmp_path, monkeypatch)
    from atlas.atlas_chat_cli import _warn_deprecated_flags, build_parser

    # The retired approvals-file flag still parses so existing scripts keep
    # working, but it is ignored (tool approvals come from mcp.json now).
    args = build_parser().parse_args(["--tool-approvals-config", "unused.json"])
    assert args.tool_approvals_config == "unused.json"

    monkeypatch.delenv("TOOL_APPROVALS_CONFIG_FILE", raising=False)
    from os import environ

    assert "TOOL_APPROVALS_CONFIG_FILE" not in environ

    _warn_deprecated_flags(args)
    assert "--tool-approvals-config is deprecated and ignored" in capsys.readouterr().err


def test_no_deprecation_warning_without_the_legacy_flag(tmp_path, monkeypatch, capsys):
    _load_resolver(tmp_path, monkeypatch)
    from atlas.atlas_chat_cli import _warn_deprecated_flags, build_parser

    _warn_deprecated_flags(build_parser().parse_args(["hi"]))
    assert capsys.readouterr().err == ""


def test_mcp_config_override_remains_supported(tmp_path, monkeypatch):
    _load_resolver(tmp_path, monkeypatch)
    from atlas.atlas_chat_cli import _apply_config_overrides_from_args, build_parser

    monkeypatch.setattr("sys.argv", ["atlas-chat", "--mcp-config", "custom-mcp.json"])
    monkeypatch.delenv("MCP_CONFIG_FILE", raising=False)
    monkeypatch.delenv("TOOL_APPROVALS_CONFIG_FILE", raising=False)
    # Register restoration before the override writes directly to os.environ.
    monkeypatch.setenv("MCP_CONFIG_FILE", "")
    _apply_config_overrides_from_args()
    assert build_parser().parse_args(["--mcp-config", "custom-mcp.json"]).mcp_config == "custom-mcp.json"
    from os import environ

    assert environ["MCP_CONFIG_FILE"] == "custom-mcp.json"
    assert "TOOL_APPROVALS_CONFIG_FILE" not in environ
