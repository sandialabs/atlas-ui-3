#!/usr/bin/env python3
"""
Logging Demo MCP Server using FastMCP
Demonstrates MCP server logging at various levels.
"""

import asyncio
import time
from typing import Any, Dict

from fastmcp import Context, FastMCP

# Initialize the MCP server
mcp = FastMCP("Logging Demo")


@mcp.tool
async def test_logging(operation: str, ctx: Context) -> Dict[str, Any]:
    """Test MCP server logging at various levels.

    This tool demonstrates log output from an MCP server. It emits log messages
    at different levels (debug, info, warning, error) to test the logging infrastructure.

    Args:
        operation: Which logging levels to test. Options:
            - "all": Test all log levels
            - "debug": Debug level only
            - "info": Info level only
            - "warning": Warning level only
            - "error": Error level only
            - "mixed": A realistic mix of levels
            - "mixed-delay": Like "mixed", but with short delays between log calls

    Returns:
        Dict with results and logs emitted
    """
    start = time.perf_counter()

    logs_emitted = []

    if operation == "all" or operation == "debug":
        await ctx.debug("This is a DEBUG message - detailed information for diagnostics")
        logs_emitted.append("debug")

    if operation == "all" or operation == "info":
        await ctx.info("This is an INFO message - general informational message")
        logs_emitted.append("info")

    if operation == "all" or operation == "warning":
        await ctx.warning("This is a WARNING message - something unexpected happened")
        logs_emitted.append("warning")

    if operation == "all" or operation == "error":
        await ctx.error("This is an ERROR message - something went wrong")
        logs_emitted.append("error")

    if operation == "mixed":
        await ctx.info("Starting operation...")
        await ctx.debug("Processing step 1")
        await ctx.debug("Processing step 2")
        await ctx.info("Operation in progress (50% complete)")
        await ctx.debug("Processing step 3")
        await ctx.warning("Encountered a minor issue, continuing...")
        await ctx.debug("Processing step 4")
        await ctx.info("Operation completed successfully")
        logs_emitted = ["info", "debug", "debug", "info", "debug", "warning", "debug", "info"]

    if operation == "mixed-delay":
        delay_s = 0.35
        await ctx.info("Starting operation...")
        await asyncio.sleep(delay_s)
        await ctx.debug("Processing step 1")
        await asyncio.sleep(delay_s)
        await ctx.debug("Processing step 2")
        await asyncio.sleep(delay_s)
        await ctx.info("Operation in progress (50% complete)")
        await asyncio.sleep(delay_s)
        await ctx.debug("Processing step 3")
        await asyncio.sleep(delay_s)
        await ctx.warning("Encountered a minor issue, continuing...")
        await asyncio.sleep(delay_s)
        await ctx.debug("Processing step 4")
        await asyncio.sleep(delay_s)
        await ctx.info("Operation completed successfully")
        logs_emitted = ["info", "debug", "debug", "info", "debug", "warning", "debug", "info"]

    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)

    return {
        "results": {
            "operation": operation,
            "logs_emitted": logs_emitted,
            "message": f"Successfully tested {operation} logging level(s)"
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": elapsed_ms
        }
    }


if __name__ == "__main__":
    mcp.run()
