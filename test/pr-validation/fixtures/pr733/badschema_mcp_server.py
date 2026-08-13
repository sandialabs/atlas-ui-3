#!/usr/bin/env python3
"""Repro MCP server: advertises a tool whose inputSchema is NOT a JSON Schema object.

OpenAI (and other providers) require every function's `parameters` to be a JSON
Schema of `type: "object"`. This server intentionally advertises `type: "string"`
so we can observe what Atlas does with an invalid schema on the wire.

The tool itself works fine when invoked directly -- the defect is purely in the
advertised schema, which is what Atlas forwards to the provider.
"""

import asyncio

import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

server = Server("badschema")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="lookup",
            description="Look up a safety document by title.",
            # INVALID: providers require type "object" here.
            inputSchema={
                "type": "string",
                "description": "the document title",
            },
        ),
        types.Tool(
            name="healthy",
            description="A correctly specified tool, for contrast.",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.ContentBlock]:
    return [types.TextContent(type="text", text=f"{name} ran fine with {arguments}")]


async def main() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
