#!/usr/bin/env python3
"""
Environment Variable Demo MCP Server using FastMCP

This server demonstrates the environment variable passing capability.
It reads environment variables that are configured in mcp.json and
exposes them through MCP tools.
"""

import os
import time
from typing import Any, Dict

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Environment Variable Demo")


@mcp.tool
def get_env_var(var_name: str) -> Dict[str, Any]:
    """Get the value of a specific environment variable.

    This tool demonstrates how environment variables configured in mcp.json
    are passed to the MCP server process.

    Args:
        var_name: Name of the environment variable to retrieve

    Returns:
        MCP contract shape with the environment variable value:
        {
          "results": {
            "var_name": str,
            "var_value": str or None,
            "is_set": bool
          },
          "meta_data": {
            "elapsed_ms": float
          }
        }
    """
    start = time.perf_counter()

    var_value = os.environ.get(var_name)
    is_set = var_name in os.environ

    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)

    return {
        "results": {
            "var_name": var_name,
            "var_value": var_value,
            "is_set": is_set
        },
        "meta_data": {
            "elapsed_ms": elapsed_ms
        }
    }


@mcp.tool
def list_configured_env_vars() -> Dict[str, Any]:
    """List all environment variables that were configured in mcp.json.

    This tool shows which environment variables from the mcp.json configuration
    are available to this server. It returns commonly expected configuration
    variables that might be set.

    Returns:
        MCP contract shape with environment variables:
        {
          "results": {
            "configured_vars": dict of var_name -> var_value,
            "total_count": int
          },
          "meta_data": {
            "elapsed_ms": float
          }
        }
    """
    start = time.perf_counter()

    # List of common configuration environment variables
    # This demonstrates what might be passed from mcp.json
    common_config_vars = [
        "CLOUD_PROFILE",
        "CLOUD_REGION",
        "API_KEY",
        "DEBUG_MODE",
        "MAX_RETRIES",
        "TIMEOUT_SECONDS",
        "ENVIRONMENT",
        "SERVICE_URL"
    ]

    configured_vars = {}
    for var_name in common_config_vars:
        if var_name in os.environ:
            configured_vars[var_name] = os.environ[var_name]

    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)

    return {
        "results": {
            "configured_vars": configured_vars,
            "total_count": len(configured_vars)
        },
        "meta_data": {
            "elapsed_ms": elapsed_ms
        }
    }


@mcp.tool
def demonstrate_env_usage(operation: str = "info") -> Dict[str, Any]:
    """Demonstrate how environment variables can be used in MCP server operations.

    This tool shows practical examples of using environment variables for:
    - Configuration (e.g., region, profile)
    - Feature flags (e.g., debug mode)
    - API credentials (e.g., API keys)

    Args:
        operation: Type of demonstration ("info", "config", "credentials")

    Returns:
        MCP contract shape with demonstration results:
        {
          "results": {
            "operation": str,
            "example": str,
            "details": dict
          },
          "meta_data": {
            "elapsed_ms": float
          }
        }
    """
    start = time.perf_counter()

    if operation == "config":
        # Demonstrate configuration from environment
        cloud_profile = os.environ.get("CLOUD_PROFILE", "default")
        cloud_region = os.environ.get("CLOUD_REGION", "us-east-1")

        example = f"Using cloud profile '{cloud_profile}' in region '{cloud_region}'"
        details = {
            "profile": cloud_profile,
            "region": cloud_region,
            "source": "environment variables from mcp.json"
        }

    elif operation == "credentials":
        # Demonstrate secure credential handling
        api_key = os.environ.get("API_KEY")
        has_key = api_key is not None

        example = f"API key is {'configured' if has_key else 'not configured'}"
        details = {
            "has_api_key": has_key,
            "key_length": len(api_key) if api_key else 0,
            "masked_key": f"{api_key[:4]}...{api_key[-4:]}" if api_key and len(api_key) > 8 else None,
            "source": "environment variable ${API_KEY} from mcp.json"
        }

    else:  # info
        example = "Environment variables can be configured in mcp.json"
        details = {
            "usage": "Set env dict in mcp.json server configuration",
            "syntax": {
                "literal": "KEY: 'literal-value'",
                "substitution": "KEY: '${SYSTEM_ENV_VAR}'"
            },
            "example_config": {
                "env": {
                    "CLOUD_PROFILE": "my-profile-9",
                    "CLOUD_REGION": "us-east-7",
                    "API_KEY": "${MY_API_KEY}"
                }
            }
        }

    elapsed_ms = round((time.perf_counter() - start) * 1000, 3)

    return {
        "results": {
            "operation": operation,
            "example": example,
            "details": details
        },
        "meta_data": {
            "elapsed_ms": elapsed_ms
        }
    }


if __name__ == "__main__":
    mcp.run(show_banner=False)
