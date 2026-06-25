"""Per-user FastMCP client cache machinery for MCPToolManager.

The bookkeeping behind the per-user/per-conversation client cache: LRU/idle
eviction, the background sweeper, bounded close helpers, and process cleanup.
Split out of mcp_user_clients.py (which keeps client acquisition/auth) to stay
within the per-module size guideline. No patched globals are used here, so this
module deliberately avoids importing the client module.
"""
import asyncio
import inspect
import logging
import time
from typing import List

from fastmcp import Client

logger = logging.getLogger(__name__)

_DEFAULT_USER_CLIENT_CACHE_MAX_ENTRIES = 1000
_DEFAULT_USER_CLIENT_CACHE_IDLE_TTL_SECONDS = 3600
_DEFAULT_USER_CLIENT_CACHE_SWEEP_INTERVAL_SECONDS = 300
_DEFAULT_USER_CLIENT_CACHE_IN_USE_WINDOW_SECONDS = 60
_DEFAULT_USER_CLIENT_CLOSE_TIMEOUT_SECONDS = 5.0


class UserClientCacheMixin:
    """LRU/idle eviction, the cache sweeper, close helpers, and cleanup()."""

    def _ensure_user_client_cache_state(self) -> None:
        """Initialize cache bookkeeping for tests that bypass __init__."""
        if not hasattr(self, "_user_client_last_used"):
            self._user_client_last_used = {}
        if not hasattr(self, "_user_client_cache_max_entries"):
            self._user_client_cache_max_entries = _DEFAULT_USER_CLIENT_CACHE_MAX_ENTRIES
        if not hasattr(self, "_user_client_cache_idle_ttl_seconds"):
            self._user_client_cache_idle_ttl_seconds = _DEFAULT_USER_CLIENT_CACHE_IDLE_TTL_SECONDS
        if not hasattr(self, "_user_client_cache_sweep_interval_seconds"):
            self._user_client_cache_sweep_interval_seconds = _DEFAULT_USER_CLIENT_CACHE_SWEEP_INTERVAL_SECONDS
        if not hasattr(self, "_user_client_cache_in_use_window_seconds"):
            self._user_client_cache_in_use_window_seconds = _DEFAULT_USER_CLIENT_CACHE_IN_USE_WINDOW_SECONDS
        if not hasattr(self, "_user_client_close_timeout_seconds"):
            self._user_client_close_timeout_seconds = _DEFAULT_USER_CLIENT_CLOSE_TIMEOUT_SECONDS
        if not hasattr(self, "_user_client_sweeper_task"):
            self._user_client_sweeper_task = None
        if not hasattr(self, "_user_client_close_tasks"):
            self._user_client_close_tasks = set()
        if not hasattr(self, "_wormhole_client_subtokens"):
            self._wormhole_client_subtokens = {}

    def _touch_user_client_locked(self, cache_key: tuple) -> None:
        """Mark a cached per-user client as recently used.

        Caller must hold _user_clients_lock. Uses ``time.monotonic`` so wall
        clock jumps (NTP, DST) cannot make entries appear arbitrarily fresh
        or stale to the idle sweeper.
        """
        self._ensure_user_client_cache_state()
        self._user_client_last_used[cache_key] = time.monotonic()

    def _pop_user_client_entries_locked(self, keys: List[tuple]) -> List[tuple[tuple, Client]]:
        """Remove cache entries and return clients that need closing.

        Caller must hold _user_clients_lock.
        """
        self._ensure_user_client_cache_state()
        removed = []
        for key in keys:
            client = self._user_clients.pop(key, None)
            self._user_client_last_used.pop(key, None)
            self._wormhole_client_subtokens.pop(key, None)
            if client is not None:
                removed.append((key, client))
        return removed

    def _enforce_user_client_cache_limit_locked(self) -> List[tuple[tuple, Client]]:
        """Evict least-recently-used clients until the cache is within bounds.

        Skips entries touched within ``_user_client_cache_in_use_window_seconds``
        so a tool call still holding the Client reference does not have its
        connection torn down by the cache-bound enforcer. If every entry is
        in-use we accept temporary cache overflow and let the next sweep
        catch up; that is safer than evicting an active client.

        Caller must hold _user_clients_lock.
        """
        self._ensure_user_client_cache_state()
        excess = len(self._user_clients) - self._user_client_cache_max_entries
        if excess <= 0:
            return []

        now = time.monotonic()
        in_use_window = self._user_client_cache_in_use_window_seconds

        keys_by_age = sorted(
            self._user_clients,
            key=lambda key: self._user_client_last_used.get(key, 0),
        )

        evictable = [
            key for key in keys_by_age
            if (now - self._user_client_last_used.get(key, 0.0)) > in_use_window
        ]

        if not evictable:
            logger.debug(
                "MCP user-client cache over bound by %d but every entry is in-use; "
                "deferring eviction to next sweep",
                excess,
            )
            return []

        return self._pop_user_client_entries_locked(evictable[:excess])

    async def _close_user_client_entry(
        self,
        cache_key: tuple,
        client: Client,
        *,
        release_session: bool = True,
    ) -> None:
        """Close one cached FastMCP client and optionally its persistent session.

        The persistent MCP session (if any) is owned by ``MCPSessionManager``;
        ``release_session`` drives that teardown. Calling ``client.__aexit__``
        directly is safe because FastMCP's ``_disconnect`` clamps the nesting
        counter at 0 and no-ops when ``session_task is None``, which is the
        steady state for a cached-but-idle client.

        Bounded by ``_user_client_close_timeout_seconds`` so a stuck upstream
        cannot block the sweeper or shutdown.

        IMPORTANT: only safe for *idle* cached clients. Callers above the
        cache (LRU/idle/release paths) are responsible for not handing this
        entry to a new in-flight tool call. ``_enforce_user_client_cache_limit_locked``
        skips recently-touched entries to honour that invariant.
        """
        self._ensure_user_client_cache_state()
        if release_session and cache_key[2]:
            # cache_key shape is (normalized_user_email, server_name, conversation_id).
            # Pass user_email so release() targets the correct user-scoped session
            # entry; without it the session was acquired under one scope and would
            # be looked up under "" — leaking ManagedSession entries in _sessions.
            try:
                await self._session_manager.release(
                    cache_key[2], cache_key[1], user_email=cache_key[0]
                )
            except Exception as e:
                logger.debug("Error releasing MCP session for evicted client %s: %s", cache_key, e)

        close = getattr(client, "__aexit__", None)
        if close is None:
            return

        try:
            result = close(None, None, None)
            if inspect.isawaitable(result):
                await asyncio.wait_for(
                    result, timeout=self._user_client_close_timeout_seconds
                )
        except asyncio.TimeoutError:
            logger.warning(
                "Timed out closing cached MCP client %s after %.1fs",
                cache_key,
                self._user_client_close_timeout_seconds,
            )
        except Exception as e:
            logger.debug("Error closing cached MCP client %s: %s", cache_key, e)

    async def _close_user_client_entries(
        self,
        entries: List[tuple[tuple, Client]],
        *,
        release_session: bool = True,
    ) -> None:
        for cache_key, client in entries:
            await self._close_user_client_entry(
                cache_key,
                client,
                release_session=release_session,
            )

    async def start_user_client_cache_sweeper(self) -> None:
        """Start periodic idle eviction for cached per-user MCP clients."""
        if self._user_client_sweeper_task and not self._user_client_sweeper_task.done():
            return
        self._user_client_sweeper_task = asyncio.create_task(
            self._user_client_cache_sweeper(),
            name="mcp-user-client-cache-sweeper",
        )

    async def stop_user_client_cache_sweeper(self) -> None:
        """Stop the cached per-user MCP client idle eviction task."""
        task = self._user_client_sweeper_task
        self._user_client_sweeper_task = None
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # Expected: we just cancelled the sweeper; swallow the
                # propagated CancelledError so callers (e.g. shutdown) see
                # a clean stop instead of the cancellation re-raising.
                pass

    async def _user_client_cache_sweeper(self) -> None:
        current_task = asyncio.current_task()
        while self._user_client_sweeper_task is current_task:
            try:
                await asyncio.sleep(self._user_client_cache_sweep_interval_seconds)
                await self._sweep_idle_user_clients_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MCP user client cache sweeper iteration failed")

    async def _sweep_idle_user_clients_once(self) -> int:
        """Evict cached clients idle longer than the configured TTL.

        Close work runs in a tracked subtask shielded from the sweeper's
        own cancellation so that ``cleanup() -> stop_user_client_cache_sweeper()``
        cannot orphan FastMCP clients that have already been popped from
        the cache but not yet closed. ``cleanup()`` drains
        ``_user_client_close_tasks`` after stopping the sweeper.
        """
        self._ensure_user_client_cache_state()
        cutoff = time.monotonic() - self._user_client_cache_idle_ttl_seconds
        async with self._user_clients_lock:
            keys_to_remove = [
                key for key in self._user_clients
                if self._user_client_last_used.get(key, 0) <= cutoff
            ]
            removed = self._pop_user_client_entries_locked(keys_to_remove)

        if not removed:
            return 0

        close_task = asyncio.create_task(
            self._close_user_client_entries(removed),
            name="mcp-user-client-close-batch",
        )
        self._user_client_close_tasks.add(close_task)
        close_task.add_done_callback(self._user_client_close_tasks.discard)

        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            # Cancellation propagated from the sweeper; the shielded
            # close_task continues independently. cleanup() will await it
            # via the _user_client_close_tasks set.
            raise

        logger.debug("Evicted %d idle per-user MCP HTTP client(s)", len(removed))
        return len(removed)


    async def cleanup(self):
        """Cleanup all clients, persistent sessions, and per-user HTTP client cache."""
        logger.info("Cleaning up MCP clients")

        self._ensure_user_client_cache_state()
        await self.stop_user_client_cache_sweeper()

        # Drain any close batches the sweeper had in flight when we
        # cancelled it. These already had their entries popped from the
        # cache, so without the drain the FastMCP clients would leak.
        pending_closes = list(self._user_client_close_tasks)
        if pending_closes:
            await asyncio.gather(*pending_closes, return_exceptions=True)

        # Close all persistent sessions. Keys are
        # (user_email, conversation_id, server_name) — pass each component to release()
        # so the correct scope is targeted.
        for key in list(self._session_manager._sessions.keys()):
            try:
                user_scope, conv_id, server = key
                await self._session_manager.release(
                    conv_id, server, user_email=user_scope
                )
            except Exception as e:
                logger.debug("Error releasing session %s: %s", key, e)

        # Close and clear per-user HTTP client cache
        async with self._user_clients_lock:
            keys_to_remove = list(self._user_clients)
            removed = self._pop_user_client_entries_locked(keys_to_remove)

        await self._close_user_client_entries(removed, release_session=False)
        if removed:
            logger.debug("Closed and cleared %d per-user HTTP client(s)", len(removed))
