#!/bin/bash
# Exercise anonymous discovery and authenticated retry against a real HTTP MCP server.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source .venv/bin/activate
export DEBUG_MODE="${DEBUG_MODE:-true}"

python - <<'PY'
import asyncio
import io
import logging
import os
import secrets
import socket
import tempfile

import uvicorn
from fastmcp import FastMCP
from fastmcp.server.auth.providers.debug import DebugTokenVerifier


async def main():
    token = secrets.token_urlsafe(32)
    mcp = FastMCP("delegated-discovery-test", auth=DebugTokenVerifier(
        validate=lambda supplied: supplied == token,
    ))

    @mcp.tool()
    def echo(message: str) -> str:
        return message

    @mcp.prompt()
    def greeting() -> str:
        return "Hello"

    from atlas.modules.mcp_tools.client import MCPToolManager
    from atlas.modules.mcp_tools.token_storage import get_token_storage

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(mcp.http_app(), log_level="warning"))
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        manager = MCPToolManager()
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        logger = logging.getLogger("atlas.modules.mcp_tools.mcp_discovery")
        logger.setLevel(logging.WARNING)
        logger.addHandler(handler)
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if server_task.done():
                        await server_task
                        raise RuntimeError("MCP server stopped before startup")
                    await asyncio.sleep(0.05)
            manager.servers_config = {"protected": {
                "url": f"http://127.0.0.1:{listener.getsockname()[1]}/mcp",
                "transport": "http",
                "auth_type": "delegated",
                "delegation": {"audience": "api://test", "scope": "tools.read"},
            }}
            await manager.initialize_clients()
            await manager.discover_tools()
            await manager.discover_prompts()
            assert manager.available_tools["protected"]["tools"] == []
            assert manager.available_prompts["protected"]["prompts"] == []
            logs = output.getvalue()
            print(logs, end="")
            assert logs.count("WARNING:") == 2, logs
            assert "ERROR:" not in logs, logs
            assert "Tools discovery deferred" in logs
            assert "Prompts discovery deferred" in logs
            assert "retry tool discovery" in logs and "after login" in logs
            print("PASSED: startup 401s produce explanatory warnings, not errors")

            # Emulate a downstream credential already minted by the OBO exchange.
            get_token_storage().store_token(
                user_email="user@example.com", server_name="protected",
                token_type="bearer", token_value=token,
                metadata={"source": "delegation"},
            )
            tools = await manager.discover_tools_for_user("user@example.com", "protected")
            assert tools is not None and [tool.name for tool in tools] == ["echo"]
            print("PASSED: per-user discovery succeeds with a downstream credential")
        finally:
            logger.removeHandler(handler)
            await manager.cleanup()
            server.should_exit = True
            await server_task


with tempfile.TemporaryDirectory(prefix="atlas-pr1003-") as state:
    os.environ["MCP_TOKEN_STORAGE_DIR"] = state
    os.environ["MCP_TOKEN_ENCRYPTION_KEY"] = secrets.token_urlsafe(32)
    asyncio.run(main())
PY

python -m pytest -q atlas/tests/test_mcp_delegated_discovery_logging.py \
    atlas/tests/test_mcp_hot_reload.py atlas/tests/test_mcp_user_discovery.py \
    atlas/tests/test_oidc_mcp_delegation.py
bash test/run_tests.sh backend
