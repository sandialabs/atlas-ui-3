"""Shared Redis connection for multi-replica deployments (issue #1091).

One ``RedisClientManager`` per API process owns a bounded async connection
pool. Every Atlas feature that needs Redis (today: the session repository)
borrows its client instead of opening its own, so the per-process connection
count stays at ``REDIS_MAX_CONNECTIONS`` no matter how many features use it.

Redis is shared coordination state, not the source of truth: conversations
and anything that must survive a Redis restart live in the chat-history
database. Losing Redis loses live session snapshots, which reconnecting
clients rebuild from that database.

Failure behavior is explicit. ``start()`` pings the server and raises when it
cannot be reached, so a replica configured for Redis never boots into a
silent process-local mode. Commands made after startup raise
``redis.exceptions.RedisError`` subclasses, which callers translate into
domain errors; there is no fallback store.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

logger = logging.getLogger(__name__)


_SECRET_QUERY_KEYS = frozenset({"password", "username", "ssl_password"})


def redact_redis_url(url: str) -> str:
    """Return ``url`` with credentials masked, for logs and health output.

    Covers the password in the authority (``redis://user:pw@host``) and the
    query-string forms redis-py also accepts (``?password=...``).
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<unparseable REDIS_URL>"
    netloc = parts.netloc
    if parts.password is not None:
        host = parts.hostname or ""
        if parts.port:
            host = f"{host}:{parts.port}"
        user = parts.username or ""
        netloc = f"{user}:***@{host}" if user else f":***@{host}"
    query = parts.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        query = urlencode(
            [(k, "***" if k.lower() in _SECRET_QUERY_KEYS else v) for k, v in pairs],
            safe="*",
        )
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))


@dataclass(frozen=True)
class RedisSettings:
    """Connection settings, built from ``AppSettings`` by ``from_app_settings``."""

    url: str
    key_prefix: str = "atlas"
    max_connections: int = 20
    pool_timeout: float = 5.0
    socket_timeout: float = 5.0
    connect_timeout: float = 5.0
    health_check_interval: int = 30
    tls_ca_certs: Optional[str] = None

    @classmethod
    def from_app_settings(cls, app_settings: Any) -> "RedisSettings":
        url = (getattr(app_settings, "redis_url", None) or "").strip()
        if not url:
            raise ValueError(
                "REDIS_URL is required when SESSION_REPOSITORY_TYPE=redis "
                "(for example redis://redis:6379/0 or rediss://user:pass@host:6380/0)"
            )
        if urlsplit(url).scheme not in ("redis", "rediss", "unix"):
            raise ValueError("REDIS_URL must start with redis://, rediss:// or unix://")
        return cls(
            url=url,
            key_prefix=app_settings.redis_key_prefix,
            max_connections=app_settings.redis_max_connections,
            pool_timeout=app_settings.redis_pool_timeout,
            socket_timeout=app_settings.redis_socket_timeout,
            connect_timeout=app_settings.redis_connect_timeout,
            health_check_interval=app_settings.redis_health_check_interval,
            tls_ca_certs=app_settings.redis_tls_ca_certs or None,
        )


@dataclass
class RedisStats:
    """Counters exposed through the health endpoint and logs."""

    command_errors: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[float] = None
    extra: Dict[str, int] = field(default_factory=dict)

    def record_error(self, exc: BaseException) -> None:
        self.command_errors += 1
        self.last_error = type(exc).__name__
        self.last_error_at = time.time()

    def incr(self, name: str, amount: int = 1) -> None:
        self.extra[name] = self.extra.get(name, 0) + amount


class RedisClientManager:
    """Owns the process-wide Redis pool: build, verify, expose, close."""

    def __init__(self, settings: RedisSettings, client: Any = None):
        """Create the manager.

        ``client`` injects a ready-made ``redis.asyncio.Redis`` (tests pass a
        fakeredis instance); otherwise one is built from ``settings`` with a
        ``BlockingConnectionPool``, so a burst beyond ``max_connections``
        waits up to ``pool_timeout`` for a free connection and then fails,
        rather than opening connections without bound.
        """
        self.settings = settings
        self.stats = RedisStats()
        self._client = client if client is not None else self._build_client(settings)
        self._started = False

    @staticmethod
    def _build_client(settings: RedisSettings) -> Any:
        import redis.asyncio as redis_asyncio

        kwargs: Dict[str, Any] = {
            "max_connections": settings.max_connections,
            "timeout": settings.pool_timeout,
            "socket_timeout": settings.socket_timeout,
            "socket_connect_timeout": settings.connect_timeout,
            "health_check_interval": settings.health_check_interval,
            "decode_responses": False,
        }
        if settings.tls_ca_certs and settings.url.startswith("rediss://"):
            kwargs["ssl_ca_certs"] = settings.tls_ca_certs
        pool = redis_asyncio.BlockingConnectionPool.from_url(settings.url, **kwargs)
        return redis_asyncio.Redis(connection_pool=pool)

    @property
    def client(self) -> Any:
        return self._client

    def key(self, *parts: str) -> str:
        """Namespace a key: ``<prefix>:<part>:<part>``."""
        return ":".join((self.settings.key_prefix, *parts))

    async def start(self) -> None:
        """Verify the server answers. Raises when it does not.

        Called from the application lifespan before the app accepts traffic.
        """
        try:
            await self._client.ping()
        except Exception as exc:
            self.stats.record_error(exc)
            raise RuntimeError(
                f"Redis at {redact_redis_url(self.settings.url)} is unreachable "
                f"({type(exc).__name__}). SESSION_REPOSITORY_TYPE=redis requires it; "
                "fix REDIS_URL or the network path, or use SESSION_REPOSITORY_TYPE=memory "
                "for a single-instance deployment."
            ) from exc
        self._started = True
        logger.info(
            "Redis connected: %s (prefix=%r, max_connections=%d, pool_timeout=%.1fs, "
            "socket_timeout=%.1fs)",
            redact_redis_url(self.settings.url),
            self.settings.key_prefix,
            self.settings.max_connections,
            self.settings.pool_timeout,
            self.settings.socket_timeout,
        )

    async def close(self) -> None:
        """Close the pool. Safe to call more than once and after a failed start."""
        try:
            aclose = getattr(self._client, "aclose", None) or getattr(self._client, "close")
            await aclose()
            pool = getattr(self._client, "connection_pool", None)
            if pool is not None:
                await pool.disconnect()
        except Exception as exc:  # pragma: no cover - shutdown must not raise
            logger.warning("Error closing Redis pool: %s", type(exc).__name__)
        self._started = False

    async def health(self, timeout: float = 1.0) -> Dict[str, Any]:
        """Report reachability, latency and error counters (never raises).

        The ping is bounded by ``timeout`` so a black-holed Redis cannot hold
        a health probe for the full socket timeout.
        """
        report: Dict[str, Any] = {
            "url": redact_redis_url(self.settings.url),
            "key_prefix": self.settings.key_prefix,
            "max_connections": self.settings.max_connections,
            "command_errors": self.stats.command_errors,
            "last_error": self.stats.last_error,
            **self.stats.extra,
        }
        started = time.perf_counter()
        try:
            await asyncio.wait_for(self._client.ping(), timeout)
        except Exception as exc:
            self.stats.record_error(exc)
            report["command_errors"] = self.stats.command_errors
            report["last_error"] = self.stats.last_error
            report["status"] = "unavailable"
            report["error"] = type(exc).__name__
            return report
        report["status"] = "ok"
        report["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return report
