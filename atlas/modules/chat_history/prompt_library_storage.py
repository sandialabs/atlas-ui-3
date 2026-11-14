"""Shared storage plumbing for the prompt library (issue #1169).

Typed storage errors, the guard that keeps database exception text out of
logs, and the classification of transient write conflicts.

Why the guard exists: a SQLAlchemy exception's message embeds the failing SQL
and its bound parameters -- prompt text, titles, email addresses. Logging it,
or letting it escape to the ASGI server (which logs unhandled exceptions with
their messages), would leak protected content. Every repository operation runs
inside ``storage_guard``, which logs only the operation name, the exception
type and the SQLSTATE, and re-raises a ``PromptStorageError`` whose message is
fixed. The original exception is not chained (``from None``), so it cannot be
printed with a traceback either.
"""

import functools
import logging
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy.exc import IntegrityError, SQLAlchemyError

logger = logging.getLogger(__name__)

VERSION_CONSTRAINT = "uq_prompt_library_version_number"
GRANT_CONSTRAINT = "uq_prompt_library_grant"

# Text a non-PostgreSQL driver puts in a unique-violation message, per
# constraint (SQLite and DuckDB do not report constraint names). Only ever
# matched, never logged.
_CONSTRAINT_MARKERS = {
    VERSION_CONSTRAINT: "version_number",
    GRANT_CONSTRAINT: "grantee",
}

# PostgreSQL: serialization failure, deadlock, lock not available.
_TRANSIENT_SQLSTATES = frozenset({"40001", "40P01", "55P03"})
_UNIQUE_VIOLATION = "23505"


class PromptStorageError(Exception):
    """The prompt-library database failed. The message is safe to show and log."""

    def __init__(self, operation: str):
        super().__init__(f"Prompt library storage error during {operation}")
        self.operation = operation


class PromptConflictError(Exception):
    """A concurrent change won: ``kind`` is ``draft``, ``sharing`` or ``publish``."""

    def __init__(self, kind: str):
        super().__init__(f"Concurrent {kind} change")
        self.kind = kind


def sqlstate(exc: BaseException) -> Optional[str]:
    orig = getattr(exc, "orig", None)
    return getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)


def is_transient_conflict(exc: BaseException, constraint: Optional[str] = None) -> bool:
    """True for a write conflict a retry (or a re-read) can resolve.

    That is a PostgreSQL serialization failure/deadlock/lock timeout, a DuckDB
    write-write conflict between connections, or -- when ``constraint`` is
    named -- a unique violation of that specific constraint. Everything else
    (connectivity, other constraints, schema errors) is not transient.
    """
    if not isinstance(exc, SQLAlchemyError):
        return False
    orig = getattr(exc, "orig", None)
    code = sqlstate(exc)
    if code in _TRANSIENT_SQLSTATES:
        return True
    if type(orig).__name__ == "TransactionException":  # DuckDB: "Conflict on ..."
        return True
    if constraint is None:
        return False
    if code == _UNIQUE_VIOLATION:
        diag = getattr(orig, "diag", None)
        return getattr(diag, "constraint_name", None) == constraint
    unique_violation = isinstance(exc, IntegrityError) or type(orig).__name__ in (
        "ConstraintException",
        "IntegrityError",
    )
    return unique_violation and _CONSTRAINT_MARKERS[constraint] in str(orig)


@contextmanager
def storage_guard(operation: str) -> Iterator[None]:
    """Turn any database error into a ``PromptStorageError`` with a safe message."""
    try:
        yield
    except (PromptStorageError, PromptConflictError):
        raise
    except SQLAlchemyError as e:
        logger.error(
            "Prompt library storage operation %s failed (%s, sqlstate=%s)",
            operation,
            type(e).__name__,
            sqlstate(e) or "n/a",
        )
        raise PromptStorageError(operation) from None


def guarded(operation: str):
    """Decorator form of ``storage_guard`` for repository methods."""

    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            with storage_guard(operation):
                return fn(*args, **kwargs)

        return wrapper

    return decorate
