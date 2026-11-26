"""Storage failure handling for the prompt library (issue #1169 review).

Publishing retries only transient conflicts and ends in a typed error;
database exception text (SQL, parameters, prompt text, identities) never
reaches a log; repository work never blocks the event loop.
"""

import asyncio
import logging
import time

import pytest
from sqlalchemy.exc import IntegrityError, OperationalError

from atlas.modules.chat_history import prompt_library_repository as repo_module
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.chat_history.prompt_library_repository import (
    PromptConflictError,
    PromptLibraryRepository,
    PromptStorageError,
)
from atlas.modules.chat_history.prompt_library_storage import (
    GRANT_CONSTRAINT,
    VERSION_CONSTRAINT,
    is_transient_conflict,
)
from atlas.modules.prompts.prompt_library_policy import PromptConflict, PromptLibraryUnavailable
from atlas.modules.prompts.prompt_library_service import PromptLibraryService

SECRET = "TOP-SECRET-PROMPT-TEXT"
EMAIL = "someone.private@example.com"


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def repo(tmp_path):
    from atlas.modules.chat_history import get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'storage.db'}")
    return PromptLibraryRepository(get_session_factory())


class _Orig(Exception):
    def __init__(self, message, sqlstate=None, constraint=None):
        super().__init__(message)
        self.sqlstate = sqlstate
        self.diag = type("Diag", (), {"constraint_name": constraint})()


class TransactionException(Exception):
    """Stands in for duckdb.TransactionException (matched by type name)."""


def _db_error(cls, orig):
    return cls(f"INSERT ... VALUES ('{SECRET}', '{EMAIL}')", {"content": SECRET, "author": EMAIL}, orig)


@pytest.mark.parametrize("exc, constraint, expected", [
    (_db_error(OperationalError, _Orig("x", "40001")), None, True),            # serialization failure
    (_db_error(OperationalError, _Orig("x", "40P01")), None, True),            # deadlock
    (_db_error(OperationalError, TransactionException("Conflict on update")), None, True),  # DuckDB
    (_db_error(IntegrityError, _Orig("dup", "23505", VERSION_CONSTRAINT)), VERSION_CONSTRAINT, True),
    (_db_error(IntegrityError, _Orig("dup", "23505", GRANT_CONSTRAINT)), VERSION_CONSTRAINT, False),
    (_db_error(IntegrityError, _Orig("UNIQUE constraint failed: t.version_number")), VERSION_CONSTRAINT, True),
    (_db_error(IntegrityError, _Orig("NOT NULL constraint failed: t.title")), VERSION_CONSTRAINT, False),
    (_db_error(OperationalError, _Orig("connection refused", "08006")), VERSION_CONSTRAINT, False),
    (RuntimeError("not a database error"), VERSION_CONSTRAINT, False),
])
def test_transient_conflict_classification(exc, constraint, expected):
    assert is_transient_conflict(exc, constraint) is expected


def test_publish_retries_transient_conflicts_then_raises_a_typed_error(repo, monkeypatch, caplog):
    prompt = repo.create_prompt("owner@example.com", "T", None, "body")
    attempts = []

    def conflict(*args):
        attempts.append(1)
        raise _db_error(IntegrityError, _Orig("dup", "23505", VERSION_CONSTRAINT))

    monkeypatch.setattr(repo, "_publish_once", conflict)
    monkeypatch.setattr(repo_module, "_PUBLISH_BACKOFF_SECONDS", 0)
    with caplog.at_level(logging.DEBUG), pytest.raises(PromptConflictError) as exc:
        repo.publish_version(prompt["id"], EMAIL, None, 1)
    assert exc.value.kind == "publish"
    assert len(attempts) == repo_module._PUBLISH_ATTEMPTS
    assert SECRET not in caplog.text and EMAIL not in caplog.text


def test_publish_does_not_retry_unrelated_failures(repo, monkeypatch, caplog):
    prompt = repo.create_prompt("owner@example.com", "T", None, "body")
    attempts = []

    def broken(*args):
        attempts.append(1)
        raise _db_error(OperationalError, _Orig("connection refused", "08006"))

    monkeypatch.setattr(repo, "_publish_once", broken)
    with caplog.at_level(logging.DEBUG), pytest.raises(PromptStorageError) as exc:
        repo.publish_version(prompt["id"], EMAIL, None, 1)
    assert len(attempts) == 1
    assert SECRET not in str(exc.value) and exc.value.__cause__ is None and exc.value.__suppress_context__
    assert SECRET not in caplog.text and EMAIL not in caplog.text
    assert "OperationalError" in caplog.text and "08006" in caplog.text


@pytest.mark.parametrize("method, args", [
    ("create_prompt", (EMAIL, SECRET, None, SECRET)),
    ("update_prompt", ("p", 1, SECRET, None, SECRET)),
    ("replace_grants", ("p", [{"grantee_type": "user", "grantee": EMAIL, "permission": "use"}], EMAIL, 0)),
    ("add_audit_event", ("p", "use", EMAIL)),
    ("page_prompts", ({"p"}, set(), SECRET, 10, 0)),
])
def test_database_errors_never_leak_into_logs(repo, monkeypatch, caplog, method, args):
    def boom():
        raise _db_error(OperationalError, _Orig(f"failed near '{SECRET}' for {EMAIL}", "XX000"))

    monkeypatch.setattr(repo, "_session", boom)
    with caplog.at_level(logging.DEBUG), pytest.raises(PromptStorageError) as exc:
        getattr(repo, method)(*args)
    assert SECRET not in str(exc.value) and EMAIL not in str(exc.value)
    assert SECRET not in caplog.text and EMAIL not in caplog.text
    assert "OperationalError" in caplog.text


@pytest.mark.asyncio
async def test_service_maps_storage_errors_to_safe_typed_errors(repo, monkeypatch):
    service = PromptLibraryService(repo, group_check=lambda u, g: asyncio.sleep(0, False))

    def boom():
        raise _db_error(OperationalError, _Orig(SECRET, "XX000"))

    monkeypatch.setattr(repo, "_session", boom)
    with pytest.raises(PromptLibraryUnavailable) as exc:
        await service.create_prompt(EMAIL, "T", "body")
    assert SECRET not in exc.value.message

    def conflict(*args):
        raise PromptConflictError("publish")

    monkeypatch.setattr(repo, "get_prompt", lambda pid: {"id": pid, "owner_email": EMAIL, "archived_at": None,
                                                          "latest_version_number": 0, "draft_revision": 1})
    monkeypatch.setattr(repo, "list_grants", lambda pid: [])
    monkeypatch.setattr(repo, "publish_version", conflict)
    with pytest.raises(PromptConflict) as conflict_exc:
        await service.publish(EMAIL, "p", 1)
    assert "Try again" in conflict_exc.value.message


@pytest.mark.asyncio
async def test_slow_storage_does_not_block_the_event_loop(repo, monkeypatch):
    """A publish that backs off for a while leaves the loop free for other sockets."""
    service = PromptLibraryService(repo)
    prompt = await service.create_prompt("owner@example.com", "T", "body")
    real_publish = repo.publish_version

    def slow_publish(*args):
        time.sleep(0.6)  # retries backing off under contention
        return real_publish(*args)

    monkeypatch.setattr(repo, "publish_version", slow_publish)
    gaps = []

    async def ticker():
        last = time.monotonic()
        for _ in range(30):
            await asyncio.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    publish = asyncio.create_task(service.publish("owner@example.com", prompt["id"], 1))
    await ticker()
    version = await publish
    assert version["version_number"] == 1
    assert max(gaps) < 0.3, f"event loop stalled for {max(gaps):.2f}s"
