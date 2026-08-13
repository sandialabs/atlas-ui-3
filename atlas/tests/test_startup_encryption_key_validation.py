"""Startup validation of MCP_TOKEN_ENCRYPTION_KEY.

The key is only resolved lazily inside request handlers (GET /api/config,
the MCP/LLM auth routes). A server started without a key — or with the
placeholder that ships in .env.example — therefore looked healthy
(/api/health and /api/config/shell returned 200) and then failed every
/api/config request with a bare 500 whose cause never reached the frontend.

These tests pin the fix: the lifespan refuses to start instead.
"""

import types

import pytest

from atlas.modules.mcp_tools.token_storage import (
    _PLACEHOLDER_ENCRYPTION_KEYS,
    MIN_ENCRYPTION_KEY_LENGTH,
    resolve_encryption_key,
)

VALID_KEY = "a" * MIN_ENCRYPTION_KEY_LENGTH


def _settings(key):
    """Minimal stub for the key check alone (see _full_settings for lifespan)."""
    return types.SimpleNamespace(mcp_token_encryption_key=key)


def _full_settings(key):
    """Enough of AppSettings for the lifespan to get past the key check."""
    return types.SimpleNamespace(
        mcp_token_encryption_key=key,
        debug_mode=True,
        feature_proxy_secret_enabled=False,
        proxy_secret=None,
        feature_globus_auth_enabled=False,
        globus_session_secret=None,
        runtime_feedback_dir=None,
    )


class TestResolveEncryptionKey:
    """The shared guard used by both startup and MCPTokenStorage."""

    def test_returns_configured_key(self):
        assert resolve_encryption_key(
            app_settings=_settings("a-real-and-unique-key-1234567890")
        ) == "a-real-and-unique-key-1234567890"

    def test_explicit_argument_wins(self):
        assert resolve_encryption_key(
            encryption_key="explicit-key-1234567890abcdefghij",
            app_settings=_settings(None),
        ) == "explicit-key-1234567890abcdefghij"

    def test_rejects_missing_key(self):
        with pytest.raises(RuntimeError, match="MCP_TOKEN_ENCRYPTION_KEY"):
            resolve_encryption_key(app_settings=_settings(None))

    @pytest.mark.parametrize("placeholder", sorted(_PLACEHOLDER_ENCRYPTION_KEYS))
    def test_rejects_shipped_placeholder(self, placeholder):
        with pytest.raises(RuntimeError, match="MCP_TOKEN_ENCRYPTION_KEY"):
            resolve_encryption_key(app_settings=_settings(placeholder))

    def test_error_message_tells_operator_how_to_generate_one(self):
        with pytest.raises(RuntimeError) as exc:
            resolve_encryption_key(app_settings=_settings(None))
        assert "secrets.token_urlsafe" in str(exc.value)

    @pytest.mark.parametrize("length", [1, MIN_ENCRYPTION_KEY_LENGTH - 1])
    def test_rejects_key_shorter_than_the_documented_minimum(self, length):
        """Every surface promises ">=32 characters"; the gate makes it true.

        The key is stretched with PBKDF2 under a hardcoded constant salt, so a
        short value is brute-forceable offline from the token store alone.
        """
        with pytest.raises(RuntimeError, match="at least 32 characters"):
            resolve_encryption_key(app_settings=_settings("k" * length))

    def test_accepts_key_at_exactly_the_minimum(self):
        assert resolve_encryption_key(app_settings=_settings(VALID_KEY)) == VALID_KEY

    def test_explicit_key_does_not_load_app_settings(self, monkeypatch):
        """atlas-init validates a candidate key without booting configuration."""
        import importlib

        def _boom():
            raise AssertionError("app settings loaded despite an explicit key")

        # atlas.modules.config re-exports a ConfigManager instance under the
        # name `config_manager`, shadowing the submodule, so the dotted-string
        # form of setattr patches the wrong object.
        config_manager = importlib.import_module(
            "atlas.modules.config.config_manager"
        )
        monkeypatch.setattr(config_manager, "get_app_settings", _boom)
        assert resolve_encryption_key(encryption_key=VALID_KEY) == VALID_KEY


class TestLifespanRefusesToStart:
    """The failure must happen at boot, not on the first /api/config call."""

    @staticmethod
    def _run_lifespan(monkeypatch, key, settings_factory=_settings):
        import asyncio

        from atlas import main as atlas_main

        config = types.SimpleNamespace(
            app_settings=settings_factory(key),
            llm_config=types.SimpleNamespace(models={}),
            mcp_config=types.SimpleNamespace(servers={}),
        )
        monkeypatch.setattr(
            atlas_main.app_factory, "get_config_manager", lambda: config
        )

        async def _enter():
            async with atlas_main.lifespan(atlas_main.app):
                pass

        asyncio.run(_enter())

    def test_missing_key_aborts_startup(self, monkeypatch):
        with pytest.raises(RuntimeError, match="MCP_TOKEN_ENCRYPTION_KEY"):
            self._run_lifespan(monkeypatch, None)

    def test_placeholder_key_aborts_startup(self, monkeypatch):
        with pytest.raises(RuntimeError, match="MCP_TOKEN_ENCRYPTION_KEY"):
            self._run_lifespan(monkeypatch, "your-random-string-at-least-32-chars")

    def test_validation_runs_before_mcp_initialization(self, monkeypatch):
        """A bad key must abort before any MCP client work begins.

        Guards against the check drifting later in the lifespan, where the
        MCP try/except would swallow it and startup would continue.
        """
        from atlas import main as atlas_main

        def _fail(*args, **kwargs):
            raise AssertionError("MCP manager touched despite invalid key")

        monkeypatch.setattr(atlas_main.app_factory, "get_mcp_manager", _fail)
        with pytest.raises(RuntimeError, match="MCP_TOKEN_ENCRYPTION_KEY"):
            self._run_lifespan(monkeypatch, None)

    def test_valid_key_lets_startup_reach_mcp_initialization(self, monkeypatch):
        """Positive control for the ordering test above.

        Without this, ``test_validation_runs_before_mcp_initialization`` would
        still pass if the lifespan aborted before MCP init for some unrelated
        reason — it only proves the manager was *not* touched. Here a valid key
        must let startup get all the way to ``get_mcp_manager()``.
        """
        from atlas import main as atlas_main

        class _ReachedMCPInit(Exception):
            pass

        def _sentinel(*args, **kwargs):
            raise _ReachedMCPInit

        # get_mcp_manager() is called outside the lifespan's MCP try/except,
        # so the sentinel propagates instead of being swallowed.
        monkeypatch.setattr(atlas_main.app_factory, "get_mcp_manager", _sentinel)
        with pytest.raises(_ReachedMCPInit):
            self._run_lifespan(
                monkeypatch, VALID_KEY, settings_factory=_full_settings
            )
