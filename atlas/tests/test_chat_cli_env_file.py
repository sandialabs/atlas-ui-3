"""Tests for atlas-chat env-file resolution (--env-file / ATLAS_ENV_FILE).

Regression coverage for tilde (``~``) expansion: server_cli and init_cli both
call ``.expanduser()`` on the resolved path, and atlas-chat must match so a
quoted ``ATLAS_ENV_FILE="~/.atlasrc"`` (or ``--env-file="~/..."``) resolves the
same way across every entry point.
"""

import os
from pathlib import Path


def _load_resolver(tmp_path):
    """Import atlas_chat_cli with a valid env file so module init succeeds.

    The module resolves the env file at import time and exits if an explicitly
    requested file is missing, so point it at a real file before importing,
    then return the resolver for direct testing.
    """
    real_env = tmp_path / "real.env"
    real_env.write_text("OPENAI_API_KEY=test\n")
    os.environ["ATLAS_ENV_FILE"] = str(real_env)
    from atlas.atlas_chat_cli import _get_env_file_from_args

    return _get_env_file_from_args


class TestChatCliEnvFile:
    def test_env_var_tilde_is_expanded(self, tmp_path, monkeypatch):
        resolver = _load_resolver(tmp_path)
        monkeypatch.setattr("sys.argv", ["atlas-chat"])
        monkeypatch.setenv("ATLAS_ENV_FILE", "~/.atlasrc")

        path, is_custom = resolver()

        assert is_custom is True
        assert "~" not in str(path)
        assert path == Path("~/.atlasrc").expanduser()

    def test_flag_tilde_is_expanded(self, tmp_path, monkeypatch):
        resolver = _load_resolver(tmp_path)
        monkeypatch.setattr("sys.argv", ["atlas-chat", "--env-file", "~/.atlasrc"])
        monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)

        path, is_custom = resolver()

        assert is_custom is True
        assert "~" not in str(path)
        assert path == Path("~/.atlasrc").expanduser()

    def test_flag_equals_form_tilde_is_expanded(self, tmp_path, monkeypatch):
        resolver = _load_resolver(tmp_path)
        monkeypatch.setattr("sys.argv", ["atlas-chat", "--env-file=~/.atlasrc"])
        monkeypatch.delenv("ATLAS_ENV_FILE", raising=False)

        path, is_custom = resolver()

        assert is_custom is True
        assert path == Path("~/.atlasrc").expanduser()
