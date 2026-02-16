"""Database engine factory for chat history persistence.

Supports DuckDB (local/dev) and PostgreSQL (production) via SQLAlchemy.
"""

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


def get_engine(db_url: Optional[str] = None) -> Engine:
    """Get or create the SQLAlchemy engine.

    Args:
        db_url: Database URL. If None, uses CHAT_HISTORY_DB_URL env var
                or defaults to DuckDB.
    """
    global _engine
    if _engine is not None:
        return _engine

    if db_url is None:
        db_url = os.environ.get("CHAT_HISTORY_DB_URL", "duckdb:///data/chat_history.db")

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


def init_database(db_url: Optional[str] = None) -> Engine:
    """Initialize the database, creating tables if they don't exist.

    For production, use Alembic migrations instead of this function.
    This is a convenience for development/testing.
    """
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    logger.info("Chat history database tables created/verified")
    return engine


def reset_engine():
    """Reset the global engine (for testing)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
