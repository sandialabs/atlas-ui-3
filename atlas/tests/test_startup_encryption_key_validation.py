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
    resolve_encryption_key,
)


def _settings(key):
    return types.SimpleNamespace(mcp_token_encryption_key=key)


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


class TestLifespanRefusesToStart:
    """The failure must happen at boot, not on the first /api/config call."""

    @staticmethod
    def _run_lifespan(monkeypatch, key):
        import asyncio

        from atlas import main as atlas_main

        config = types.SimpleNamespace(app_settings=_settings(key))
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
