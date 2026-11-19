"""Concurrency for the prompt library (issue #1169 review).

Concurrent publication, concurrent sharing updates and concurrent draft edits
on DuckDB (dev backend) and, when ``ATLAS_TEST_POSTGRES_URL`` points at a
server, on PostgreSQL with independent engines standing in for replicas.
"""

import os
import threading
import time
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from alembic import command
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.chat_history.prompt_library_repository import (
    PromptConflictError,
    PromptLibraryRepository,
)
from atlas.modules.chat_history.schema_migration import default_alembic_ini

PG_URL = os.environ.get("ATLAS_TEST_POSTGRES_URL")
OWNER = "owner@example.com"


def group(name):
    return {"grantee_type": "group", "grantee": name, "permission": "use"}


def user(email, permission="use"):
    return {"grantee_type": "user", "grantee": email, "permission": permission}


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def duck(tmp_path):
    from atlas.modules.chat_history import get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'race.db'}")
    return PromptLibraryRepository(get_session_factory())


@pytest.fixture
def pg_url(monkeypatch):
    if not PG_URL:
        pytest.skip("ATLAS_TEST_POSTGRES_URL not set")
    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    name = f"atlas_lib_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = make_url(PG_URL).set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("CHAT_HISTORY_DB_URL", url)
    cfg = Config(str(default_alembic_ini()))
    engine = create_engine(url)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn  # keeps env.py off logging.fileConfig
        command.upgrade(cfg, "head")
    engine.dispose()
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


@pytest.fixture
def replicas(pg_url):
    """Two repositories on independent engines: two replicas, one database."""
    engines = [create_engine(pg_url, pool_size=5) for _ in range(2)]
    yield [PromptLibraryRepository(sessionmaker(bind=e, expire_on_commit=False)) for e in engines]
    for e in engines:
        e.dispose()


def _race(calls):
    """Start every call at once; return (results, exceptions)."""
    results, errors = [], []
    barrier = threading.Barrier(len(calls))

    def run(fn):
        barrier.wait()
        try:
            results.append(fn())
        except Exception as e:  # collected for the assertions
            errors.append(e)

    threads = [threading.Thread(target=run, args=(fn,)) for fn in calls]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, errors


# --- Publication ----------------------------------------------------------


def _assert_versions_consistent(repo, prompt_id, expected):
    versions = repo.list_versions(prompt_id)
    assert sorted(v["version_number"] for v in versions) == list(range(1, expected + 1))
    prompt = repo.get_prompt(prompt_id)
    assert prompt["latest_version_number"] == expected
    assert prompt["latest_version_id"] == versions[0]["id"]


def _publish_many(repos, prompt_id, per_worker=5):
    def worker(repo):
        return lambda: [repo.publish_version(prompt_id, "editor@example.com", "race", 1) for _ in range(per_worker)]

    return _race([worker(r) for r in repos])


def test_concurrent_publication_duckdb(duck):
    prompt = duck.create_prompt(OWNER, "Race", None, "body")
    _, errors = _publish_many([duck] * 4, prompt["id"])
    assert errors == []
    _assert_versions_consistent(duck, prompt["id"], 20)


def test_concurrent_publication_across_replicas_postgresql(replicas):
    prompt = replicas[0].create_prompt(OWNER, "Race", None, "body")
    _, errors = _publish_many(replicas * 3, prompt["id"])
    assert errors == []
    _assert_versions_consistent(replicas[1], prompt["id"], 30)


# --- Sharing --------------------------------------------------------------


@pytest.mark.parametrize("backend", ["duckdb", "postgresql"])
def test_concurrent_sharing_updates_have_one_winner(backend, request):
    repos = [request.getfixturevalue("duck")] * 2 if backend == "duckdb" else request.getfixturevalue("replicas")
    prompt = repos[0].create_prompt(OWNER, "Shared", None, "body")
    sets = [[group("eng")], [group("ops"), user("bob@example.com", "edit")]]
    results, errors = _race([
        lambda i=i: repos[i].replace_grants(prompt["id"], sets[i], OWNER, 0) for i in range(2)
    ])
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], PromptConflictError) and errors[0].kind == "sharing"
    revision, winner = results[0]
    assert revision == 1
    stored = repos[1].list_grants(prompt["id"])
    assert stored == winner
    keys = [(g["grantee_type"], g["grantee"]) for g in stored]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("backend", ["duckdb", "postgresql"])
