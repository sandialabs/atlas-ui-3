#!/usr/bin/env python3
"""Minimal MCP server for PR #770 validation: one document-lookup tool.

The tool exists only so a real tool round happens (the stub LLM asks for
`orderdocs_lookup`), forcing the continuation round whose message list carries
the assistant `tool_calls` block plus its tool reply.
"""

import asyncio

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

server = Server("orderdocs")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="lookup",
            description="Look up a safety document by title.",
            inputSchema={
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.ContentBlock]:
    title = (arguments or {}).get("title", "unknown")
    return [types.TextContent(
        type="text",
        text=f"Document '{title}': lock the energy source, tag it, verify zero energy.",
    )]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
