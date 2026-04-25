"""MCP Session Manager — holds live sessions per (user, conversation, server).

Sessions are opened lazily on first tool call and reused across subsequent calls
within the same conversation. Cleanup happens on conversation end (WebSocket
disconnect) or when a conversation is restored/reset.
"""
import asyncio
import logging
from typing import Any, Dict, Optional, Protocol, Tuple

from fastmcp import Client

from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)


class SessionStore(Protocol):
    """Abstract storage interface for session metadata.

    In-memory dict is the default. Swap to Redis for durable sessions
    that survive restarts (future phase).
    """

    def get(self, key: Tuple[str, str, str]) -> Optional[Any]: ...
    def set(self, key: Tuple[str, str, str], value: Any) -> None: ...
    def delete(self, key: Tuple[str, str, str]) -> None: ...
    def keys_by_prefix(self, prefix: str) -> list: ...


class ManagedSession:
    """Wraps an open FastMCP client context for reuse across tool calls."""

    def __init__(self, client: Client):
        self._client = client
        self._opened = False
        self._closed = False

    @property
    def client(self) -> Client:
        return self._client

    @property
    def is_open(self) -> bool:
        """Check if session is open and the underlying transport is still alive."""
        if not self._opened or self._closed:
            return False
        # Detect server-side disconnects that our flags don't know about
        if not self._client.is_connected():
            return False
        return True

    async def open(self) -> None:
        if self._opened:
            return
        self._opened = True
        await self._client.__aenter__()

    async def close(self) -> None:
        if self._opened and not self._closed:
            self._closed = True
            try:
                await self._client.__aexit__(None, None, None)
            except Exception as e:
                logger.debug("Error closing MCP session: %s", e)


class MCPSessionManager:
    """Manages live MCP sessions keyed by (user_email, conversation_id, server_name).

    Sessions are created lazily and reused. Call release_all() on
    conversation end to clean up.
    """

    def __init__(self) -> None:
        self._sessions: Dict[Tuple[str, str, str], ManagedSession] = {}
        self._lock = asyncio.Lock()
        self._key_locks: Dict[Tuple[str, str, str], asyncio.Lock] = {}
        # Reverse index: conversation_id → set of (user_email, conversation_id, server_name) keys
        self._conv_index: Dict[str, set] = {}

    async def acquire(
        self,
        conversation_id: str,
        server_name: str,
        client: Client,
        user_email: Optional[str] = None,
    ) -> ManagedSession:
        """Get or create a session for (user_email, conversation_id, server_name).

        If a session already exists and is open, returns it.
        Otherwise opens a new one.  Uses per-key locks so that opening
        a session for one server doesn't block other servers.

        Dead sessions (server process crashed) are detected via
        ``client.is_connected()`` and automatically cleaned up before
        opening a fresh session.
        """
        user_scope = normalize_user_email(user_email)
        key = (user_scope, conversation_id, server_name)

        # Fast path: check under global lock (no I/O)
        async with self._lock:
            existing = self._sessions.get(key)
            if existing is not None and existing.is_open:
                return existing
            # Get or create a per-key lock
            if key not in self._key_locks:
                self._key_locks[key] = asyncio.Lock()
            key_lock = self._key_locks[key]

        # Slow path: open session under per-key lock (network I/O)
        async with key_lock:
            # Re-check after acquiring per-key lock
            dead_session: Optional[ManagedSession] = None
            async with self._lock:
                existing = self._sessions.get(key)
                if existing is not None and existing.is_open:
                    return existing
                # If the session exists but is no longer open (server died),
                # remove it so we can clean up and reconnect.
                if existing is not None:
                    dead_session = self._sessions.pop(key, None)

            # Clean up the dead session outside the lock.  This calls
            # client.__aexit__ which resets FastMCP's internal nesting
            # counter and session task — required before __aenter__ can
            # start a fresh connection.
            if dead_session is not None:
                logger.info(
                    "Evicting dead MCP session for conversation=%s server=%s",
                    conversation_id,
                    server_name,
                )
                await dead_session.close()

            session = ManagedSession(client)
            await session.open()

            async with self._lock:
                self._sessions[key] = session
                self._conv_index.setdefault(conversation_id, set()).add(key)

            logger.debug(
                "Opened MCP session for conversation=%s server=%s",
                conversation_id,
                server_name,
            )
            return session

    async def release(
        self,
        conversation_id: str,
        server_name: str,
        user_email: Optional[str] = None,
    ) -> None:
        """Close and remove a specific session."""
        key = (normalize_user_email(user_email), conversation_id, server_name)
        async with self._lock:
            session = self._sessions.pop(key, None)
            self._key_locks.pop(key, None)
            conv_keys = self._conv_index.get(conversation_id)
            if conv_keys is not None:
                conv_keys.discard(key)
                if not conv_keys:
                    del self._conv_index[conversation_id]
        if session is not None:
            await session.close()
            logger.debug(
                "Released MCP session for conversation=%s server=%s",
                conversation_id,
                server_name,
            )

    async def release_all(
        self, conversation_id: str, user_email: Optional[str] = None
    ) -> None:
        """Close sessions for a conversation (O(1) lookup via reverse index)."""
        to_close: list[ManagedSession] = []
        user_scope = normalize_user_email(user_email) if user_email else None
        async with self._lock:
            keys_to_remove = self._pop_release_keys_locked(
                conversation_id, user_scope
            )
            for k in keys_to_remove:
                session = self._sessions.pop(k, None)
                self._key_locks.pop(k, None)
                if session is not None:
                    to_close.append(session)

        for session in to_close:
            await session.close()

        if to_close:
            logger.info(
                "Released %d MCP session(s) for conversation=%s",
                len(to_close),
                conversation_id,
            )

    def _pop_release_keys_locked(
        self,
        conversation_id: str,
        user_scope: Optional[str],
    ) -> set[Tuple[str, str, str]]:
        """Return and remove indexed keys for a user scope, or all users."""
        conv_keys = self._conv_index.get(conversation_id)
        if not conv_keys:
            return set()

        if user_scope is None:
            self._conv_index.pop(conversation_id, None)
            return set(conv_keys)

        keys_to_remove = {k for k in conv_keys if k[0] == user_scope}
        conv_keys.difference_update(keys_to_remove)
        if not conv_keys:
            self._conv_index.pop(conversation_id, None)
        return keys_to_remove
