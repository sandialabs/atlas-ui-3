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

from sqlalchemy import create_engine, inspect, make_url, pool, text
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


class DatabaseUnreachable(RuntimeError):
    """No connection could be opened before the wait ran out."""


class DatabaseAuthFailed(RuntimeError):
    """The server rejected the credentials; retrying cannot help."""


# Invalid password / invalid authorization specification. libpq reports a
# connect-time FATAL without a SQLSTATE through both psycopg drivers, so the
# server's message text is matched as well.
_PASSWORD_SQLSTATE = "28P01"
_AUTHORIZATION_SQLSTATE = "28000"
_PASSWORD_MESSAGES = ("password authentication failed",)


def _auth_failure_kind(exc: OperationalError) -> Optional[str]:
    """"password", "authorization" (pg_hba / unknown role), or None for anything else.

    Transient FATALs ("the database system is starting up", "too many
    clients") are not authentication failures and keep being retried.
    """
    orig = getattr(exc, "orig", None)
    code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    message = str(orig or exc)
    if code == _PASSWORD_SQLSTATE or ("FATAL" in message and any(m in message for m in _PASSWORD_MESSAGES)):
        return "password"
    if code == _AUTHORIZATION_SQLSTATE or (
        "FATAL" in message
        and ("no pg_hba.conf entry" in message or ('role "' in message and "does not exist" in message))
    ):
        return "authorization"
    return None


def _is_auth_failure(exc: OperationalError) -> bool:
    return _auth_failure_kind(exc) is not None


class MigrationLockTimeout(RuntimeError):
    """Another migration held the advisory lock for longer than the wait."""


# libpq-based drivers accept connect_timeout; without it one connect attempt to
# a black-holed address can hang far past the retry deadline.
_LIBPQ_DRIVERS = ("psycopg", "psycopg2")
DEFAULT_CONNECT_TIMEOUT_SECONDS = 10


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
            kind = _auth_failure_kind(exc)
            if kind == "password":
                raise DatabaseAuthFailed(
                    f"PostgreSQL rejected the password ({_reason(exc)}). If secrets.env was regenerated "
                    "against a kept database volume, the volume still has the old password: restore the old "
                    "secrets.env, change the password in PostgreSQL (ALTER ROLE), or purge the volume."
                ) from exc
            if kind == "authorization":
                raise DatabaseAuthFailed(
                    f"PostgreSQL refused the connection ({_reason(exc)}): the role does not exist or "
                    "pg_hba.conf does not admit this client. Fix the database user or its access rules; "
                    "retrying will not help."
                ) from exc
            if time.monotonic() >= deadline:
                raise DatabaseUnreachable(f"Database unreachable after {wait_seconds:.0f}s: {_reason(exc)}") from exc
            logger.info("Database not reachable yet (%s); retrying in %.0fs", _reason(exc), delay)
            sleep(delay)
            delay = min(delay * 2, 10.0)


def _acquire_lock(connection: Connection, wait_seconds: float, sleep: Callable[[float], None]) -> None:
    """Take the advisory lock, polling so the wait is bounded and visible."""
    deadline = time.monotonic() + wait_seconds
    next_report = 0.0
    while True:
        acquired = connection.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY}
        ).scalar()
        connection.commit()
        if acquired:
            return
        now = time.monotonic()
        if now >= deadline:
            raise MigrationLockTimeout(
                f"Another migration held the schema migration lock for over {wait_seconds:.0f}s; "
                "check the other pods' migrate init containers (or pg_locks) and re-run"
            )
        if now >= next_report:
            logger.info("Another pod is migrating; waiting for the schema migration lock")
            next_report = now + 30.0
        sleep(min(2.0, max(0.0, deadline - now)))


def _release_lock(connection: Connection) -> None:
    """Best effort: closing the connection releases a session lock anyway."""
    try:
        connection.rollback()
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
        connection.commit()
    except Exception as exc:  # never hide the error that brought us here
        logger.warning("Could not release the migration lock explicitly (%s); closing the connection releases it",
                       type(exc).__name__)


