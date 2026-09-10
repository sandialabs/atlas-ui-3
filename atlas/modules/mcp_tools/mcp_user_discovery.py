"""Per-user tool discovery for MCP servers that gate ``tools/list`` behind auth.

Process-level discovery (``DiscoveryMixin.discover_tools``) runs once at
startup with anonymous, shared clients. That is enough for a server that only
enforces authorization on tool *invocation*, but the MCP authorization spec
also permits gating ``initialize``/``tools/list``. Such a server answers the
startup sweep with ``401`` and is recorded with an empty tool list forever --
even after the user completes the OAuth flow, because nothing re-runs
discovery and the process-level client has no token to re-run it with
(issue #912).

This mixin adds the missing half: discovery performed *with the user's own
client*, cached per ``(user, server)``, refreshed when the user authorizes and
lazily on first use afterwards.

**Whose catalogue is whose.** A gated server chose to hide its tool metadata
from anonymous callers, so a catalogue obtained with one user's token is that
user's to see: ``get_visible_tools_for_server`` serves each requester their own
entry and falls back to the shared one only when it came from a real anonymous
sweep. The shared ``available_tools``/``_tool_index`` are still populated from
a per-user result -- marked ``user_scoped`` -- because ``get_tools_schema`` and
``get_server_for_tool`` are user-agnostic and nothing could be scheduled or
routed otherwise. That publication is withdrawn when the user it came from
disconnects. It never widens access either way: every call into an
auth-required server goes through ``_get_user_client`` and raises
``AuthenticationRequiredException`` for a user holding no token of their own.
"""
import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.user_identity import normalize_user_email

logger = logging.getLogger(__name__)

# How long a successful per-user discovery is reused before being re-run.
_USER_DISCOVERY_TTL_SECONDS = 300.0

# Cool-down after a failed attempt. Lazy discovery is driven by ``/api/config``,
# which the SPA polls, so an unreachable or still-unauthorized server must not
# be retried on every request.
_USER_DISCOVERY_RETRY_SECONDS = 60.0

# Ceiling on resident cache entries before expired ones are swept. Keys are
# (user, server), so this only bites on a large deployment with many auth-gated
# servers -- but "never evicts" is not a property a long-lived process should
# have.
_MAX_USER_DISCOVERY_ENTRIES = 2000


