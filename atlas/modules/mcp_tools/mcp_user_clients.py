"""Per-user / per-conversation FastMCP client acquisition for MCPToolManager.

Resolves which FastMCP client a tool/prompt call should use: per-user
authenticated clients (token storage), plain per-conversation HTTP clients for
session isolation, and the Wormhole subtoken forwarding that composes with
either. The cache bookkeeping these methods drive lives in
mcp_user_client_cache.py. Patched globals (Client, StreamableHttpTransport) are
referenced via the client module to preserve test patch targets.
"""
import logging
from typing import Dict, Optional
from urllib.parse import urlsplit

from fastmcp import Client

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.user_identity import normalize_user_email
from atlas.modules.config.config_manager import resolve_env_var

logger = logging.getLogger(__name__)


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
    """Per-user/per-conversation client acquisition, auth, and Wormhole headers."""

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

    def _is_wormhole_server(self, server_name: str) -> bool:
        """Return True if a server is configured to receive the Wormhole subtoken."""
        config = self.servers_config.get(server_name, {})
        return bool(config.get("wormhole", False))

    def _current_wormhole_subtoken(
        self, server_name: str, user_email: Optional[str]
    ) -> Optional[str]:
        """Return the Wormhole subtoken to forward to a server, if applicable.

        Returns the captured subtoken when the Wormhole feature is enabled, the
        server opts in via ``wormhole: true``, and a subtoken exists for
        ``user_email``. Returns ``None`` otherwise.
        """
        if not user_email or not self._is_wormhole_server(server_name):
            return None

        app_settings = _client().config_manager.app_settings
        if not getattr(app_settings, "feature_wormhole_enabled", False):
            return None

        from atlas.modules.mcp_tools.wormhole_token_store import get_wormhole_store

        return get_wormhole_store().get_subtoken(user_email)

    def _build_wormhole_headers(
        self, server_name: str, user_email: Optional[str]
    ) -> Dict[str, str]:
        """Build the Wormhole subtoken header for a server, if applicable.

        Returns ``{forward_header: subtoken}`` (default ``{"X-Token": ...}``) when
        a subtoken should be forwarded (see :meth:`_current_wormhole_subtoken`),
        otherwise an empty dict, so callers can unconditionally merge the result
        into their transport headers.
        """
        subtoken = self._current_wormhole_subtoken(server_name, user_email)
        if not subtoken:
            if user_email and self._is_wormhole_server(server_name):
                logger.debug(
                    "Wormhole server '%s' has no subtoken for the current user; "
                    "connecting without the forward header",
                    sanitize_for_logging(server_name),
                )
            return {}

        forward_header = getattr(
            _client().config_manager.app_settings, "wormhole_forward_header", "X-Token"
        )
        # Defense-in-depth: the subtoken is a session credential. Warn (but do not
        # block — internal/loopback HPC endpoints are legitimately plaintext) when
        # it would be forwarded in cleartext to a remote host.
        self._warn_if_insecure_wormhole_url(server_name)
        logger.debug(
            "Forwarding Wormhole subtoken to server '%s' via header '%s'",
            sanitize_for_logging(server_name),
            sanitize_for_logging(forward_header),
        )
        return {forward_header: subtoken}

    @staticmethod
    def _is_loopback_host(host: str) -> bool:
        """Return True for localhost / IPv4 127.0.0.0/8 / IPv6 ::1."""
        host = (host or "").lower().strip("[]")
        return host in ("localhost", "::1") or host.startswith("127.")

    def _warn_if_insecure_wormhole_url(self, server_name: str) -> None:
        """Warn when the Wormhole subtoken would ride plaintext http:// to a
        non-loopback host.

        The subtoken is a short-lived session credential. Within a Wormhole/HPC
        deployment the server is normally reached over https (its own Wormhole
        endpoint) or on loopback, so this only fires for the genuinely risky
        case — cleartext to a remote host — and never blocks the connection.
        """
        config = self.servers_config.get(server_name, {})
        url = config.get("url", "") or ""
        # Mirror the scheme-defaulting used when the transport is built.
        parsed = urlsplit(url if "://" in url else f"http://{url}")
        if parsed.scheme != "http":
            return
        if self._is_loopback_host(parsed.hostname or ""):
            return
        logger.warning(
            "Forwarding Wormhole subtoken to server '%s' over plaintext http:// "
            "to non-loopback host '%s'; the session credential is sent in the clear. "
            "Use https:// (e.g. the server's Wormhole endpoint) or a loopback address.",
            sanitize_for_logging(server_name),
            sanitize_for_logging(parsed.hostname or ""),
        )


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

        # Resolve the current Wormhole subtoken (None for non-Wormhole servers).
        # A cached client whose baked-in subtoken differs is stale and must be
        # rebuilt even when its primary auth token is still valid.
        current_subtoken = self._current_wormhole_subtoken(server_name, user_email)

        # Check cache first, but validate token is still valid
        removed = []
        async with self._user_clients_lock:
            self._ensure_user_client_cache_state()
            if cache_key in self._user_clients:
                # Verify the token is still valid before returning cached client
                stored_token = token_storage.get_valid_token(user_email, server_name)
                subtoken_unchanged = (
                    self._wormhole_client_subtokens.get(cache_key) == current_subtoken
                )
                if stored_token is not None and subtoken_unchanged:
                    self._touch_user_client_locked(cache_key)
                    return self._user_clients[cache_key]
                else:
                    # Token expired/removed, or the Wormhole subtoken rotated;
                    # invalidate the cached client so it is rebuilt below.
                    if stored_token is not None and not subtoken_unchanged:
                        logger.debug(
                            "Wormhole subtoken changed for server '%s'; rebuilding client",
                            sanitize_for_logging(server_name),
                        )
                    else:
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

            # Forward the per-session Wormhole subtoken when this server opts in.
            # This composes with the server's primary auth: the subtoken rides as
            # an extra header alongside the API key / bearer token.
            wormhole_headers = self._build_wormhole_headers(server_name, user_email)

            # For API key auth, use custom header; for bearer/jwt/oauth, use auth parameter
            if auth_type == "api_key":
                # Use custom header for API key authentication
                auth_header = config.get("auth_header", "X-API-Key")
                logger.debug(
                    f"Creating API key client for '{server_name}' with header '{auth_header}'"
                )
                headers = {auth_header: stored_token.token_value, **wormhole_headers}
                transport = _client().StreamableHttpTransport(url, headers=headers)
                client = _client().Client(
                    transport=transport,
                    log_handler=log_handler,
                    elicitation_handler=self._create_elicitation_handler(server_name),
                    sampling_handler=self._create_sampling_handler(server_name),
                )
            elif wormhole_headers:
                # Bearer/jwt/oauth token via auth=, plus the Wormhole subtoken header.
                transport = _client().StreamableHttpTransport(url, headers=wormhole_headers)
                client = _client().Client(
                    transport=transport,
                    auth=stored_token.token_value,
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
                    if current_subtoken is not None:
                        self._wormhole_client_subtokens[cache_key] = current_subtoken
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

        # Resolve the current Wormhole subtoken (None for non-Wormhole servers).
        # If it differs from the one baked into a cached client, the cached client
        # is stale (the subtoken rotated) and must be rebuilt.
        current_subtoken = self._current_wormhole_subtoken(server_name, user_email)

        stale_removed = []
        async with self._user_clients_lock:
            self._ensure_user_client_cache_state()
            if cache_key in self._user_clients:
                cached_subtoken = self._wormhole_client_subtokens.get(cache_key)
                if cached_subtoken == current_subtoken:
                    self._touch_user_client_locked(cache_key)
                    return self._user_clients[cache_key]
                # Subtoken rotated: drop the stale client and fall through to rebuild.
                logger.debug(
                    "Wormhole subtoken changed for server '%s'; rebuilding client",
                    sanitize_for_logging(server_name),
                )
                stale_removed = self._pop_user_client_entries_locked([cache_key])

        await self._close_user_client_entries(stale_removed)

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

            # Forward the per-session Wormhole subtoken when this server opts in.
            wormhole_headers = self._build_wormhole_headers(server_name, user_email)
            if wormhole_headers:
                transport = _client().StreamableHttpTransport(url, headers=wormhole_headers)
                client = _client().Client(
                    transport=transport,
                    auth=token,
                    log_handler=log_handler,
                    elicitation_handler=self._create_elicitation_handler(server_name),
                    sampling_handler=self._create_sampling_handler(server_name),
                )
            else:
                client = _client().Client(
                    url,
                    auth=token,
                    log_handler=log_handler,
                    elicitation_handler=self._create_elicitation_handler(server_name),
                    sampling_handler=self._create_sampling_handler(server_name),
                )

            self._user_clients[cache_key] = client
            if current_subtoken is not None:
                self._wormhole_client_subtokens[cache_key] = current_subtoken
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

