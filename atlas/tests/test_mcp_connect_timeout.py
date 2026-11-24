"""Startup MCP discovery must not wait forever on a server that never connects."""

import asyncio
import logging
import sys
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from atlas.modules.mcp_tools.client import MCPToolManager
from atlas.modules.mcp_tools.mcp_connect_timeout import connected_client

CONFIG_MANAGER = "atlas.modules.mcp_tools.client.config_manager"
LOGGER = "atlas.modules.mcp_tools.mcp_discovery"


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
async def test_discovery_gives_up_on_a_server_that_never_connects(kind, caplog):
    manager = manager_for("stuck")
    client = never_connecting_client()

    with patch(CONFIG_MANAGER) as config_manager, caplog.at_level(logging.ERROR, logger=LOGGER):
        config_manager.app_settings.mcp_discovery_timeout = 0.05
        result = await asyncio.wait_for(
            getattr(manager, f"_discover_{kind}_for_server")("stuck", client),
            timeout=5,
        )

    assert result == {kind: [], "config": manager.servers_config["stuck"]}
    getattr(client, f"list_{kind}").assert_not_awaited()
    messages = [record.getMessage() for record in caplog.records]
    assert any("did not complete the MCP handshake" in message for message in messages)
    assert not any("slow to respond" in message for message in messages)
    if kind == "tools":
        assert "Timeout connecting" in manager._failed_servers["stuck"]["error"]


SILENT_SERVER = "import os, sys, time; open(sys.argv[1], 'a').write(f'{os.getpid()}\\n'); time.sleep(60)"


def silent_stdio_client(pid_file):
    """A real stdio process that never speaks MCP, built the way Atlas builds stdio clients."""
    transport = StdioTransport(command=sys.executable, args=["-c", SILENT_SERVER, str(pid_file)])
    return Client(transport)


def process_is_running(pid):
    try:
        with open(f"/proc/{pid}/stat") as stat:
            return stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    except FileNotFoundError:
        return False


async def wait_until_gone(pid_file, expected):
    """The spawned processes must be terminated, not left behind as orphans."""
    for _ in range(100):
        pids = [int(line) for line in pid_file.read_text().split()] if pid_file.exists() else []
        if len(pids) == expected and not any(process_is_running(pid) for pid in pids):
            return pids
        await asyncio.sleep(0.1)
    pytest.fail(f"stdio processes still running after a connect timeout: {pids}")


@pytest.mark.asyncio
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
async def test_discovery_gives_up_on_a_silent_stdio_process(tmp_path):
    """Tools then prompts, as at startup: each phase times out and leaves no process."""
    manager = manager_for("silent")
    pid_file = tmp_path / "pids"
    client = silent_stdio_client(pid_file)

    with patch(CONFIG_MANAGER) as config_manager:
        config_manager.app_settings.mcp_discovery_timeout = 1
        tools = await asyncio.wait_for(
            manager._discover_tools_for_server("silent", client), timeout=20
        )
        await wait_until_gone(pid_file, expected=1)
        # The prompts phase reuses the client that has just timed out.
        prompts = await asyncio.wait_for(
            manager._discover_prompts_for_server("silent", client), timeout=20
        )
        pids = await wait_until_gone(pid_file, expected=2)

    assert tools["tools"] == [] and prompts["prompts"] == []
    assert len(set(pids)) == 2, "the second phase must spawn a fresh process"
    assert "Timeout connecting" in manager._failed_servers["silent"]["error"]


@pytest.mark.asyncio
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
async def test_connected_client_can_be_entered_again_after_a_timeout(tmp_path):
    pid_file = tmp_path / "pids"
    client = silent_stdio_client(pid_file)

    for attempt in (1, 2):
        with pytest.raises(TimeoutError, match="Timeout connecting to MCP server"):
            await asyncio.wait_for(enter(client, 1), timeout=20)
        await wait_until_gone(pid_file, expected=attempt)
        assert not client.is_connected()


async def enter(client, timeout):
    async with connected_client(client, timeout):
        return None
