"""Drive the HTTP session_state_demo server through MCPToolManager.

MCP 2026-07-28 connections are stateless, so the demo keeps its cart in an
explicit FastMCP session. This checks that a cart survives across separate
tool calls, and that a session id nobody created is refused.

Usage: session_state_e2e.py <mcp.json> (server must already be listening).
"""

import asyncio
import json
import sys
from typing import Any, Dict, List

from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools.client import MCPToolManager

FAILURES: List[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {name} ... {'PASS' if ok else 'FAIL'}{(' - ' + detail) if detail and not ok else ''}")
    if not ok:
        FAILURES.append(name)


async def run(manager: MCPToolManager, tool: str, arguments: Dict[str, Any], user: str, conversation: str):
    return await manager.execute_tool(
        ToolCall(id=f"{tool}-{conversation}", name=f"session_state_demo_{tool}", arguments=arguments),
        context={"user_email": user, "conversation_id": conversation, "update_callback": None},
    )


async def main(config_path: str) -> int:
    manager = MCPToolManager(config_path=config_path)
    try:
        await manager.initialize_clients()
        await manager.discover_tools()
        tools = {t.name for t in manager.available_tools.get("session_state_demo", {}).get("tools", [])}
        check("session tools are offered", {"create_session", "add_to_cart", "view_cart"} <= tools, str(sorted(tools)))

        created = await run(manager, "create_session", {}, "alice@example.com", "conv-a")
        session_id = json.loads(created.content)["results"] if created.success else ""
        check("create_session returns an id", bool(session_id), created.content[:200])

        added = await run(
            manager, "add_to_cart",
            {"item": "apple", "quantity": 2, "price": 1.5, "session_id": session_id},
            "alice@example.com", "conv-a",
        )
        check("add_to_cart succeeds", added.success, added.content[:200])

        viewed = await run(manager, "view_cart", {"session_id": session_id}, "alice@example.com", "conv-a")
        cart = json.loads(viewed.content)["results"] if viewed.success else {}
        check(
            "cart survives across separate tool calls",
            cart.get("item_count") == 1 and cart.get("total") == 3.0,
            viewed.content[:200],
        )

        guessed = await run(manager, "view_cart", {"session_id": "not-a-real-session"}, "bob@example.com", "conv-b")
        check("an id that was never created is refused", not guessed.success, guessed.content[:200])
    finally:
        await manager.cleanup()

    print(f"\n{'ALL CHECKS PASSED' if not FAILURES else 'FAILED: ' + ', '.join(FAILURES)}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1])))
