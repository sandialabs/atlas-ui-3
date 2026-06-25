"""Per-user / per-conversation FastMCP client cache for MCPToolManager.

Keyed by (normalized_user_email, server_name, conversation_id). Keying by
conversation is required because the session manager opens persistent contexts
per (conversation, server) and FastMCP's reentrant nesting counter cannot be
shared across conversations. Includes LRU/idle eviction, the cache sweeper,
and process cleanup. Patched globals (Client, StreamableHttpTransport) are
referenced via the client module to preserve test patch targets.
"""
import asyncio
import inspect
import logging
import time
from typing import List, Optional

from fastmcp import Client

from atlas.core.user_identity import normalize_user_email
from atlas.modules.config.config_manager import resolve_env_var

logger = logging.getLogger(__name__)

_DEFAULT_USER_CLIENT_CACHE_MAX_ENTRIES = 1000
_DEFAULT_USER_CLIENT_CACHE_IDLE_TTL_SECONDS = 3600
_DEFAULT_USER_CLIENT_CACHE_SWEEP_INTERVAL_SECONDS = 300
_DEFAULT_USER_CLIENT_CACHE_IN_USE_WINDOW_SECONDS = 60
_DEFAULT_USER_CLIENT_CLOSE_TIMEOUT_SECONDS = 5.0


def _client():
    """Lazily import the client module to avoid a module-level import cycle.

    The patched globals (``config_manager`` / ``Client`` /
    ``StreamableHttpTransport``) live on the client module; resolving them at
    call time keeps ``@patch('atlas.modules.mcp_tools.client.<name>')`` working
    regardless of which module the calling method now lives in.
    """
    from atlas.modules.mcp_tools import client
    return client