def test_stale_sharing_update_cannot_restore_a_revoked_grant(backend, request):
    repos = [request.getfixturevalue("duck")] * 2 if backend == "duckdb" else request.getfixturevalue("replicas")
    prompt = repos[0].create_prompt(OWNER, "Shared", None, "body")
    repos[0].replace_grants(prompt["id"], [group("eng"), user("alice@example.com")], OWNER, 0)
    # Manager A loads sharing at revision 1 ...
    revision_a, _ = repos[0].get_sharing_state(prompt["id"])
    # ... the owner revokes Alice (revision 2) ...
    repos[1].replace_grants(prompt["id"], [group("eng")], OWNER, revision_a)
    # ... and A saves a set computed from its stale view, still listing Alice.
    with pytest.raises(PromptConflictError):
        repos[0].replace_grants(
            prompt["id"], [group("eng"), user("alice@example.com"), user("bob@example.com")], OWNER, revision_a
        )
    assert [g["grantee"] for g in repos[1].list_grants(prompt["id"])] == ["eng"]


def test_sharing_waits_on_the_postgresql_row_lock(replicas, pg_url):
    """A sharing update blocks behind another transaction holding the prompt row."""
    prompt = replicas[0].create_prompt(OWNER, "Locked", None, "body")
    holder = create_engine(pg_url)
    outcome = {}
    try:
        with holder.connect() as conn:
            tx = conn.begin()
            conn.execute(
                text("UPDATE prompt_library_prompts SET sharing_revision = sharing_revision + 1 WHERE id = :id"),
                {"id": prompt["id"]},
            )

            def contender():
                try:
                    outcome["result"] = replicas[1].replace_grants(prompt["id"], [group("eng")], OWNER, 0)
                except PromptConflictError as e:
                    outcome["error"] = e

            thread = threading.Thread(target=contender)
            thread.start()
            time.sleep(0.5)
            assert thread.is_alive(), "the update must wait for the row lock"
            tx.commit()
            thread.join(timeout=10)
        assert isinstance(outcome.get("error"), PromptConflictError)
        assert replicas[0].list_grants(prompt["id"]) == []
    finally:
        holder.dispose()


@pytest.mark.parametrize("backend", ["duckdb", "postgresql"])
def test_grant_changes_are_diffs_under_the_unique_constraint(backend, request):
    repo = request.getfixturevalue("duck") if backend == "duckdb" else request.getfixturevalue("replicas")[0]
    prompt = repo.create_prompt(OWNER, "Diff", None, "body")
    revision, _ = repo.replace_grants(prompt["id"], [group("eng"), user("a@example.com")], OWNER, 0)
    revision, _ = repo.replace_grants(prompt["id"], [group("eng"), user("a@example.com", "edit")], OWNER, revision)
    revision, grants = repo.replace_grants(prompt["id"], [user("a@example.com", "edit"), group("Ops")], OWNER, revision)
    assert revision == 3
    assert [(g["grantee"], g["permission"]) for g in grants] == [("Ops", "use"), ("a@example.com", "edit")]
    # The constraint itself rejects a duplicate row written behind the repository.
    from atlas.modules.chat_history.models import PromptLibraryGrantRecord

    with repo._session() as session:
        session.add(PromptLibraryGrantRecord(prompt_id=prompt["id"], grantee_type="group", grantee="Ops",
                                             permission="use", created_by=OWNER))
        with pytest.raises(Exception) as exc:
            session.commit()
    assert isinstance(exc.value, IntegrityError) or "Constraint" in type(exc.value).__name__ or "constraint" in str(exc.value).lower()


# --- Drafts ---------------------------------------------------------------


@pytest.mark.parametrize("backend", ["duckdb", "postgresql"])
def test_concurrent_editors_cannot_overwrite_each_other(backend, request):
    repos = [request.getfixturevalue("duck")] * 2 if backend == "duckdb" else request.getfixturevalue("replicas")
    prompt = repos[0].create_prompt(OWNER, "Draft", None, "v1")
    results, errors = _race([
        lambda i=i: repos[i].update_prompt(prompt["id"], 1, draft_content=f"editor {i}") for i in range(2)
    ])
    assert len(results) == 1 and len(errors) == 1
    assert isinstance(errors[0], PromptConflictError) and errors[0].kind == "draft"
    stored = repos[1].get_prompt(prompt["id"])
    assert stored["draft_revision"] == 2 and stored["draft_content"] == results[0]["draft_content"]


