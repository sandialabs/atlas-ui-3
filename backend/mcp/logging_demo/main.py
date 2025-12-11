#!/usr/bin/env python3
"""
Logging Demo MCP Server using FastMCP
Demonstrates MCP server logging at various levels.
"""

import time
from typing import Any, Dict
from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Logging Demo")


@mcp.tool
def test_logging(operation: str = "all") -> Dict[str, Any]:
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
    
    Returns:
        Dict with results and logs emitted
    """
    start = time.perf_counter()
    
    # Get the context to access logging
    ctx = mcp.get_context()
    
    logs_emitted = []
    
    if operation == "all" or operation == "debug":
        ctx.log.debug("This is a DEBUG message - detailed information for diagnostics")
        logs_emitted.append("debug")
    
    if operation == "all" or operation == "info":
        ctx.log.info("This is an INFO message - general informational message")
        logs_emitted.append("info")
    
    if operation == "all" or operation == "warning":
        ctx.log.warning("This is a WARNING message - something unexpected happened")
        logs_emitted.append("warning")
    
    if operation == "all" or operation == "error":
        ctx.log.error("This is an ERROR message - something went wrong")
        logs_emitted.append("error")
    
    if operation == "mixed":
        ctx.log.info("Starting operation...")
        ctx.log.debug("Processing step 1")
        ctx.log.debug("Processing step 2")
        ctx.log.info("Operation in progress (50% complete)")
        ctx.log.debug("Processing step 3")
        ctx.log.warning("Encountered a minor issue, continuing...")
        ctx.log.debug("Processing step 4")
        ctx.log.info("Operation completed successfully")
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