class UserClientMixin:
    """Per-user/per-conversation client cache lifecycle and teardown."""

    def _is_http_server(self, server_name: str) -> bool:
        """Check if a server uses HTTP/SSE transport (not STDIO)."""
        config = self.servers_config.get(server_name, {})
        transport = self._determine_transport_type(config)
        return transport in ("http", "sse")

    def _requires_user_auth(self, server_name: str) -> bool:
        """Check if a server requires per-user authentication.

        Returns True for servers with auth_type 'oauth', 'jwt', 'bearer', or 'api_key'.
        These servers need user-specific tokens rather than shared/admin tokens.
        """
        config = self.servers_config.get(server_name, {})
        auth_type = config.get("auth_type", "none")
        return auth_type in ("oauth", "jwt", "bearer", "api_key")

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

    async def _get_user_client(
        self,
        server_name: str,
        user_email: str,
        conversation_id: Optional[str] = None,
    ) -> Optional[Client]:
        """Get or create a user-specific client for servers requiring per-user auth.

        Args:
            server_name: Name of the MCP server
            user_email: User's email address
            conversation_id: Conversation scope for the cached client. Each
                conversation gets its own FastMCP Client so that persistent
                MCP sessions opened by ``MCPSessionManager`` don't share
                FastMCP's reentrant nesting counter across conversations.

        Returns:
            FastMCP Client configured with user's token, or None if no token available
        """
        from atlas.modules.mcp_tools.token_storage import get_token_storage

        token_storage = get_token_storage()
        cache_key = (normalize_user_email(user_email), server_name, conversation_id)

        # Check cache first, but validate token is still valid
        removed = []
        async with self._user_clients_lock:
            if cache_key in self._user_clients:
                # Verify the token is still valid before returning cached client
                stored_token = token_storage.get_valid_token(user_email, server_name)
                if stored_token is not None:
                    self._touch_user_client_locked(cache_key)
                    return self._user_clients[cache_key]
                else:
                    # Token expired or removed, invalidate cached client
                    logger.debug(
                        f"Token expired for user on server '{server_name}', "
                        f"invalidating cached client"
                    )
                    removed = self._pop_user_client_entries_locked([cache_key])

        await self._close_user_client_entries(removed)

        # Get user's token from storage
        logger.debug(f"[AUTH] Looking up token for server='{server_name}'")
        stored_token = token_storage.get_valid_token(user_email, server_name)
        logger.debug(f"[AUTH] Token found: {stored_token is not None}")

        if stored_token is None:
            logger.debug(
                f"[AUTH] No valid token for server '{server_name}' - user needs to authenticate"
            )
            return None

        # Get server config
        config = self.servers_config.get(server_name, {})
        url = config.get("url")

        if not url:
            logger.error(f"No URL configured for server '{server_name}'")
            return None

        # Ensure URL has protocol
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"

        # Create client with user's token
        try:
            log_handler = self._create_log_handler(server_name)
            auth_type = config.get("auth_type", "bearer")

            # For API key auth, use custom header; for bearer/jwt/oauth, use auth parameter
            if auth_type == "api_key":
                # Use custom header for API key authentication
                auth_header = config.get("auth_header", "X-API-Key")
                logger.debug(
                    f"Creating API key client for '{server_name}' with header '{auth_header}'"
                )
                transport = _client().StreamableHttpTransport(
                    url,
                    headers={auth_header: stored_token.token_value},
                )
                client = _client().Client(
                    transport=transport,
                    log_handler=log_handler,
                    elicitation_handler=self._create_elicitation_handler(server_name),
                    sampling_handler=self._create_sampling_handler(server_name),
                )
            else:
                # FastMCP Client accepts auth= as a string (bearer token)
                client = _client().Client(
                    url,
                    auth=stored_token.token_value,
                    log_handler=log_handler,
                    elicitation_handler=self._create_elicitation_handler(server_name),
                    sampling_handler=self._create_sampling_handler(server_name),
                )

            # Cache the client (re-check to avoid duplicate creation race)
            close_created_client = False
            evicted = []
            async with self._user_clients_lock:
                if cache_key in self._user_clients:
                    # Another coroutine created it while we were building ours
                    self._touch_user_client_locked(cache_key)
                    close_created_client = True
                    cached_client = self._user_clients[cache_key]
                else:
                    self._user_clients[cache_key] = client
                    self._touch_user_client_locked(cache_key)
                    evicted = self._enforce_user_client_cache_limit_locked()
                    cached_client = client

            if close_created_client:
                await self._close_user_client_entry(cache_key, client, release_session=False)
            await self._close_user_client_entries(evicted)

            logger.debug(
                f"Created user-specific client for server '{server_name}' (auth_type={auth_type})"
            )
            return cached_client

        except Exception as e:
            logger.error(
                f"Failed to create user client for server '{server_name}': {e}"
            )
            return None

    async def _invalidate_user_client(self, user_email: str, server_name: str) -> None:
        """Remove all cached clients for a user/server (e.g., when token is revoked).

        Removes every entry matching ``(user_email, server_name, *)`` across
        all conversations, since the cache key now includes conversation_id.

        After evicting cache entries (and releasing their sessions via
        ``_close_user_client_entries``), also calls
        ``MCPSessionManager.release_sessions_for_user_server`` to close any
        live sessions that outlived their cache entry — for example when the
        LRU sweeper had already popped a cache entry but its async close-task
        had not yet executed at revocation time.
        """
        user_lc = normalize_user_email(user_email)
        async with self._user_clients_lock:
            keys_to_remove = [
                k for k in self._user_clients
                if k[0] == user_lc and k[1] == server_name
            ]
            removed = self._pop_user_client_entries_locked(keys_to_remove)
            if keys_to_remove:
                logger.debug(
                    "Invalidated %d user client cache entry(s) for server '%s'",
                    len(keys_to_remove), server_name,
                )
        await self._close_user_client_entries(removed)
        # Belt-and-suspenders: release any sessions that are still alive in
        # MCPSessionManager but have no corresponding _user_clients entry.
        await self._session_manager.release_sessions_for_user_server(
            user_lc, server_name
        )

    async def _get_or_create_user_http_client(
        self,
        server_name: str,
        user_email: str,
        conversation_id: str,
    ) -> Client:
        """Get or create a per-user/per-conversation HTTP client for session isolation.

        Unlike _get_user_client (which requires auth tokens), this creates
        plain HTTP clients. Each (user, server, conversation) gets its own
        FastMCP Client and therefore its own MCP session ID.

        Args:
            server_name: Name of the MCP server
            user_email: User's email address
            conversation_id: Conversation scope for the client. Required:
                a None value would collapse every caller for the same
                (user, server) into a single shared cache slot, recreating
                the cross-conversation aliasing bug this caching layer
                exists to prevent.

        Returns:
            FastMCP Client instance for this (user, server, conversation) tuple
        """
        if not conversation_id:
            raise ValueError(
                "conversation_id is required for per-user HTTP client cache "
                "(falsy values would alias unrelated conversations together)"
            )
        cache_key = (normalize_user_email(user_email), server_name, conversation_id)

        async with self._user_clients_lock:
            if cache_key in self._user_clients:
                self._touch_user_client_locked(cache_key)
                return self._user_clients[cache_key]

            config = self.servers_config.get(server_name, {})
            url = config.get("url", "")
            if not url.startswith(("http://", "https://")):
                url = f"http://{url}"

            # Resolve admin auth token if configured (not per-user, just server-level)
            raw_token = config.get("auth_token")
            try:
                token = resolve_env_var(raw_token)
            except ValueError:
                token = None

            log_handler = self._create_log_handler(server_name)
            client = _client().Client(
                url,
                auth=token,
                log_handler=log_handler,
                elicitation_handler=self._create_elicitation_handler(server_name),
                sampling_handler=self._create_sampling_handler(server_name),
            )

            self._user_clients[cache_key] = client
            self._touch_user_client_locked(cache_key)
            evicted = self._enforce_user_client_cache_limit_locked()

        await self._close_user_client_entries(evicted)
        logger.debug(f"Created per-user HTTP client for server '{server_name}'")
        return client

    async def release_sessions(self, conversation_id: str, user_email: str | None = None) -> None:
        """Release all MCP sessions for a conversation.

        Call this on WebSocket disconnect or conversation restore to clean up
        persistent sessions held by the session manager. Also evicts cached
        FastMCP clients scoped to this (user, conversation) so the next
        conversation on the same user gets fresh clients.
        """
        await self._session_manager.release_all(
            conversation_id, user_email=user_email
        )

        # Evict cached clients scoped to this conversation. PR #565 narrowed
        # the cache key to (user, server, conversation), so per-conversation
        # eviction is now safe to scope by user too. When user_email is
        # missing we fall back to conversation_id-only eviction (e.g. internal
        # callers without auth context) and rely on the upstream
        # release_all/session-manager teardown above to bound any cross-user
        # exposure. Closing uses PR #564's pop-then-close pattern so each
        # FastMCP client is properly torn down.
        user_lc = normalize_user_email(user_email) if user_email else None
        async with self._user_clients_lock:
            keys_to_remove = [
                k for k in self._user_clients
                if k[2] == conversation_id and (user_lc is None or k[0] == user_lc)
            ]
            removed = self._pop_user_client_entries_locked(keys_to_remove)
            if keys_to_remove:
                logger.debug(
                    "Evicted %d per-conversation HTTP client(s) for conversation=%s",
                    len(keys_to_remove), conversation_id,
                )

        await self._close_user_client_entries(removed, release_session=False)

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
