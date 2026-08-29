"""End-to-end harness for PR #857: typed-object MCP tool results must serialize.

Runs a real FastMCP server in-process and drives it with the real FastMCP
client, then feeds the resulting CallToolResult through the real ATLAS
result normalizer and the same `json.dumps(..., default=str)` call that
`execute_tool` uses. This reproduces the exact "Root" code path: a tool
returning a typed object (pydantic model / typed dict / dataclass) gets an
object `output_schema` with `properties` but no `title`, so the client
validates the structuredContent into a pydantic dataclass named `Root`, and
`CallToolResult.data` arrives as a model instance rather than a plain dict.
"""
import asyncio
import json
import sys
from typing import Any, Dict, List

from fastmcp import FastMCP
from fastmcp.client import Client
from pydantic import BaseModel

from atlas.modules.mcp_tools.client import MCPToolManager


class Doc(BaseModel):
    query: str
    answers: List[str]


mcp = FastMCP("pr857-root-server")


@mcp.tool
def typed_tool(query: str) -> Doc:
    """Typed object return -> object output_schema with properties, no title.

    The client rebuilds a pydantic dataclass named 'Root' for this, which is
    the exact shape that used to raise 'Object of type Root is not JSON
    serializable'.
    """
    return Doc(query=query, answers=["a1", "a2"])


@mcp.tool
def dict_tool(query: str) -> Dict[str, Any]:
    """Unconstrained dict return -> dict[str, Any]; must keep working."""
    return {"query": query, "answers": ["a1", "a2"]}


@mcp.tool
def str_tool(query: str) -> str:
    """Scalar str return (wrapped on the wire, unwrapped to str); must keep working."""
    return json.dumps({"query": query})


def _serialize(res) -> Dict[str, Any]:
    mgr = MCPToolManager(config_path="/tmp/nonexistent_pr857_mcp.json")
    normalized = mgr._normalize_mcp_tool_result(res)
    content = json.dumps(normalized, ensure_ascii=False, default=str)
    return {"data_type": type(res.data).__name__, "content": content}


async def main() -> int:
    failures: List[str] = []

    async with Client(mcp) as client:
        # 1. Typed object: the bug path. data must be a 'Root' instance (proving
        #    we exercised the real client-side validation), and ATLAS must still
        #    serialize it to JSON carrying the object's fields.
        typed = await client.call_tool("typed_tool", {"query": "hello"})
        if type(typed.data).__name__ != "Root":
            failures.append(
                f"typed_tool: expected client data type 'Root', got "
                f"{type(typed.data).__name__!r} (bug path not exercised)"
            )
        out = _serialize(typed)
        try:
            decoded = json.loads(out["content"])
            assert decoded["results"]["query"] == "hello", decoded
            assert decoded["results"]["answers"] == ["a1", "a2"], decoded
        except Exception as exc:  # noqa: BLE001
            failures.append(f"typed_tool: serialization failed: {exc} :: {out['content']!r}")

        # 2. Dict[str, Any] return: no 'Root' model, plain dict; still serializes.
        d = await client.call_tool("dict_tool", {"query": "hello"})
        out = _serialize(d)
        try:
            decoded = json.loads(out["content"])
            assert decoded["results"]["query"] == "hello", decoded
        except Exception as exc:  # noqa: BLE001
            failures.append(f"dict_tool: serialization failed: {exc} :: {out['content']!r}")

        # 3. str return: wrapped/unwrapped to a string; still serializes.
        s = await client.call_tool("str_tool", {"query": "hello"})
        out = _serialize(s)
        try:
            decoded = json.loads(out["content"])
            # str results land under 'results' verbatim (a JSON string here).
            assert json.loads(decoded["results"])["query"] == "hello", decoded
        except Exception as exc:  # noqa: BLE001
            failures.append(f"str_tool: serialization failed: {exc} :: {out['content']!r}")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    print("All PR #857 harness checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