@pytest.mark.parametrize("backend", ["duckdb", "postgresql"])
def test_publish_requires_the_reviewed_draft(backend, request):
    repo = request.getfixturevalue("duck") if backend == "duckdb" else request.getfixturevalue("replicas")[0]
    prompt = repo.create_prompt(OWNER, "Draft", None, "reviewed text")
    # Someone else saves new text after the publisher reviewed revision 1.
    repo.update_prompt(prompt["id"], 1, draft_content="unreviewed text")
    with pytest.raises(PromptConflictError) as exc:
        repo.publish_version(prompt["id"], OWNER, None, 1)
    assert exc.value.kind == "draft"
    assert repo.list_versions(prompt["id"]) == []
    version = repo.publish_version(prompt["id"], OWNER, None, 2)
    assert version["content"] == "unreviewed text"


# --- Save-and-publish (issue #1169, third review) -------------------------


@pytest.mark.parametrize("backend", ["duckdb", "postgresql"])
@pytest.mark.asyncio
async def test_save_and_publish_never_publishes_another_editors_unseen_save(backend, request):
    """Editor A saves, editor B saves right after, A publishes "what it saved".

    The save response used to be a fresh read taken after the commit, so it
    could carry B's text and revision; the editor then published B's unseen
    change under A's name. The response is now the compare-and-set's own
    snapshot, so A's publish names A's revision and is refused.
    """
    from atlas.modules.prompts.prompt_library_policy import PromptConflict
    from atlas.modules.prompts.prompt_library_service import PromptLibraryService

    repos = [request.getfixturevalue("duck")] * 2 if backend == "duckdb" else request.getfixturevalue("replicas")

    async def everyone(user_email, group_name):
        return False

    editor_a = PromptLibraryService(repos[0], group_check=everyone)
    prompt = await editor_a.create_prompt(OWNER, "Draft", "v1")
    original_update = repos[0].update_prompt

    def update_then_b_saves(prompt_id, expected, *args, **kwargs):
        saved = original_update(prompt_id, expected, *args, **kwargs)
        # B lands between A's commit and anything A does next.
        # (Unbound call: on DuckDB both editors share one repository object.)
        PromptLibraryRepository.update_prompt(
            repos[1], prompt_id, saved["draft_revision"], draft_content="B's unseen text"
        )
        return saved

    repos[0].update_prompt = update_then_b_saves
    try:
        saved = await editor_a.update_prompt(OWNER, prompt["id"], 1, content="A's text")
    finally:
        repos[0].update_prompt = original_update
    assert saved["draft_content"] == "A's text"
    assert saved["draft_revision"] == 2
    with pytest.raises(PromptConflict):
        await editor_a.publish(OWNER, prompt["id"], saved["draft_revision"], "A publishes")
    assert repos[0].list_versions(prompt["id"]) == []
    # Without interference the snapshot's revision publishes exactly that text.
    again = await editor_a.update_prompt(OWNER, prompt["id"], 3, content="A reviewed B")
    version = await editor_a.publish(OWNER, prompt["id"], again["draft_revision"], None)
    assert version["content"] == "A reviewed B"


def test_save_snapshot_is_read_under_the_postgresql_row_lock(replicas):
    """A's snapshot is taken while A holds the row lock.

    A racing save (same expected revision) blocks until A commits and then
    loses; a save made after A's commit cannot leak into A's response.
    """
    a, b = replicas
    prompt = a.create_prompt(OWNER, "Draft", None, "v1")
    b_result = {}
    original = a.prompt_to_dict

    def snapshot_while_b_waits(record):
        if record.draft_content == "A's text" and "thread" not in b_result:
            def b_save():
                try:
                    b_result["value"] = b.update_prompt(prompt["id"], 1, draft_content="B's text")
                except Exception as e:  # collected for the assertion
                    b_result["value"] = e

            thread = threading.Thread(target=b_save)
            b_result["thread"] = thread
            thread.start()
            time.sleep(0.5)  # B is now blocked on A's row lock
            assert "value" not in b_result
        return original(record)

    a.prompt_to_dict = snapshot_while_b_waits
    try:
        saved = a.update_prompt(prompt["id"], 1, draft_content="A's text")
    finally:
        a.prompt_to_dict = original
    b_result["thread"].join(10)
    assert saved["draft_content"] == "A's text" and saved["draft_revision"] == 2
    assert isinstance(b_result["value"], PromptConflictError)
    # B reloads and saves on top of A; A's publish of revision 2 is refused.
    later = b.update_prompt(prompt["id"], 2, draft_content="B's text")
    assert later["draft_revision"] == 3
    assert saved["draft_content"] == "A's text"
    with pytest.raises(PromptConflictError):
        a.publish_version(prompt["id"], OWNER, None, saved["draft_revision"])
