#!/usr/bin/env python3
"""
Session State Demo MCP Server using FastMCP 3.x

Demonstrates pluggable session state (ctx.get_state / ctx.set_state) —
state that persists across multiple tool calls within the same MCP session.

This server implements a simple shopping cart where items are stored in
session state and survive across separate tool invocations, proving that
session persistence works end-to-end.

FastMCP 3.x features demonstrated:
- ctx.set_state(key, value) — persist data across tool calls
- ctx.get_state(key) — retrieve persisted data
- ctx.delete_state(key) — clear persisted data
- Session-scoped storage (each conversation gets its own cart)
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict

from fastmcp import Context, FastMCP

mcp = FastMCP("Session State Demo")

CART_KEY = "shopping_cart"
HISTORY_KEY = "action_history"


@mcp.tool
async def add_to_cart(item: str, quantity: int, price: float, ctx: Context) -> Dict[str, Any]:
    """Add an item to the session-persisted shopping cart.

    Demonstrates ctx.set_state() — the cart persists across tool calls
    within the same conversation session.

    Args:
        item: Name of the item to add
        quantity: Number of units
        price: Price per unit

    Returns:
        Updated cart contents with total
    """
    start = time.perf_counter()

    # Retrieve existing cart from session state
    cart = await ctx.get_state(CART_KEY) or []

    # Add the new item
    cart.append({
        "item": item,
        "quantity": quantity,
        "price": price,
        "subtotal": round(quantity * price, 2),
    })

    # Persist back to session state
    await ctx.set_state(CART_KEY, cart)

    # Track action history
    history = await ctx.get_state(HISTORY_KEY) or []
    history.append(f"Added {quantity}x {item} @ ${price:.2f}")
    await ctx.set_state(HISTORY_KEY, history)

    total = sum(entry["subtotal"] for entry in cart)

    return {
        "results": {
            "action": "added",
            "item": item,
            "quantity": quantity,
            "price": price,
            "cart_items": len(cart),
            "cart_total": round(total, 2),
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
            "state_keys_used": [CART_KEY, HISTORY_KEY],
        },
    }


@mcp.tool
async def view_cart(ctx: Context) -> Dict[str, Any]:
    """View the current shopping cart contents.

    Demonstrates ctx.get_state() — retrieves data persisted by previous
    tool calls in this session.

    Returns:
        Current cart contents, item count, and total
    """
    start = time.perf_counter()

    cart = await ctx.get_state(CART_KEY) or []
    total = sum(entry["subtotal"] for entry in cart)

    return {
        "results": {
            "cart": cart,
            "item_count": len(cart),
            "total": round(total, 2),
            "empty": len(cart) == 0,
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
        },
    }


@mcp.tool
async def remove_from_cart(item: str, ctx: Context) -> Dict[str, Any]:
    """Remove an item from the cart by name.

    Demonstrates reading, modifying, and writing back session state.

    Args:
        item: Name of the item to remove (first match)

    Returns:
        Updated cart after removal
    """
    start = time.perf_counter()

    cart = await ctx.get_state(CART_KEY) or []

    # Remove first matching item
    removed = False
    new_cart = []
    for entry in cart:
        if entry["item"].lower() == item.lower() and not removed:
            removed = True
            continue
        new_cart.append(entry)

    await ctx.set_state(CART_KEY, new_cart)

    history = await ctx.get_state(HISTORY_KEY) or []
    history.append(f"Removed {item}" if removed else f"Item '{item}' not found")
    await ctx.set_state(HISTORY_KEY, history)

    total = sum(entry["subtotal"] for entry in new_cart)

    return {
        "results": {
            "action": "removed" if removed else "not_found",
            "item": item,
            "cart_items": len(new_cart),
            "cart_total": round(total, 2),
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
        },
    }


@mcp.tool
async def clear_cart(ctx: Context) -> Dict[str, Any]:
    """Clear all items from the cart.

    Demonstrates ctx.delete_state() — removes persisted session data.

    Returns:
        Confirmation of cart clearing
    """
    start = time.perf_counter()

    cart = await ctx.get_state(CART_KEY) or []
    items_cleared = len(cart)

    await ctx.delete_state(CART_KEY)

    history = await ctx.get_state(HISTORY_KEY) or []
    history.append(f"Cleared cart ({items_cleared} items)")
    await ctx.set_state(HISTORY_KEY, history)

    return {
        "results": {
            "action": "cleared",
            "items_cleared": items_cleared,
            "cart_items": 0,
            "cart_total": 0.0,
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
        },
    }


@mcp.tool
async def view_history(ctx: Context) -> Dict[str, Any]:
    """View the action history for this session.

    Shows all cart actions taken during this session, demonstrating
    that session state accumulates across multiple tool calls.

    Returns:
        List of all actions performed in this session
    """
    start = time.perf_counter()

    history = await ctx.get_state(HISTORY_KEY) or []

    return {
        "results": {
            "history": history,
            "action_count": len(history),
        },
        "meta_data": {
            "is_error": False,
            "elapsed_ms": round((time.perf_counter() - start) * 1000, 3),
        },
    }


if __name__ == "__main__":
    mcp.run(show_banner=False)
