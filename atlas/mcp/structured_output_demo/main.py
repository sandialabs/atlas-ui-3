#!/usr/bin/env python3
"""
Structured Output Demo MCP Server using FastMCP 3.x

Demonstrates the structured output priority feature where the backend
prioritizes the `data` field over `structured_content` in tool results.

This server returns results using the `data` field (FastMCP 3.x preferred)
so the UI can display clean, formatted output instead of raw JSON blobs.

FastMCP 3.x features demonstrated:
- `data` field in tool results (higher priority than structured_content)
- Clean structured data display in the UI
- Various data shapes (tables, key-value, lists, nested objects)
"""

from __future__ import annotations

import time
from typing import Any, Dict

from atlas.mcp_shared.server_factory import create_stdio_server

mcp = create_stdio_server("Structured Output Demo")


@mcp.tool
def weather_report(city: str) -> Dict[str, Any]:
    """Get a mock weather report demonstrating structured data output.

    Returns structured data using the `data` field, which FastMCP 3.x
    prioritizes over `structured_content` for cleaner UI display.

    Args:
        city: City name for the weather report

    Returns:
        Structured weather data with the `data` field
    """
    start = time.perf_counter()

    # Mock weather data
    weather = {
        "city": city,
        "temperature": 72,
        "unit": "F",
        "conditions": "Partly Cloudy",
        "humidity": 45,
        "wind_speed": 12,
        "wind_direction": "NW",
        "forecast": [
            {"day": "Today", "high": 75, "low": 58, "conditions": "Partly Cloudy"},
            {"day": "Tomorrow", "high": 78, "low": 60, "conditions": "Sunny"},
            {"day": "Wednesday", "high": 70, "low": 55, "conditions": "Rain"},
        ],
    }

    return {
        "data": weather,
        "results": {
            "operation": "weather_report",
            "city": city,
            "status": "success",
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
            "source": "mock_data",
        },
    }


@mcp.tool
def system_status() -> Dict[str, Any]:
    """Get mock system status demonstrating key-value structured output.

    Returns a flat key-value structure in the `data` field, showing how
    the UI renders structured data cleanly without raw JSON.

    Returns:
        System status as structured key-value data
    """
    start = time.perf_counter()

    status = {
        "server": "atlas-prod-01",
        "uptime_hours": 720,
        "cpu_usage_percent": 34.2,
        "memory_usage_percent": 67.8,
        "disk_usage_percent": 52.1,
        "active_connections": 142,
        "requests_per_second": 1250,
        "error_rate_percent": 0.02,
        "last_deployment": "2026-03-12T14:30:00Z",
        "health": "healthy",
    }

    return {
        "data": status,
        "results": {
            "operation": "system_status",
            "status": "success",
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
        },
    }


@mcp.tool
def compare_items(item_a: str, item_b: str) -> Dict[str, Any]:
    """Compare two items demonstrating tabular structured output.

    Returns a comparison table structure in the `data` field, showing how
    nested structured data gets displayed in the UI.

    Args:
        item_a: First item to compare
        item_b: Second item to compare

    Returns:
        Side-by-side comparison as structured data
    """
    start = time.perf_counter()

    comparison = {
        "items": [item_a, item_b],
        "comparison": {
            "price": {item_a: "$29.99", item_b: "$34.99"},
            "rating": {item_a: 4.5, item_b: 4.2},
            "reviews": {item_a: 1250, item_b: 890},
            "in_stock": {item_a: True, item_b: True},
            "shipping": {item_a: "Free", item_b: "$4.99"},
        },
        "recommendation": item_a,
        "reason": f"{item_a} has better price and higher rating",
    }

    return {
        "data": comparison,
        "results": {
            "operation": "compare_items",
            "items_compared": 2,
            "status": "success",
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
        },
    }


@mcp.tool
def user_profile(username: str) -> Dict[str, Any]:
    """Get a mock user profile demonstrating nested structured output.

    Returns deeply nested structured data in the `data` field to verify
    the UI handles complex data hierarchies cleanly.

    Args:
        username: Username to look up

    Returns:
        User profile as nested structured data
    """
    start = time.perf_counter()

    profile = {
        "username": username,
        "display_name": username.title(),
        "email": f"{username}@example.com",
        "role": "developer",
        "stats": {
            "projects": 12,
            "commits_this_month": 47,
            "pull_requests_open": 3,
            "code_reviews_done": 15,
        },
        "skills": ["Python", "TypeScript", "Go", "SQL"],
        "recent_activity": [
            {"type": "commit", "repo": "atlas-ui", "message": "Fix login bug", "time": "2h ago"},
            {"type": "review", "repo": "api-gateway", "message": "Approved PR #42", "time": "5h ago"},
            {"type": "issue", "repo": "atlas-ui", "message": "Report perf regression", "time": "1d ago"},
        ],
    }

    return {
        "data": profile,
        "results": {
            "operation": "user_profile",
            "username": username,
            "status": "success",
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
        },
    }


if __name__ == "__main__":
    mcp.run(show_banner=False)
