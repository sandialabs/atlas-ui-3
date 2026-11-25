#!/usr/bin/env python3
"""
Session State Demo MCP Server using FastMCP 4.x

Demonstrates explicit session state - data that persists across multiple
tool calls in the same conversation.

This server implements a simple shopping cart where items are stored in a
session and survive across separate tool invocations.

MCP 2026-07-28 connections are stateless: every request is its own
connection, so the connection-scoped ``ctx.get_state`` / ``ctx.set_state``
no longer carries anything from one tool call to the next. State is kept in
an explicit session instead:

- ``SessionProvider`` adds ``create_session`` / ``end_session`` tools
- the model calls ``create_session`` once and passes the returned
  ``session_id`` to every cart tool
- ``get_session(session_id)`` resolves the session; an id that was never
  created (or belongs to another authenticated user) is rejected
- ``session.get`` / ``session.set`` / ``session.delete`` read and write it

When the server sits behind authentication, sessions are also walled off per
authenticated user. Without authentication the session id is the only
credential, so treat it as a secret.
"""

from __future__ import annotations

import time
from typing import Any, Dict

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_session
from fastmcp.server.sessions import SessionId, SessionProvider

from atlas.mcp.common.state import get_state_store

mcp = FastMCP("Session State Demo", session_state_store=get_state_store())
mcp.add_provider(SessionProvider())

CART_KEY = "shopping_cart"
HISTORY_KEY = "action_history"


@mcp.tool
async def add_to_cart(item: str, quantity: int, price: float, session_id: SessionId) -> Dict[str, Any]:
    """Add an item to the session-persisted shopping cart.

    Demonstrates session.set() - the cart persists across tool calls
    within the same conversation session.

    Args:
        item: Name of the item to add
        quantity: Number of units
        price: Price per unit

    Returns:
        Updated cart contents with total
    """
    start = time.perf_counter()
    session = await get_session(session_id)

    # Retrieve existing cart from session state
    cart = await session.get(CART_KEY) or []

    # Add the new item
    cart.append({
        "item": item,
        "quantity": quantity,
        "price": price,
        "subtotal": round(quantity * price, 2),
    })

    # Persist back to session state
    await session.set(CART_KEY, cart)

    # Track action history
    history = await session.get(HISTORY_KEY) or []
    history.append(f"Added {quantity}x {item} @ ${price:.2f}")
    await session.set(HISTORY_KEY, history)

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
async def view_cart(session_id: SessionId) -> Dict[str, Any]:
    """View the current shopping cart contents.

    Demonstrates session.get() - retrieves data persisted by previous
    tool calls in this session.

    Returns:
        Current cart contents, item count, and total
    """
    start = time.perf_counter()
    session = await get_session(session_id)

    cart = await session.get(CART_KEY) or []
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
async def remove_from_cart(item: str, session_id: SessionId) -> Dict[str, Any]:
    """Remove an item from the cart by name.

    Demonstrates reading, modifying, and writing back session state.

    Args:
        item: Name of the item to remove (first match)

    Returns:
        Updated cart after removal
    """
    start = time.perf_counter()
    session = await get_session(session_id)

    cart = await session.get(CART_KEY) or []

    # Remove first matching item
    removed = False
    new_cart = []
    for entry in cart:
        if entry["item"].lower() == item.lower() and not removed:
            removed = True
            continue
        new_cart.append(entry)

    await session.set(CART_KEY, new_cart)

    history = await session.get(HISTORY_KEY) or []
    history.append(f"Removed {item}" if removed else f"Item '{item}' not found")
    await session.set(HISTORY_KEY, history)

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
async def clear_cart(session_id: SessionId) -> Dict[str, Any]:
    """Clear all items from the cart.

    Demonstrates session.delete() - removes persisted session data.

    Returns:
        Confirmation of cart clearing
    """
    start = time.perf_counter()
    session = await get_session(session_id)

    cart = await session.get(CART_KEY) or []
    items_cleared = len(cart)

    await session.delete(CART_KEY)

    history = await session.get(HISTORY_KEY) or []
    history.append(f"Cleared cart ({items_cleared} items)")
    await session.set(HISTORY_KEY, history)

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
async def view_history(session_id: SessionId) -> Dict[str, Any]:
    """View the action history for this session.

    Shows all cart actions taken during this session, demonstrating
    that session state accumulates across multiple tool calls.

    Returns:
        List of all actions performed in this session
    """
    start = time.perf_counter()
    session = await get_session(session_id)

    history = await session.get(HISTORY_KEY) or []

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
    import os
    port = int(os.environ.get("MCP_SESSION_STATE_PORT", "8010"))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, show_banner=False)