def _engine_args(db_url: str) -> tuple[str, dict]:
    """URL and connect args for the migration engine (bounded connect attempts)."""
    url, connect_args = pop_prepare_threshold_query(db_url)
    parsed = make_url(url)
    if (
        parsed.get_backend_name() == "postgresql"
        and parsed.get_driver_name() in _LIBPQ_DRIVERS
        and "connect_timeout" not in parsed.query
    ):
        connect_args = {**connect_args, "connect_timeout": DEFAULT_CONNECT_TIMEOUT_SECONDS}
    return url, connect_args


def upgrade_to_head(
    db_url: Optional[str] = None,
    ini_path: Optional[Path] = None,
    wait_seconds: float = 300.0,
    sleep: Callable[[float], None] = time.sleep,
    lock_wait_seconds: float = 600.0,
    ddl_lock_timeout_seconds: int = 60,
) -> set[str]:
    """Upgrade the chat-history database to head; returns the revisions applied over.

    Holds the PostgreSQL advisory lock for the whole run so concurrent callers
    serialize. Other databases (SQLite in tests) are upgraded without a
    lock. Alembic has no DuckDB support; DuckDB deployments rely on create_all.

    Every wait is bounded: connecting (``wait_seconds``, each attempt with a
    connect timeout), the advisory lock (``lock_wait_seconds``) and each DDL
    statement's table locks (``lock_timeout``), so a stuck migration fails the
    init container instead of holding a rollout forever.
    """
    from alembic import command

    db_url = db_url or resolve_migration_db_url()
    if not db_url:
        raise MigrationRefused("No database configured: set CHAT_HISTORY_DB_URL (or the DB_* variables)")
    ini_path = ini_path or default_alembic_ini()
    config = _alembic_config(ini_path)
    heads = head_revisions(ini_path)

    url, connect_args = _engine_args(db_url)
    engine = create_engine(url, poolclass=pool.NullPool, connect_args=connect_args)
    is_postgres = engine.dialect.name == "postgresql"
    try:
        connection = _connect_with_retry(engine, wait_seconds, sleep)
        try:
            if is_postgres:
                _acquire_lock(connection, lock_wait_seconds, sleep)
                # Session-wide: a migration that waits on a table lock (held
                # by a long transaction of a running replica) fails instead
                # of blocking that table for everyone behind it.
                connection.execute(text(f"SET lock_timeout = '{int(ddl_lock_timeout_seconds)}s'"))
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
                    _release_lock(connection)
        finally:
            try:
                connection.close()
            except Exception:  # the original error, if any, matters more
                logger.debug("Closing the migration connection failed", exc_info=True)
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
    parser.add_argument(
        "--lock-wait-seconds",
        type=float,
        default=float(os.environ.get("ATLAS_MIGRATION_LOCK_WAIT_SECONDS", "600")),
        help="How long to wait for another pod's migration to release the lock (default 600)",
    )
    parser.add_argument(
        "--ddl-lock-timeout-seconds",
        type=int,
        default=int(os.environ.get("ATLAS_MIGRATION_DDL_LOCK_TIMEOUT_SECONDS", "60")),
        help="PostgreSQL lock_timeout for the migration's statements (default 60)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    try:
        upgrade_to_head(
            wait_seconds=args.wait_seconds,
            lock_wait_seconds=args.lock_wait_seconds,
            ddl_lock_timeout_seconds=args.ddl_lock_timeout_seconds,
        )
    except MigrationRefused as exc:
        logger.error("%s", exc)
        return 2
    except DatabaseUnreachable as exc:
        logger.error("%s", exc)
        return 1
    except DatabaseAuthFailed as exc:
        logger.error("%s", exc)
        return 4
    except MigrationLockTimeout as exc:
        logger.error("%s", exc)
        return 3
    except OperationalError as exc:
        logger.error("Migration failed: %s", _reason(exc))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