class UserDiscoveryMixin:
    """Authenticated, per-user tool discovery and its cache."""

    def _ensure_user_discovery_state(self) -> None:
        """Create the per-user discovery caches if they are not there yet.

        Written defensively rather than relying on ``__init__`` alone: tests
        and older callers build partially-initialised managers, and a missing
        attribute must not turn discovery into an AttributeError.
        """
        if not hasattr(self, "_user_available_tools"):
            self._user_available_tools: Dict[Tuple[str, str], Dict[str, Any]] = {}
        if not hasattr(self, "_user_discovery_failures"):
            self._user_discovery_failures: Dict[Tuple[str, str], float] = {}
        if not hasattr(self, "_user_discovery_locks"):
            self._user_discovery_locks: Dict[Tuple[str, str], asyncio.Lock] = {}
        if not hasattr(self, "_user_discovery_tasks"):
            self._user_discovery_tasks: set = set()

    @staticmethod
    def _user_discovery_key(user_email: str, server_name: str) -> Tuple[str, str]:
        return (normalize_user_email(user_email), server_name)

    # --- inventory queries ------------------------------------------------

    def _has_anonymous_catalogue(self, server_name: str) -> bool:
        """Whether the shared catalogue for this server came from the sweep.

        A ``user_scoped`` entry was published from one user's token and does
        not mean the server answers anonymously, so it must not stand in for
        another user's view or suppress their own discovery.
        """
        entry = self.available_tools.get(server_name) or {}
        return bool(entry.get("tools")) and not entry.get("user_scoped")

    def get_user_tools_for_server(
        self, user_email: str, server_name: str
    ) -> Optional[List[Any]]:
        """Tools this user's own credentials revealed, or None if never run."""
        self._ensure_user_discovery_state()
        entry = self._user_available_tools.get(
            self._user_discovery_key(user_email, server_name)
        )
        return None if entry is None else entry.get("tools", [])

    def get_visible_tools_for_server(
        self, user_email: str, server_name: str
    ) -> List[Any]:
        """The tools ``user_email`` may be shown for ``server_name``.

        Their own discovery wins. The shared catalogue stands in only when it
        was obtained anonymously -- a gated server's metadata, fetched with
        someone else's token, is not this user's to read.
        """
        entry = self.available_tools.get(server_name) or {}
        if not self._requires_user_auth(server_name):
            return entry.get("tools") or []
        own = self.get_user_tools_for_server(user_email, server_name)
        if own is not None:
            return own
        if entry.get("user_scoped"):
            return []
        return entry.get("tools") or []

    # --- cache maintenance ------------------------------------------------

    def clear_user_tool_cache(
        self, user_email: Optional[str] = None, server_name: Optional[str] = None
    ) -> None:
        """Drop cached per-user discovery, and unpublish what it promoted.

        Called with no arguments on config reload (every catalogue is now
        suspect), and with a user (and usually a server) when that user's token
        changes. A revoked user's tool names and schemas must not stay
        published and routable for everyone else.
        """
        self._ensure_user_discovery_state()
        user_lc = None if user_email is None else normalize_user_email(user_email)

        def matches(key) -> bool:
            if user_lc is not None and key[0] != user_lc:
                return False
            if server_name is not None and key[1] != server_name:
                return False
            return True

        cleared = [k for k in self._user_available_tools if matches(k)]
        for cache in (self._user_available_tools, self._user_discovery_failures):
            for key in [k for k in cache if matches(k)]:
                cache.pop(key, None)
        # An in-flight discovery still needs its lock; only idle ones are swept.
        for key in [
            k for k, lock in self._user_discovery_locks.items()
            if matches(k) and not lock.locked()
        ]:
            self._user_discovery_locks.pop(key, None)

        for owner, server in cleared:
            self._withdraw_promoted_tools(server, owner)

    def _withdraw_promoted_tools(self, server_name: str, user_lc: str) -> None:
        """Unpublish a shared catalogue that came from this user, if it did."""
        entry = self.available_tools.get(server_name) or {}
        if not entry.get("user_scoped") or entry.get("discovered_for") != user_lc:
            return
        self.available_tools[server_name] = {
            "tools": [],
            "config": self.servers_config.get(server_name, {}),
        }
        self._rebuild_tool_index()
        logger.info(
            "Withdrew the per-user tool catalogue published for server '%s'",
            sanitize_for_logging(server_name),
        )

    def _prune_user_discovery_state(self) -> None:
        """Drop expired entries once the caches grow past the ceiling."""
        if len(self._user_available_tools) <= _MAX_USER_DISCOVERY_ENTRIES:
            if len(self._user_discovery_failures) <= _MAX_USER_DISCOVERY_ENTRIES:
                return
        now = time.time()
        for key, entry in list(self._user_available_tools.items()):
            if (now - entry.get("discovered_at", 0.0)) >= _USER_DISCOVERY_TTL_SECONDS:
                self._user_available_tools.pop(key, None)
        for key, at in list(self._user_discovery_failures.items()):
            if (now - at) >= _USER_DISCOVERY_RETRY_SECONDS:
                self._user_discovery_failures.pop(key, None)
        for key, lock in list(self._user_discovery_locks.items()):
            if not lock.locked():
                self._user_discovery_locks.pop(key, None)

    # --- discovery --------------------------------------------------------

    async def discover_tools_for_user(
        self,
        user_email: str,
        server_name: str,
        *,
        force: bool = False,
    ) -> Optional[List[Any]]:
        """Run ``tools/list`` against one server as ``user_email``.

        Returns the discovered tools, or None when discovery was not attempted
        or did not succeed (no token, cooling down, or the server refused).
        ``force`` bypasses both the success TTL and the failure cool-down; it
        is what the OAuth callback uses, where a token has just changed.
        """
        if not user_email or not self._requires_user_auth(server_name):
            return None

        self._ensure_user_discovery_state()
        key = self._user_discovery_key(user_email, server_name)

        if not force:
            hit, cached = self._cached_user_tools(key, time.time())
            if hit:
                return cached

        # One discovery per (user, server) at a time. /api/config is polled, so
        # without this a slow server would accumulate a fresh connection per
        # in-flight request instead of the callers sharing one result.
        lock = self._user_discovery_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not force:
                # A concurrent caller may have finished while we waited.
                hit, cached = self._cached_user_tools(key, time.time())
                if hit:
                    return cached
            return await self._run_user_discovery(user_email, server_name, key)

    def _cached_user_tools(self, key, now: float):
        """(hit, tools) from the caches, honouring the TTL and the cool-down."""
        entry = self._user_available_tools.get(key)
        if entry is not None and (now - entry.get("discovered_at", 0.0)) < _USER_DISCOVERY_TTL_SECONDS:
            return True, entry.get("tools", [])
        last_failure = self._user_discovery_failures.get(key)
        if last_failure is not None and (now - last_failure) < _USER_DISCOVERY_RETRY_SECONDS:
            return True, (entry.get("tools", []) if entry is not None else None)
        return False, None

    async def _run_user_discovery(
        self, user_email: str, server_name: str, key
    ) -> Optional[List[Any]]:
        """The discovery itself, run under this (user, server)'s lock."""
        now = time.time()
        safe_server = sanitize_for_logging(server_name)
        # conversation_id is deliberately None: discovery is not part of any
        # conversation, and borrowing a conversation's client would perturb
        # the persistent session the session manager holds for it.
        client = await self._get_user_client(server_name, user_email, None)
        if client is None:
            logger.debug(
                "Per-user tool discovery skipped for '%s': user holds no token",
                safe_server,
            )
            self._user_discovery_failures[key] = now
            return None

        tools = await self._list_tools_with_client(server_name, client)
        if tools is None:
            self._user_discovery_failures[key] = now
            return None

        self._user_discovery_failures.pop(key, None)
        self._user_available_tools[key] = {
            "tools": tools,
            "config": self.servers_config.get(server_name, {}),
            "discovered_at": now,
        }
        logger.info(
            "Per-user tool discovery found %d tool(s) on server '%s'",
            len(tools),
            safe_server,
        )
        self._promote_user_tools(server_name, tools, key[0])
        self._prune_user_discovery_state()
        return tools

    async def discover_tools_for_user_servers(
        self,
        user_email: str,
        server_names: List[str],
        *,
        wait_timeout: Optional[float] = None,
    ) -> Dict[str, List[Any]]:
        """Lazily discover every auth-gated server in ``server_names`` at once.

        Servers whose catalogue came from a real anonymous sweep are skipped --
        they are already covered, and probing them per user would cost a
        connection per request for nothing. The rest go through
        ``discover_tools_for_user``, whose TTL and cool-down make a repeat call
        free; that is what lets a promoted catalogue still refresh, which a
        filter on ``available_tools`` alone would prevent forever.

        ``wait_timeout`` bounds how long the *caller* waits. On expiry the
        discovery is left running (shielded, not cancelled) so its result lands
        in the cache for the next request, rather than one slow server stalling
        every ``/api/config`` response for the full discovery timeout.
        """
        self._ensure_user_discovery_state()
        candidates = [
            name for name in server_names
            if self._requires_user_auth(name) and not self._has_anonymous_catalogue(name)
        ]
        if not candidates:
            return {}

        task = asyncio.ensure_future(self._discover_many(user_email, candidates))
        self._user_discovery_tasks.add(task)
        task.add_done_callback(self._user_discovery_tasks.discard)
        if wait_timeout is None:
            return await task
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=wait_timeout)
        except asyncio.TimeoutError:
            logger.debug(
                "Per-user tool discovery still running after %ss; continuing "
                "without it (results land in the cache for the next request)",
                wait_timeout,
            )
            return {}

    async def _discover_many(
        self, user_email: str, candidates: List[str]
    ) -> Dict[str, List[Any]]:
        """Probe each candidate concurrently; never raise at the caller."""
        results = await asyncio.gather(
            *(self.discover_tools_for_user(user_email, name) for name in candidates),
            return_exceptions=True,
        )
        discovered: Dict[str, List[Any]] = {}
        for name, result in zip(candidates, results):
            if isinstance(result, Exception):
                # Never let one broken server take down the caller (/api/config
                # builds the whole tools panel from this).
                logger.warning(
                    "Per-user tool discovery raised for server '%s': %s",
                    sanitize_for_logging(name),
                    sanitize_for_logging(str(result)),
                )
                continue
            if result:
                discovered[name] = result
        return discovered

    async def _list_tools_with_client(
        self, server_name: str, client: Any
    ) -> Optional[List[Any]]:
        """``tools/list`` over an already-built client, or None on failure.

        The timeout covers connection setup as well as the call: an auth-gated
        server that hangs on ``initialize`` is exactly the shape this feature
        exists for, and a timeout around ``list_tools`` alone would not bound it.

        Deliberately not ``_discover_tools_for_server``: that helper records a
        *global* server failure, and one user's expired token must not mark the
        server as broken for everyone or feed the reconnect backoff.
        """
        safe_server = sanitize_for_logging(server_name)
        try:
            tools = await asyncio.wait_for(
                self._open_and_list(client), timeout=self._discovery_timeout()
            )
        except Exception as exc:
            logger.warning(
                "Per-user tool discovery failed for server '%s': %s: %s",
                safe_server,
                type(exc).__name__,
                sanitize_for_logging(str(exc)),
            )
            logger.debug("Per-user discovery traceback for %s:", safe_server, exc_info=True)
            return None
        return list(tools or [])

    @staticmethod
    async def _open_and_list(client: Any) -> List[Any]:
        async with client:
            return await client.list_tools()

    def _discovery_timeout(self) -> float:
        from atlas.modules.mcp_tools import client as client_module
        return client_module.config_manager.app_settings.mcp_discovery_timeout

    def _rebuild_tool_index(self) -> None:
        from atlas.modules.mcp_tools.mcp_discovery import _build_tool_index
        self._tool_index = _build_tool_index(self.available_tools)

    def _promote_user_tools(
        self, server_name: str, tools: List[Any], user_lc: str
    ) -> None:
        """Publish a per-user catalogue into the shared inventory, if empty.

        Without this the tools stay invisible to ``get_tools_schema`` and
        ``get_server_for_tool``, so the model could neither be offered them nor
        have a call routed back to the owning server. A successful anonymous
        sweep always wins: it is the operator-visible catalogue, and one user's
        view must not overwrite it.

        The task-support metadata is applied only once promotion is accepted.
        Recording it unconditionally would purge the whole server's
        ``_tool_task_forbidden`` set and rebuild it from a possibly narrower
        per-user ``tools/list``, flipping tools the anonymous sweep had marked
        task-forbidden to task-allowed process-wide.
        """
        if not tools or self._has_anonymous_catalogue(server_name):
            return
        self._apply_task_support_metadata(server_name, tools)
        self.available_tools[server_name] = {
            "tools": tools,
            "config": self.servers_config.get(server_name, {}),
            # Marks this catalogue as one user's view, so it is not served to
            # anyone else, does not suppress their own discovery, and is
            # withdrawn when that user disconnects.
            "user_scoped": True,
            "discovered_for": user_lc,
        }
        self._rebuild_tool_index()
        logger.info(
            "Published %d per-user tool(s) for server '%s' into the shared inventory",
            len(tools),
            sanitize_for_logging(server_name),
        )
