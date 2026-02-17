#!/usr/bin/env python3
"""
Public Demo MCP Server - No Authentication Required

This server demonstrates tools that are publicly accessible without any
authentication. Use this as an example of a server that anyone can use.

Authentication Type: none
Transport: stdio, http, or sse

Updated: 2025-01-21
"""

import hashlib
import random
import time
from datetime import datetime, timezone
from typing import Any

from fastmcp import FastMCP

mcp = FastMCP("Public Demo")


@mcp.tool
def get_server_time() -> dict[str, Any]:
    """
    Get the current server time in various formats.

    This tool is publicly accessible - no authentication required.
    Useful for testing connectivity and server responsiveness.
    """
    start = time.perf_counter()
    now = datetime.now(timezone.utc)

    return {
        "results": {
            "utc_iso": now.isoformat(),
            "utc_timestamp": int(now.timestamp()),
            "formatted": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "day_of_week": now.strftime("%A"),
            "timezone": "UTC"
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "auth_required": False
        }
    }


@mcp.tool
def generate_uuid() -> dict[str, Any]:
    """
    Generate a random UUID (version 4).

    This tool is publicly accessible - no authentication required.
    Useful for generating unique identifiers.
    """
    import uuid
    start = time.perf_counter()

    new_uuid = str(uuid.uuid4())

    return {
        "results": {
            "uuid": new_uuid,
            "uuid_hex": new_uuid.replace("-", ""),
            "version": 4
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "auth_required": False
        }
    }


@mcp.tool
def hash_text(text: str, algorithm: str = "sha256") -> dict[str, Any]:
    """
    Hash text using a specified algorithm.

    This tool is publicly accessible - no authentication required.

    Args:
        text: The text to hash
        algorithm: Hash algorithm (md5, sha1, sha256, sha512). Default: sha256
    """
    start = time.perf_counter()

    valid_algorithms = ["md5", "sha1", "sha256", "sha512"]
    if algorithm.lower() not in valid_algorithms:
        return {
            "results": None,
            "meta_data": {
                "is_error": True,
                "error_message": f"Invalid algorithm. Choose from: {', '.join(valid_algorithms)}",
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                "auth_required": False
            }
        }

    hash_obj = hashlib.new(algorithm.lower())
    hash_obj.update(text.encode('utf-8'))
    hash_result = hash_obj.hexdigest()

    return {
        "results": {
            "input_text": text[:50] + "..." if len(text) > 50 else text,
            "algorithm": algorithm.lower(),
            "hash": hash_result,
            "hash_length": len(hash_result)
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "auth_required": False
        }
    }


@mcp.tool
def random_number(min_value: int = 1, max_value: int = 100) -> dict[str, Any]:
    """
    Generate a random integer within a range.

    This tool is publicly accessible - no authentication required.

    Args:
        min_value: Minimum value (inclusive). Default: 1
        max_value: Maximum value (inclusive). Default: 100
    """
    start = time.perf_counter()

    if min_value > max_value:
        return {
            "results": None,
            "meta_data": {
                "is_error": True,
                "error_message": "min_value must be less than or equal to max_value",
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                "auth_required": False
            }
        }

    result = random.randint(min_value, max_value)

    return {
        "results": {
            "number": result,
            "range": f"[{min_value}, {max_value}]"
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "auth_required": False
        }
    }


@mcp.tool
def echo(message: str) -> dict[str, Any]:
    """
    Echo back a message - simple connectivity test.

    This tool is publicly accessible - no authentication required.

    Args:
        message: The message to echo back
    """
    start = time.perf_counter()

    return {
        "results": {
            "echo": message,
            "length": len(message),
            "server": "public_demo"
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
            "auth_required": False
        }
    }


if __name__ == "__main__":
    mcp.run(show_banner=False)
