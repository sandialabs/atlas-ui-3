"""Startup MCP discovery must not wait forever on a server that never connects."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.mcp_connect_timeout import connected_client

CONFIG_MANAGER = "atlas.modules.mcp_tools.client.config_manager"


def manager_for(server_name):
    manager = MCPToolManager.__new__(MCPToolManager)
    manager.servers_config = {server_name: {"command": ["python", "main.py"]}}
    manager._failed_servers = {}
    return manager


def never_connecting_client():
    client = AsyncMock()

    async def hang(*_args):
        await asyncio.Event().wait()

    client.__aenter__.side_effect = hang
    return client


@pytest.mark.asyncio
async def test_connected_client_times_out_with_a_message():
    client = never_connecting_client()

    block_ran = False

    async def connect():
        nonlocal block_ran
        async with connected_client(client, 0.05):
            block_ran = True

    with pytest.raises(TimeoutError, match="Timeout connecting to MCP server"):
        await connect()

    assert not block_ran, "the block must not run without a connection"
    client.__aexit__.assert_not_awaited()


@pytest.mark.asyncio
async def test_connected_client_enters_and_exits_once():
    client = AsyncMock()

    async with connected_client(client, 1) as entered:
        assert entered is client
        client.__aexit__.assert_not_awaited()

    client.__aenter__.assert_awaited_once()
    client.__aexit__.assert_awaited_once()
    assert client.__aexit__.await_args.args[-3:] == (None, None, None)


@pytest.mark.asyncio
async def test_connected_client_exits_and_propagates_block_errors():
    client = AsyncMock()

    async def failing_block():
        async with connected_client(client, 1):
            raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await failing_block()

    client.__aexit__.assert_awaited_once()
    assert ValueError in client.__aexit__.await_args.args


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tools", "prompts"])
async def test_discovery_gives_up_on_a_server_that_never_connects(kind):
    manager = manager_for("stuck")
    client = never_connecting_client()

    with patch(CONFIG_MANAGER) as config_manager:
        config_manager.app_settings.mcp_discovery_timeout = 0.05
        result = await asyncio.wait_for(
            getattr(manager, f"_discover_{kind}_for_server")("stuck", client),
            timeout=5,
        )

    assert result == {kind: [], "config": manager.servers_config["stuck"]}
    getattr(client, f"list_{kind}").assert_not_awaited()
    if kind == "tools":
        assert "Timeout connecting" in manager._failed_servers["stuck"]["error"]


@pytest.mark.asyncio
async def test_discovery_gives_up_on_a_silent_stdio_process():
    """A real stdio process that never speaks MCP: the handshake never ends."""
    manager = manager_for("silent")
    manager._record_server_failure = MagicMock()
    transport = StdioTransport(
        command=sys.executable, args=["-c", "import time; time.sleep(60)"], keep_alive=False
    )
    client = Client(transport)

    with patch(CONFIG_MANAGER) as config_manager:
        config_manager.app_settings.mcp_discovery_timeout = 1
        result = await asyncio.wait_for(
            manager._discover_tools_for_server("silent", client), timeout=20
        )

    assert result["tools"] == []
    manager._record_server_failure.assert_called_once()
    assert "Timeout connecting" in manager._record_server_failure.call_args.args[1]
    await transport.close()
