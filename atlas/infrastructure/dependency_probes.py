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
from typing import Any, Dict, Optional

from atlas.core.coordination_metrics import coordination_metrics

logger = logging.getLogger(__name__)

DB_PROBE_TIMEOUT_SECONDS = 1.5
DB_PROBE_CACHE_SECONDS = 2.0

# Probe state for this process: the in-flight probe future, the last cached
# result and when it was taken (monotonic). Mutated in place, never rebound.
_db_state: Dict[str, Any] = {"inflight": None, "last": None, "last_at": 0.0}


def database_dependency_name(engine: Any) -> str:
    """``postgres`` for PostgreSQL, else the SQLAlchemy dialect name."""
    dialect = getattr(getattr(engine, "dialect", None), "name", "") or "database"
    return "postgres" if dialect == "postgresql" else dialect


def _select_one(engine: Any) -> None:
    from sqlalchemy import text

    with engine.connect() as conn:
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
    if inflight is None or inflight.done() or inflight.get_loop() is not loop:
        inflight = state["inflight"] = loop.run_in_executor(None, _select_one, engine)
    started = time.perf_counter()
    metrics = coordination_metrics()
    try:
        await asyncio.wait_for(asyncio.shield(inflight), timeout)
    except asyncio.TimeoutError:
        result = {"dependency": name, "status": "unavailable", "error": "Timeout"}
        metrics.dependency_failed(name, "Timeout", op="probe")
    except Exception as exc:
        result = {"dependency": name, "status": "unavailable", "error": type(exc).__name__}
        metrics.dependency_failed(name, exc, op="probe")
    else:
        result = {
            "dependency": name,
            "status": "ok",
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        }
        metrics.dependency_ok(name)
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
    _db_state.update(inflight=None, last=None, last_at=0.0)
