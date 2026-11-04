"""Live probes of the shared dependencies a replica relies on (issue #1129).

Before this module, ``/api/health`` reported the chat-history database only as
its *startup* outcome: a PostgreSQL outage after boot left every replica
reporting ``healthy`` while conversation saves failed in the logs. These
probes check reachability on demand, feed the per-replica dependency state in
``atlas.core.coordination_metrics`` (which logs ``dependency_down`` /
``dependency_recovered`` once per transition), and are bounded so a
black-holed server cannot hold a health request open.

The database probe runs ``SELECT 1`` on a worker thread (the engine is
synchronous). It is single-flight and its result is cached briefly, so a burst
of health checks during an outage costs one blocked thread, not one per request.
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional

from atlas.core.coordination_metrics import coordination_metrics

logger = logging.getLogger(__name__)

DB_PROBE_TIMEOUT_SECONDS = 1.5
DB_PROBE_CACHE_SECONDS = 2.0
# A probe still blocked after this long (a black-holed connection that TCP
# has not given up on yet) is abandoned and a fresh one started, so recovery
# is noticed within this bound. Costs at most one parked worker thread per
# interval during such an outage.
DB_PROBE_STALE_SECONDS = 15.0
# Probes run on their own small pool, not the loop's default executor, so a
# black-holed database parks at most this many threads (and pooled
# connections) and never stalls other ``to_thread`` work. While every probe
# thread is parked, no new probe starts: the database reports unavailable.
DB_PROBE_MAX_THREADS = 2
_PROBE_EXECUTOR = ThreadPoolExecutor(max_workers=DB_PROBE_MAX_THREADS, thread_name_prefix="atlas-db-probe")

# Probe state for this process: the in-flight probe future, the last cached
# result and when it was taken (monotonic). Mutated in place, never rebound.
_db_state: Dict[str, Any] = {"inflight": None, "inflight_at": 0.0, "last": None, "last_at": 0.0, "parked": set()}


def database_dependency_name(engine: Any) -> str:
    """``postgres`` for PostgreSQL, else the SQLAlchemy dialect name."""
    dialect = getattr(getattr(engine, "dialect", None), "name", "") or "database"
    return "postgres" if dialect == "postgresql" else dialect


# PostgreSQL SQLSTATE classes that mean the connection itself is gone:
# 08 connection exception, 57P0x admin/crash shutdown, cannot connect now.
_PG_CONNECTION_SQLSTATE_PREFIXES = ("08", "57P01", "57P02", "57P03")


def is_db_connectivity_error(exc: BaseException) -> bool:
    """Whether a database error means the server could not be reached.

    Statement timeouts, deadlocks, serialization failures and constraint
    violations come from a reachable server and are not an outage.
    """
    from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError

    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    if isinstance(exc, InterfaceError):
        return True
    if isinstance(exc, OperationalError):
        sqlstate = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
        # No SQLSTATE: the client failed before the server answered.
        return not sqlstate or str(sqlstate).startswith(_PG_CONNECTION_SQLSTATE_PREFIXES)
    return False


def _record_probe_outcome(name: str, future: "asyncio.Future") -> None:
    """The probe's own result drives the dependency state, not a caller's budget."""
    if future.cancelled():
        return
    metrics = coordination_metrics()
    exc = future.exception()
    if exc is None:
        metrics.dependency_ok(name)
    else:
        metrics.dependency_failed(name, exc, op="probe")


def _select_one(engine: Any) -> None:
    from sqlalchemy import text

    with engine.connect() as conn:
        if engine.dialect.name == "postgresql":
            # Server-side bound as well; transaction-scoped, so a pooled
            # connection goes back without it.
            conn.execute(text("SET LOCAL statement_timeout = 1500"))
        conn.execute(text("SELECT 1"))


async def probe_chat_history_db(
    app_factory: Any, *, timeout: float = DB_PROBE_TIMEOUT_SECONDS, use_cache: bool = True
) -> Dict[str, Any]:
    """Reachability of the chat-history database. Never raises.

    Returns ``{"dependency", "status": ok|unavailable|disabled, ...}``.
    ``disabled`` covers both a disabled feature and a failed startup (the
    startup outcome is reported separately as ``checks.chat_history``).
    """
    state = _db_state
    if getattr(app_factory, "conversation_repository", None) is None:
        return {"dependency": "chat_history", "status": "disabled"}
    from atlas.modules.chat_history import get_engine

    try:
        engine = get_engine()
    except Exception as exc:  # pragma: no cover - the engine exists once the repo does
        return {"dependency": "chat_history", "status": "unavailable", "error": type(exc).__name__}
    name = database_dependency_name(engine)
    now = time.monotonic()
    if use_cache and state["last"] is not None and now - state["last_at"] < DB_PROBE_CACHE_SECONDS:
        return dict(state["last"])

    loop = asyncio.get_running_loop()
    # A probe started on another event loop (tests create one per client)
    # cannot be awaited here; start a fresh one.
    inflight = state["inflight"]
    parked = state["parked"]
    parked.difference_update({f for f in parked if f.done()})
    metrics = coordination_metrics()
    if (
        inflight is None
        or inflight.done()
        or inflight.get_loop() is not loop
        or now - state["inflight_at"] > DB_PROBE_STALE_SECONDS
    ):
        if inflight is not None and not inflight.done():
            parked.add(inflight)
        if len(parked) >= DB_PROBE_MAX_THREADS:
            # Every probe thread is still blocked on the database.
            metrics.dependency_failed(name, "ProbeSaturated", op="probe")
            return {"dependency": name, "status": "unavailable", "error": "ProbeSaturated"}
        inflight = state["inflight"] = loop.run_in_executor(_PROBE_EXECUTOR, _select_one, engine)
        state["inflight_at"] = now
        inflight.add_done_callback(lambda fut, dep=name: _record_probe_outcome(dep, fut))
    started = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.shield(inflight), timeout)
    except asyncio.TimeoutError:
        result = {"dependency": name, "status": "unavailable", "error": "Timeout"}
        if time.monotonic() - state["inflight_at"] < DB_PROBE_TIMEOUT_SECONDS:
            # Only this caller's budget (e.g. /api/health's 0.8 s) ran out; the
            # probe is still within its own bound and will record its result.
            # Neither mark the database down nor cache the miss.
            return result
        metrics.dependency_failed(name, "Timeout", op="probe")
    except Exception as exc:
        result = {"dependency": name, "status": "unavailable", "error": type(exc).__name__}
    else:
        result = {
            "dependency": name,
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
    if use_cache:
        # A forced probe (use_cache=False) answers its caller only; it does not
        # replace what the cached path serves.
        state["last"], state["last_at"] = dict(result), time.monotonic()
    return result


async def probe_redis(app_factory: Any) -> Optional[Dict[str, Any]]:
    """``RedisClientManager.health()`` (which updates the dependency state), or None."""
    redis = getattr(app_factory, "redis", None)
    if redis is None:
        return None
    return await redis.health()


async def probe_all(app_factory: Any, *, use_cache: bool = True) -> Dict[str, Dict[str, Any]]:
    """Probe every configured dependency concurrently."""
    redis_report, db_report = await asyncio.gather(
        probe_redis(app_factory), probe_chat_history_db(app_factory, use_cache=use_cache)
    )
    out: Dict[str, Dict[str, Any]] = {}
    if redis_report is not None:
        out["redis"] = {k: redis_report[k] for k in ("status", "latency_ms", "error") if k in redis_report}
    if db_report.get("status") != "disabled":
        out[db_report["dependency"]] = {k: v for k, v in db_report.items() if k != "dependency"}
    return out


def reset_probe_cache() -> None:
    """Tests only."""
    _db_state.update(inflight=None, inflight_at=0.0, last=None, last_at=0.0, parked=set())
