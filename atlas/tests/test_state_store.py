"""Tests for pluggable state store factory."""
import pytest
from unittest.mock import patch, MagicMock


class TestGetStateStore:
    def test_memory_backend_returns_none(self):
        """Memory backend (default) returns None so FastMCP uses its built-in store."""
        from atlas.mcp.common.state import get_state_store

        with patch.dict("os.environ", {"MCP_STATE_BACKEND": "memory"}):
            assert get_state_store() is None

    def test_default_returns_none(self):
        """When env var is unset, defaults to memory (None)."""
        from atlas.mcp.common.state import get_state_store

        with patch.dict("os.environ", {}, clear=True):
            assert get_state_store() is None

    def test_redis_import_error_falls_back(self):
        """When pykeyvalue[redis] is not installed, falls back to None."""
        from atlas.mcp.common.state import get_state_store

        with patch.dict("os.environ", {"MCP_STATE_BACKEND": "redis"}):
            with patch("builtins.__import__", side_effect=ImportError("no module")):
                assert get_state_store() is None

    def test_redis_connection_error_falls_back(self):
        """When Redis connection fails, falls back to None."""
        from atlas.mcp.common.state import get_state_store

        mock_redis_store = MagicMock(side_effect=ConnectionError("refused"))
        with patch.dict("os.environ", {"MCP_STATE_BACKEND": "redis"}):
            with patch.dict("sys.modules", {"key_value": MagicMock(), "key_value.aio": MagicMock(), "key_value.aio.stores": MagicMock(), "key_value.aio.stores.redis": MagicMock(RedisStore=mock_redis_store)}):
                assert get_state_store() is None

    def test_unrecognized_backend_returns_none(self):
        """Unrecognized backend values fall back to memory (None)."""
        from atlas.mcp.common.state import get_state_store

        with patch.dict("os.environ", {"MCP_STATE_BACKEND": "postgres"}):
            assert get_state_store() is None
