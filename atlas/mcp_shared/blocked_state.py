"""BlockedStateStore — prevents STDIO MCP servers from storing session state.

STDIO servers share a single process across all users. Any state stored via
ctx.set_state would be visible to every user, which is a security issue.

Read operations return empty values (safe — there's no state to leak).
Write operations raise RuntimeError to enforce that stateful MCP servers
must use HTTP transport with per-user sessions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, SupportsFloat

_WRITE_ERROR = (
    "Session state writes are not supported for STDIO MCP servers. "
    "Stateful servers must use HTTP transport for per-user session isolation. "
    "See docs or atlas/mcp/session_state_demo for an HTTP example."
)


class BlockedStateStore:
    """AsyncKeyValue-compatible store that blocks writes but allows reads.

    Reads return empty values (no state stored = nothing to leak).
    Writes raise RuntimeError to prevent cross-user state sharing.
    """

    # --- Read operations: return empty (safe) ---

    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        return None

    async def get_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[dict[str, Any] | None]:
        return [None] * len(keys)

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        return (None, None)

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        return [(None, None)] * len(keys)

    # --- Delete operations: no-op (nothing to delete) ---

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        return False

    async def delete_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> int:
        return 0

    # --- Write operations: BLOCKED ---

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        raise RuntimeError(_WRITE_ERROR)

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        raise RuntimeError(_WRITE_ERROR)
