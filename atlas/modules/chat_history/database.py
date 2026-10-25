"""Database engine factory for chat history persistence.

Supports DuckDB (local/dev) and PostgreSQL (production) via SQLAlchemy.
"""

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from atlas.core.duckdb_indexes import drop_duckdb_secondary_indexes
from atlas.modules.config.settings import normalize_postgres_driver_url

from .models import Base

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None
_engine_pool: Optional["PoolSettings"] = None
_session_factory: Optional[sessionmaker] = None

# Drivers built on libpq, which accept a ``connect_timeout`` connection argument.
_LIBPQ_DRIVERS = frozenset({"psycopg", "psycopg2"})


def _resolve_db_url(db_url: str) -> str:
    """Resolve the database URL, creating directories for DuckDB if needed."""
    if db_url.startswith("duckdb:///"):
        # Relative path - make it relative to project root
        db_path = db_url.replace("duckdb:///", "")
        if not os.path.isabs(db_path):
            # Find project root (where .env lives)
            project_root = Path(__file__).parent.parent.parent.parent
            full_path = project_root / db_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            db_url = f"duckdb:///{full_path}"
            logger.info("DuckDB path resolved to: %s", full_path)
    return db_url


@dataclass(frozen=True)
class PoolSettings:
    """PostgreSQL connection pool settings for the chat-history engine.

    Each API process builds its own engine, so the connections a deployment
    can hold open is replicas x workers x ``max_connections_per_process``.
    The defaults match the values Atlas used before they were configurable,
    plus a connection recycle and a connect timeout (issue #1091).
    """

    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: float = 30.0
    pool_recycle: int = 1800
    pool_pre_ping: bool = True
    connect_timeout: int = 10
    disable_pooling: bool = False

    @classmethod
    def from_app_settings(cls, app_settings: Any) -> "PoolSettings":
        """Read the ``CHAT_HISTORY_DB_*`` pool settings from ``AppSettings``."""
        return cls(
            pool_size=app_settings.chat_history_db_pool_size,
            max_overflow=app_settings.chat_history_db_max_overflow,
            pool_timeout=app_settings.chat_history_db_pool_timeout,
            pool_recycle=app_settings.chat_history_db_pool_recycle,
            pool_pre_ping=app_settings.chat_history_db_pool_pre_ping,
            connect_timeout=app_settings.chat_history_db_connect_timeout,
            disable_pooling=app_settings.chat_history_db_disable_pooling,
        )

    @property
    def max_connections_per_process(self) -> Optional[int]:
        """Upper bound on connections one process holds; None when unpooled."""
        if self.disable_pooling:
            return None
        return self.pool_size + self.max_overflow


def _postgres_engine_kwargs(db_url: str, pool: PoolSettings) -> Dict[str, Any]:
    """Build ``create_engine`` keyword arguments for a PostgreSQL URL."""
    kwargs: Dict[str, Any] = {"pool_pre_ping": pool.pool_pre_ping, "echo": False}
    url = make_url(db_url)
    # Only libpq drivers understand connect_timeout (pg8000 or asyncpg would
    # reject it). A connect_timeout already in the URL query wins over the
    # setting, since the driver would otherwise receive the setting's value.
    if url.get_driver_name() in _LIBPQ_DRIVERS and "connect_timeout" not in url.query:
        kwargs["connect_args"] = {"connect_timeout": pool.connect_timeout}
    if pool.disable_pooling:
        kwargs["poolclass"] = NullPool
        # psycopg (v3) auto-prepares statements after a few executions, which
        # transaction-mode PgBouncer below 1.21 cannot route. When an external
        # pooler does the pooling, keep statements unnamed; psycopg2 has no
        # such setting and would reject the keyword, so scope it to psycopg 3.
        # A prepare_threshold the operator set in the URL query wins.
        if url.get_driver_name() == "psycopg" and "prepare_threshold" not in url.query:
            kwargs.setdefault("connect_args", {})["prepare_threshold"] = None
    else:
        kwargs.update(
            pool_size=pool.pool_size,
            max_overflow=pool.max_overflow,
            pool_timeout=pool.pool_timeout,
            pool_recycle=pool.pool_recycle,
        )
    return kwargs


def get_engine(db_url: Optional[str] = None, pool: Optional[PoolSettings] = None) -> Engine:
    """Get or create the SQLAlchemy engine.

    Args:
        db_url: Database URL. If None, uses CHAT_HISTORY_DB_URL env var
                or defaults to DuckDB.
        pool: PostgreSQL pool settings. If None, the ``PoolSettings``
              defaults are used. Ignored for other databases.
    """
    global _engine, _engine_pool
    if _engine is not None:
        if pool is not None and _engine.dialect.name == "postgresql" and pool != _engine_pool:
            logger.warning(
                "Chat history engine already exists; ignoring new pool settings %s (engine built with %s)",
                pool,
                _engine_pool,
            )
        return _engine

    if db_url is None:
        db_url = os.environ.get("CHAT_HISTORY_DB_URL", "duckdb:///data/chat_history.db")

    db_url = _resolve_db_url(db_url)
    # Name the psycopg (v3) driver on bare postgresql:// URLs so the driver does
    # not depend on the installed SQLAlchemy version (issue #991). URLs that
    # already name a driver are left as the operator wrote them.
    db_url = normalize_postgres_driver_url(db_url)

    if db_url.startswith("postgresql"):
        pool = pool or PoolSettings()
        _engine_pool = pool
        engine_kwargs = _postgres_engine_kwargs(db_url, pool)
        _engine = create_engine(db_url, **engine_kwargs)
        connect_args = engine_kwargs.get("connect_args", {})
        # Not set here when the URL carries its own value or the driver is not libpq.
        connect_timeout = f"{connect_args['connect_timeout']}s" if "connect_timeout" in connect_args else "driver/URL"
        if pool.disable_pooling:
            logger.info(
                "Chat history PostgreSQL pooling disabled (NullPool); pool externally, e.g. PgBouncer. "
                "connect_timeout=%s",
                connect_timeout,
            )
        else:
            logger.info(
                "Chat history PostgreSQL pool: pool_size=%d max_overflow=%d (up to %d connections per process), "
                "pool_timeout=%ss pool_recycle=%s connect_timeout=%s",
                pool.pool_size,
                pool.max_overflow,
                pool.max_connections_per_process,
                pool.pool_timeout,
                "disabled" if pool.pool_recycle == -1 else f"{pool.pool_recycle}s",
                connect_timeout,
            )
    else:
        _engine = create_engine(db_url, echo=False)

    logger.info("Chat history database engine created: %s", db_url.split("@")[-1] if "@" in db_url else db_url)
    return _engine


def get_session_factory(engine: Optional[Engine] = None) -> sessionmaker:
    """Get or create the session factory."""
    global _session_factory
    if _session_factory is not None:
        return _session_factory

    if engine is None:
        engine = get_engine()

    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factory


def init_database(db_url: Optional[str] = None, pool: Optional[PoolSettings] = None) -> Engine:
    """Initialize the database, creating tables if they don't exist.

    For production, use Alembic migrations instead of this function.
    This is a convenience for development/testing.
    """
    engine = get_engine(db_url, pool)
    Base.metadata.create_all(engine)
    drop_duckdb_secondary_indexes(engine, Base.metadata)
    logger.info("Chat history database tables created/verified")
    return engine


def reset_engine():
    """Reset the global engine (for testing)."""
    global _engine, _engine_pool, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _engine_pool = None
    _session_factory = None
