"""Stdio MCP server used by test_pr972_tool_approval_config_payload.sh.

Exposes one approval-gated tool and one free tool so the script can validate
the /api/config tool_approvals payload against a real discovered tool list.
"""
from fastmcp import FastMCP

mcp = FastMCP("approval-demo")


@mcp.tool
def delete_file(path: str) -> str:
    """Delete a file (would require approval)."""
    return f"would delete {path}"


@mcp.tool
def read_file(path: str) -> str:
    """Read a file (no approval required)."""
    return f"contents of {path}"


if __name__ == "__main__":
    mcp.run(show_banner=False)
