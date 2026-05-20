"""Database engine factory for Agent Portal V3.

Reuses the same DuckDB file as agent_portal v1/v2 by default (clean
single-file backup story) but with its own table prefix. Override via
AGENT_PORTAL_V3_DB_URL.
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

DEFAULT_DB_URL = "duckdb:///data/agent_portal_v3.db"


def _resolve_db_url(db_url: str) -> str:
    if db_url.startswith("duckdb:///"):
        db_path = db_url.replace("duckdb:///", "")
        if not os.path.isabs(db_path):
            project_root = Path(__file__).parent.parent.parent.parent
            full_path = project_root / db_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            db_url = f"duckdb:///{full_path}"
            logger.info("Agent portal v3 DuckDB path resolved to: %s", full_path)
    return db_url


def get_engine(db_url: Optional[str] = None) -> Engine:
    global _engine
    if _engine is not None:
        return _engine

    if db_url is None:
        db_url = os.environ.get("AGENT_PORTAL_V3_DB_URL", DEFAULT_DB_URL)

    db_url = _resolve_db_url(db_url)
    _engine = create_engine(db_url, echo=False)
    logger.info("Agent portal v3 engine created: %s", db_url)
    return _engine


def get_session_factory(engine: Optional[Engine] = None) -> sessionmaker:
    global _session_factory
    if _session_factory is not None:
        return _session_factory
    if engine is None:
        engine = get_engine()
    _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factory


def init_database(db_url: Optional[str] = None) -> Engine:
    engine = get_engine(db_url)
    Base.metadata.create_all(engine)
    logger.info("Agent portal v3 tables created/verified")
    return engine


def reset_engine() -> None:
    """For tests only."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
