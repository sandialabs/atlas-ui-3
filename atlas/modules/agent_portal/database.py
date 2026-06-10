"""Database engine factory for the Agent Portal state store.

Mirrors ``atlas/modules/chat_history/database.py`` — same DuckDB-via-
SQLAlchemy pattern, just with its own URL env var (``AGENT_PORTAL_DB_URL``)
and default file (``data/agent_portal.db``) so the two stores live in
separate files and can be backed up / wiped independently.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from .models import Base

logger = logging.getLogger(__name__)

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None

DEFAULT_DB_URL = "duckdb:///data/agent_portal.db"


def _resolve_db_url(db_url: str) -> str:
    """Resolve a DuckDB URL to an absolute path under the project root.

    Mirrors the chat_history resolver so a relative ``duckdb:///data/...``
    URL lands in the same place regardless of the cwd the server happens
    to be launched from.
    """
    if db_url.startswith("duckdb:///"):
        db_path = db_url.replace("duckdb:///", "")
        if not os.path.isabs(db_path):
            # atlas/ is one above this file's parent (modules/agent_portal/),
            # and the project root is one above atlas/.
            project_root = Path(__file__).parent.parent.parent.parent
            full_path = project_root / db_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            db_url = f"duckdb:///{full_path}"
            logger.info("Agent portal DuckDB path resolved to: %s", full_path)
    return db_url


def get_engine(db_url: Optional[str] = None) -> Engine:
    """Get or create the SQLAlchemy engine for the agent-portal store.

    Resolution order: explicit ``db_url`` arg → ``AGENT_PORTAL_DB_URL`` env
    var → ``DEFAULT_DB_URL``.
    """
    global _engine
    if _engine is not None:
        return _engine

    if db_url is None:
        db_url = os.environ.get("AGENT_PORTAL_DB_URL", DEFAULT_DB_URL)

    db_url = _resolve_db_url(db_url)

    if db_url.startswith("duckdb"):
        _engine = create_engine(db_url, echo=False)
    elif db_url.startswith("postgresql"):
        _engine = create_engine(
            db_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
            echo=False,
        )
    else:
        _engine = create_engine(db_url, echo=False)

    logger.info(
        "Agent portal database engine created: %s",
        db_url.split("@")[-1] if "@" in db_url else db_url,
    )
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


def init_database(db_url: Optional[str] = None) -> Engine:
    """Initialize the database, creating tables if they don't exist.

    Single-user / dev convenience. A real prod deploy should manage
    schema via Alembic — but PortalStore is single-user-on-own-machine
    today, so create_all is enough.
    """
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    logger.info("Agent portal database tables created/verified")
    return engine


def reset_engine() -> None:
    """Reset the global engine (for testing only)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
