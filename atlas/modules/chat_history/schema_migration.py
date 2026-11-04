"""Run chat-history schema migrations safely from every replica (issue #1129).

``python -m atlas.modules.chat_history.schema_migration`` brings the database
to the Alembic head of the code it ships with. Kubernetes runs it as an init
container of every API pod, so a rollout or scale-up never starts a replica
against an older schema, and no separate ordering step is needed.

Running it from many pods at once is safe on PostgreSQL: each run holds a
session-level advisory lock for the whole upgrade, so exactly one pod applies
a pending migration and the others wait, then find the database at head and
change nothing. Without that lock, replicas racing on an empty database fail
(``Base.metadata.create_all`` at startup is not concurrency-safe either: the
losers hit ``DuplicateTable`` and come up with chat history disabled).

A database already at a revision this image does not know (a rollback after a
newer release migrated) is left unchanged with a warning: migrations are
additive, and a downgrade is never automatic.

A database whose tables were created by an Atlas startup (``create_all``)
before Alembic was ever run has no ``alembic_version`` table. Upgrading it
would fail with ``DuplicateTable``, so the run stops with instructions
instead; stamping is an operator decision, never automatic.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from sqlalchemy import create_engine, inspect, pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError

from atlas.modules.config.settings import pop_prepare_threshold_query, resolve_migration_db_url

logger = logging.getLogger("atlas.schema_migration")

# Arbitrary, fixed 64-bit key shared by every Atlas process ("ATLASMIG").
ADVISORY_LOCK_KEY = 0x41544C41534D4947

# Tables only Atlas creates; any of them without alembic_version means the
# schema came from create_all.
_SENTINEL_TABLES = ("conversations", "messages")


class MigrationRefused(RuntimeError):
    """The database is in a state the runner will not change on its own."""


def default_alembic_ini() -> Path:
    """``alembic.ini`` beside the ``atlas`` package (the image keeps it at /app)."""
    override = os.environ.get("ATLAS_ALEMBIC_INI")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "alembic.ini"


def _alembic_config(ini_path: Path):
    from alembic.config import Config

    if not ini_path.is_file():
        raise MigrationRefused(f"alembic.ini not found at {ini_path}; set ATLAS_ALEMBIC_INI")
    return Config(str(ini_path))


def head_revisions(ini_path: Path) -> set[str]:
    from alembic.script import ScriptDirectory

    return set(ScriptDirectory.from_config(_alembic_config(ini_path)).get_heads())


def unknown_revisions(ini_path: Path, revisions: set[str]) -> set[str]:
    """Revisions this image's migration scripts do not contain (a newer release's)."""
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(_alembic_config(ini_path))
    known = {rev.revision for rev in script.walk_revisions()}
    return {rev for rev in revisions if rev not in known}


def current_revisions(connection: Connection) -> set[str]:
    from alembic.runtime.migration import MigrationContext

    return set(MigrationContext.configure(connection).get_current_heads())


def _check_not_unversioned(connection: Connection) -> None:
    tables = set(inspect(connection).get_table_names())
    if "alembic_version" in tables:
        return
    present = [name for name in _SENTINEL_TABLES if name in tables]
    if present:
        raise MigrationRefused(
            "Chat-history tables exist but the database has no alembic_version table "
            f"(found {', '.join(present)}): it was created by an Atlas startup, not by "
            "Alembic. Verify the schema matches the current release, then record it "
            "once with `alembic -c /app/alembic.ini stamp head` and re-run. "
            "See docs/admin/chat-history.md#alembic-migrations."
        )


def _reason(exc: OperationalError) -> str:
    """The driver's first error line; libpq messages name host and port, never the password."""
    detail = str(getattr(exc, "orig", None) or exc).strip().splitlines()
    return f"{type(exc).__name__}: {detail[0] if detail else ''}"


def _connect_with_retry(engine, wait_seconds: float, sleep: Callable[[float], None]) -> Connection:
    deadline = time.monotonic() + wait_seconds
    delay = 1.0
    while True:
        try:
            return engine.connect()
        except OperationalError as exc:
            if time.monotonic() >= deadline:
                raise
            logger.info("Database not reachable yet (%s); retrying in %.0fs", _reason(exc), delay)
            sleep(delay)
            delay = min(delay * 2, 10.0)


def upgrade_to_head(
    db_url: Optional[str] = None,
    ini_path: Optional[Path] = None,
    wait_seconds: float = 300.0,
    sleep: Callable[[float], None] = time.sleep,
) -> set[str]:
    """Upgrade the chat-history database to head; returns the revisions applied over.

    Holds the PostgreSQL advisory lock for the whole run so concurrent callers
    serialize. Other databases (SQLite in tests) are upgraded without a
    lock. Alembic has no DuckDB support; DuckDB deployments rely on create_all.
    """
    from alembic import command

    db_url = db_url or resolve_migration_db_url()
    if not db_url:
        raise MigrationRefused("No database configured: set CHAT_HISTORY_DB_URL (or the DB_* variables)")
    ini_path = ini_path or default_alembic_ini()
    config = _alembic_config(ini_path)
    heads = head_revisions(ini_path)

    url, connect_args = pop_prepare_threshold_query(db_url)
    engine = create_engine(url, poolclass=pool.NullPool, connect_args=connect_args)
    is_postgres = engine.dialect.name == "postgresql"
    try:
        connection = _connect_with_retry(engine, wait_seconds, sleep)
        try:
            if is_postgres:
                logger.info("Waiting for the schema migration lock")
                connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY})
                connection.commit()
            try:
                before = current_revisions(connection)
                connection.commit()
                if before == heads:
                    logger.info("Chat-history schema already at head %s; nothing to do", sorted(heads))
                    return before
                newer = unknown_revisions(ini_path, before)
                if newer:
                    # A rollback to an older image after a newer release
                    # migrated. Migrations are additive, so the older code
                    # runs on the newer schema; downgrading is never automatic.
                    logger.warning(
                        "Chat-history schema is at %s, newer than this image's head %s (a rollback?); "
                        "leaving it unchanged. Downgrade explicitly with alembic if required.",
                        sorted(newer), sorted(heads),
                    )
                    return before
                _check_not_unversioned(connection)
                connection.commit()
                logger.info("Upgrading chat-history schema %s -> %s", sorted(before) or "(empty)", sorted(heads))
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
                connection.commit()
                after = current_revisions(connection)
                connection.commit()
                if after != heads:
                    raise MigrationRefused(f"Upgrade finished at {sorted(after)}, expected {sorted(heads)}")
                logger.info("Chat-history schema now at head %s", sorted(after))
                return before
            finally:
                if is_postgres:
                    connection.rollback()
                    connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
                    connection.commit()
        finally:
            connection.close()
    finally:
        engine.dispose()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=float(os.environ.get("ATLAS_MIGRATION_WAIT_SECONDS", "300")),
        help="How long to keep retrying while the database is unreachable (default 300)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    try:
        upgrade_to_head(wait_seconds=args.wait_seconds)
    except MigrationRefused as exc:
        logger.error("%s", exc)
        return 2
    except OperationalError as exc:
        logger.error("Database unreachable after %.0fs: %s", args.wait_seconds, _reason(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
