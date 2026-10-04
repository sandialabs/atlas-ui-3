"""Startup discovery messaging for MCP servers requiring delegated credentials."""

import logging
from unittest.mock import AsyncMock

import httpx
import pytest

from atlas.modules.mcp_tools.client import MCPToolManager

LOGGER = "atlas.modules.mcp_tools.mcp_discovery"


def discovery_manager(auth_type, error, phase):
    manager = MCPToolManager.__new__(MCPToolManager)
    manager.servers_config = {
        "protected": {
            "url": "http://localhost:8081/mcp",
            "auth_type": auth_type,
            "delegation": {"audience": "api://test", "scope": "tools.read"},
        }
    }
    manager._failed_servers = {}
    client = AsyncMock()
    if phase == "connect":
        client.__aenter__.side_effect = error
    else:
        getattr(client, f"list_{phase}").side_effect = error
    return manager, client


def http_error(status):
    response = httpx.Response(status, request=httpx.Request("POST", "http://localhost:8081/mcp"))
    return httpx.HTTPStatusError(f"HTTP {status}", request=response.request, response=response)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,phase", [
    ("tools", "connect"), ("tools", "tools"),
    ("prompts", "connect"), ("prompts", "prompts"),
])
@pytest.mark.parametrize("wrapper", [None, "cause", "context"])
async def test_delegated_401_explains_user_retry(caplog, kind, phase, wrapper):
    error = http_error(401)
    if wrapper:
        wrapped = RuntimeError("Client initialization failed")
        setattr(wrapped, f"__{wrapper}__", error)
        error = wrapped
    manager, client = discovery_manager("delegated", error, phase)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = await getattr(manager, f"_discover_{kind}_for_server")("protected", client)

    assert result == {kind: [], "config": manager.servers_config["protected"]}
    records = [record for record in caplog.records if record.name == LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    message = records[0].getMessage()
    assert f"{kind.capitalize()} discovery deferred" in message
    assert "'protected'" in message
    assert "HTTP 401" in message
    assert "no user session" in message
    assert "retry tool discovery" in message
    assert "delegated credentials" in message
    assert "after login" in message
    assert "OBO" in message
    if phase != "prompts":
        assert "protected" in manager._failed_servers


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tools", "prompts"])
@pytest.mark.parametrize("auth_type,status", [
    ("none", 401), ("oauth", 401), ("delegated", 403),
    ("delegated", 500), ("delegated", None),
])
async def test_other_discovery_failures_remain_errors(caplog, kind, auth_type, status):
    error = http_error(status) if status else ConnectionError("Connection refused")
    manager, client = discovery_manager(auth_type, error, "connect")

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = await getattr(manager, f"_discover_{kind}_for_server")("protected", client)

    assert result[kind] == []
    assert f"{kind[:-1].upper()} DISCOVERY FAILED" in caplog.text
    assert "discovery deferred" not in caplog.text
    assert any(record.levelno == logging.ERROR for record in caplog.records)
    assert "protected" in manager._failed_servers
