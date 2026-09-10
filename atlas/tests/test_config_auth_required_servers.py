"""/api/config must expose auth-gated MCP servers, tools or not (issue #912).

A server that requires authorization is the one case where an empty tool list
is not a reason to hide the row: the connect control lives on that row, so
omitting it leaves the user with no way to authorize -- and no tools until
they do.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from main import app
from starlette.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config.config_manager import config_manager


def _set_proxy_secret_on_app(secret="test-proxy-secret"):
    for middleware in app.user_middleware:
        if middleware.cls.__name__ == "AuthMiddleware":
            middleware.kwargs["proxy_secret"] = secret
            middleware.kwargs["proxy_secret_enabled"] = True
            return
    raise AssertionError("AuthMiddleware not found")


def _headers():
    return {
        "X-User-Email": config_manager.app_settings.test_user,
        "X-Proxy-Secret": "test-proxy-secret",
    }


def _tool(name):
    return SimpleNamespace(
        name=name, description=f"{name} tool", inputSchema={"type": "object"}
    )


class _FakeManager:
    """Only the surface /api/config touches."""

    def __init__(self, servers_config, available_tools=None, user_tools=None):
        self.servers_config = servers_config
        self.available_tools = available_tools or {}
        self.available_prompts = {}
        self._user_tools = user_tools or {}
        self.discovery_calls = []

    async def get_authorized_servers(self, user, is_user_in_group):
        return list(self.servers_config)

    async def discover_tools_for_user_servers(
        self, user_email, server_names, *, wait_timeout=None
    ):
        self.discovery_calls.append((user_email, list(server_names), wait_timeout))
        return dict(self._user_tools)

    def get_visible_tools_for_server(self, user_email, server_name):
        if server_name in self._user_tools:
            return self._user_tools[server_name]
        return (self.available_tools.get(server_name) or {}).get("tools") or []


def _servers_by_name(payload):
    return {entry["server"]: entry for entry in payload["tools"]}


def _run(manager):
    _set_proxy_secret_on_app()
    settings = app_factory.get_config_manager().app_settings
    original = settings.feature_tools_enabled
    object.__setattr__(settings, "feature_tools_enabled", True)
    try:
        with patch.object(app_factory, "get_mcp_manager", return_value=manager):
            client = TestClient(app)
            resp = client.get("/api/config", headers=_headers())
    finally:
        object.__setattr__(settings, "feature_tools_enabled", original)
    assert resp.status_code == 200
    return resp.json()


def test_auth_required_server_with_no_tools_is_listed():
    manager = _FakeManager(
        {"remote-mcp": {"auth_type": "oauth", "description": "Remote MCP"}}
    )

    servers = _servers_by_name(_run(manager))

    assert "remote-mcp" in servers
    assert servers["remote-mcp"]["tools"] == []
    assert servers["remote-mcp"]["tool_count"] == 0
    assert servers["remote-mcp"]["auth_required"] is True
    assert servers["remote-mcp"]["auth_type"] == "oauth"
    assert servers["remote-mcp"]["description"] == "Remote MCP"


def test_delegated_server_with_no_tools_is_listed_but_not_connectable():
    """It needs per-user credentials, but Atlas mints them -- nothing to connect."""
    manager = _FakeManager({"delegated-mcp": {"auth_type": "delegated"}})

    servers = _servers_by_name(_run(manager))

    assert "delegated-mcp" in servers
    assert servers["delegated-mcp"]["tools"] == []
    assert servers["delegated-mcp"]["auth_required"] is False
    assert servers["delegated-mcp"]["auth_type"] == "delegated"


def test_open_server_with_no_tools_is_still_omitted():
    """Nothing to show and nothing to do -- the old behaviour is kept."""
    manager = _FakeManager(
        {"quiet": {}},
        available_tools={"quiet": {"tools": [], "config": {}}},
    )

    assert "quiet" not in _servers_by_name(_run(manager))


def test_per_user_discovery_result_is_used_for_the_payload():
    manager = _FakeManager(
        {"remote-mcp": {"auth_type": "oauth"}},
        available_tools={"remote-mcp": {"tools": [], "config": {}}},
        user_tools={"remote-mcp": [_tool("search")]},
    )

    servers = _servers_by_name(_run(manager))

    assert servers["remote-mcp"]["tools"] == ["search"]
    assert servers["remote-mcp"]["tools_detailed"][0]["description"] == "search tool"
    assert manager.discovery_calls  # discovery was actually attempted


def test_the_inline_discovery_wait_is_bounded():
    """The SPA polls /api/config; one slow server must not stall every poll."""
    manager = _FakeManager({"remote-mcp": {"auth_type": "oauth"}})

    _run(manager)

    assert manager.discovery_calls
    wait_timeout = manager.discovery_calls[0][2]
    assert wait_timeout is not None and wait_timeout <= 5


def test_discovery_failure_does_not_break_the_config_payload():
    manager = _FakeManager({"remote-mcp": {"auth_type": "oauth"}})
    manager.discover_tools_for_user_servers = AsyncMock(
        side_effect=RuntimeError("provider down")
    )

    servers = _servers_by_name(_run(manager))

    assert "remote-mcp" in servers
    assert servers["remote-mcp"]["tools"] == []
