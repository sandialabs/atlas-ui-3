"""BlockedStateStore — prevents STDIO MCP servers from using session state.

STDIO servers share a single process across all users. Any state stored via
ctx.get_state/ctx.set_state would be visible to every user, which is a
security issue. This store raises RuntimeError on all operations to enforce
that stateful MCP servers must use HTTP transport with per-user sessions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, SupportsFloat

_ERROR = (
    "Session state is not supported for STDIO MCP servers. "
    "Stateful servers must use HTTP transport for per-user session isolation. "
    "See docs or atlas/mcp/session_state_demo for an HTTP example."
)


class BlockedStateStore:
    """AsyncKeyValue-compatible store that raises on every operation."""

    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        raise RuntimeError(_ERROR)

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        raise RuntimeError(_ERROR)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        raise RuntimeError(_ERROR)

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        raise RuntimeError(_ERROR)

    async def get_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[dict[str, Any] | None]:
        raise RuntimeError(_ERROR)

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: SupportsFloat | None = None,
    ) -> None:
        raise RuntimeError(_ERROR)

    async def delete_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> int:
        raise RuntimeError(_ERROR)

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        raise RuntimeError(_ERROR)
