"""Revocation racing a write, and storage bounds (issue #1169, review notes 7 and 8).

``update_prompt`` and ``publish`` authorized the editor, then wrote in a
separate transaction: a revocation landing in between let a former editor's
save or publication through. Edit rights come only from ownership or a direct
grant, so they are now re-checked inside the write's own transaction (under
the prompt's row lock on PostgreSQL, which sharing changes also take).

Configurable bounds: prompts per owner (archived included) and published
versions per prompt; 0 means unlimited.
"""

import os
import threading
import time
import uuid

import pytest
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import sessionmaker

from atlas.modules.chat_history.database import reset_engine
from atlas.modules.prompts.prompt_library_service import PromptLibraryService

OWNER = "owner@example.com"
ALICE = "alice@example.com"
PG_URL = os.environ.get("ATLAS_TEST_POSTGRES_URL")


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


async def _member(user, group):
    return False


def _lib(tmp_path, limits=None):
    from atlas.modules.chat_history import PromptLibraryRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'races.db'}")
    kwargs = {"limits": (lambda: limits)} if limits is not None else {}
    return PromptLibraryService(PromptLibraryRepository(get_session_factory()), group_check=_member,
                                group_candidates=lambda: [], **kwargs)


async def _shared_with_editor(lib, permission="edit"):
    prompt = await lib.create_prompt(OWNER, "Reviewer", "v1 text")
    sharing = await lib.get_sharing(OWNER, prompt["id"])
    await lib.set_sharing(OWNER, prompt["id"], [], [{"email": ALICE, "permission": permission}], sharing["revision"])
    return prompt


def _revoke_right_after_authorizing(lib, monkeypatch, prompt_id):
    """The revocation lands in the window between the service's check and its write."""
    original = lib._authorize
    fired = []

    async def authorize_then_revoke(user_email, pid, minimum, history=False):
        result = await original(user_email, pid, minimum, history=history)
        if user_email == ALICE and not fired:  # once, and not for the owner's own revocation
            fired.append(True)
            state = await lib.get_sharing(OWNER, prompt_id)
            await lib.set_sharing(OWNER, prompt_id, [], [], state["revision"])
        return result

    monkeypatch.setattr(lib, "_authorize", authorize_then_revoke)


@pytest.mark.asyncio
async def test_a_publication_racing_a_revocation_is_refused(tmp_path, monkeypatch):
    from atlas.modules.prompts.prompt_library_policy import PromptLibraryError

    lib = _lib(tmp_path)
    prompt = await _shared_with_editor(lib)
    detail = await lib.get_prompt(ALICE, prompt["id"])
    _revoke_right_after_authorizing(lib, monkeypatch, prompt["id"])
    with pytest.raises(PromptLibraryError) as exc:
        await lib.publish(ALICE, prompt["id"], detail["draft_revision"], "sneaky")
    assert "permission" in exc.value.message
    monkeypatch.undo()
    assert (await lib.list_versions(OWNER, prompt["id"]))["total"] == 0  # nothing was published


@pytest.mark.asyncio
async def test_a_draft_save_racing_a_revocation_is_refused(tmp_path, monkeypatch):
    from atlas.modules.prompts.prompt_library_policy import PromptLibraryError

    lib = _lib(tmp_path)
    prompt = await _shared_with_editor(lib)
    detail = await lib.get_prompt(ALICE, prompt["id"])
    _revoke_right_after_authorizing(lib, monkeypatch, prompt["id"])
    with pytest.raises(PromptLibraryError):
        await lib.update_prompt(ALICE, prompt["id"], detail["draft_revision"], content="REVOKED EDITOR TEXT")
    monkeypatch.undo()
    stored = await lib.get_prompt(OWNER, prompt["id"])
    assert stored["draft_content"] == "v1 text" and stored["draft_revision"] == detail["draft_revision"]


@pytest.mark.asyncio
@pytest.mark.parametrize("permission", ["edit", "manage"])
async def test_current_editors_and_owners_still_write(tmp_path, permission):
    lib = _lib(tmp_path)
    prompt = await _shared_with_editor(lib, permission)
    for user in (ALICE, OWNER):
        detail = await lib.get_prompt(user, prompt["id"])
        saved = await lib.update_prompt(user, prompt["id"], detail["draft_revision"], content=f"by {user}")
        await lib.publish(user, prompt["id"], saved["draft_revision"], f"by {user}")
    assert (await lib.list_versions(OWNER, prompt["id"]))["total"] == 2


# --- PostgreSQL: the check runs under the row lock a sharing change holds ---------


@pytest.fixture
def pg_url():
    if not PG_URL:
        pytest.skip("ATLAS_TEST_POSTGRES_URL not set")
    from alembic.config import Config

    from alembic import command
    from atlas.modules.chat_history.schema_migration import default_alembic_ini

    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    name = f"atlas_race_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = make_url(PG_URL).set(database=name).render_as_string(hide_password=False)
    cfg = Config(str(default_alembic_ini()))
    engine = create_engine(url)
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    engine.dispose()
    yield url
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
    admin.dispose()


@pytest.mark.parametrize("operation", ["publish", "update"])
def test_a_write_waits_for_an_in_flight_revocation_postgresql(pg_url, operation):
    from atlas.modules.chat_history import PromptLibraryRepository
    from atlas.modules.chat_history.prompt_library_storage import PromptConflictError

    engine = create_engine(pg_url)
    try:
        repo = PromptLibraryRepository(sessionmaker(bind=engine, expire_on_commit=False))
        prompt = repo.create_prompt(OWNER, "T", None, "v1")
        with engine.begin() as conn:
            conn.execute(text(
                "INSERT INTO prompt_library_grants (id, prompt_id, grantee_type, grantee, permission, created_by, "
                "created_at) VALUES (:id, :p, 'user', :u, 'edit', :o, now())"
            ), {"id": str(uuid.uuid4()), "p": prompt["id"], "u": ALICE, "o": OWNER})
        done = {}
        with engine.connect() as revoking:
            tx = revoking.begin()
            # A sharing change: bump the revision (takes the prompt's row lock), drop the grant.
            revoking.execute(text(
                "UPDATE prompt_library_prompts SET sharing_revision = sharing_revision + 1 WHERE id = :p"
            ), {"p": prompt["id"]})
            revoking.execute(text("DELETE FROM prompt_library_grants WHERE prompt_id = :p"), {"p": prompt["id"]})

            def write():
                try:
                    if operation == "publish":
                        done["result"] = repo.publish_version(prompt["id"], ALICE, "x", 1, True)
                    else:
                        done["result"] = repo.update_prompt(prompt["id"], 1, draft_content="REVOKED", editor_email=ALICE)
                except PromptConflictError as e:
                    done["error"] = e.kind

            thread = threading.Thread(target=write)
            thread.start()
            time.sleep(0.5)
            assert not done, "the write did not wait for the sharing change's row lock"
            tx.commit()
        thread.join(30)
        assert done == {"error": "access"}
        with engine.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM prompt_library_versions")).scalar() == 0
            assert conn.execute(text(
                "SELECT draft_content FROM prompt_library_prompts WHERE id = :p"), {"p": prompt["id"]}
            ).scalar() == "v1"
    finally:
        engine.dispose()


# --- Note 8: storage bounds ----------------------------------------------------------


@pytest.mark.asyncio
async def test_prompts_per_owner_are_bounded_including_archived_and_forks(tmp_path):
    from atlas.modules.prompts.prompt_library_policy import PromptValidationError

    lib = _lib(tmp_path, limits={"prompts_per_owner": 2, "versions_per_prompt": 0})
    first = await lib.create_prompt(OWNER, "One", "x")
    await lib.create_prompt(OWNER, "Two", "x")
    await lib.archive(OWNER, first["id"])  # archived still counts
    with pytest.raises(PromptValidationError) as exc:
        await lib.create_prompt(OWNER, "Three", "x")
    assert "at most 2" in exc.value.message
    detail = await lib.get_prompt(OWNER, (await lib.list_prompts(OWNER, scope="mine"))["prompts"][0]["id"])
    await lib.publish(OWNER, detail["id"], detail["draft_revision"], "v1")
    with pytest.raises(PromptValidationError):
        await lib.fork(OWNER, detail["id"])
    assert (await lib.create_prompt(ALICE, "Alice's", "x"))["owner_email"] == ALICE  # per owner


@pytest.mark.asyncio
async def test_versions_per_prompt_are_bounded(tmp_path):
    from atlas.modules.prompts.prompt_library_policy import PromptValidationError

    lib = _lib(tmp_path, limits={"prompts_per_owner": 0, "versions_per_prompt": 2})
    prompt = await lib.create_prompt(OWNER, "T", "x")
    for n in range(2):
        detail = await lib.get_prompt(OWNER, prompt["id"])
        saved = await lib.update_prompt(OWNER, prompt["id"], detail["draft_revision"], content=f"v{n}")
        await lib.publish(OWNER, prompt["id"], saved["draft_revision"], f"v{n}")
    detail = await lib.get_prompt(OWNER, prompt["id"])
    with pytest.raises(PromptValidationError) as exc:
        await lib.publish(OWNER, prompt["id"], detail["draft_revision"], "v3")
    assert "at most 2 published versions" in exc.value.message


@pytest.mark.asyncio
async def test_zero_and_unset_limits_are_unlimited(tmp_path):
    lib = _lib(tmp_path, limits={"prompts_per_owner": 0, "versions_per_prompt": 0})
    for i in range(5):
        await lib.create_prompt(OWNER, f"P{i}", "x")
    assert (await lib.list_prompts(OWNER, scope="mine"))["total"] == 5


def test_default_limits_are_generous_and_configurable(monkeypatch):
    from atlas.modules.config.settings import AppSettings

    defaults = AppSettings()
    assert defaults.prompt_library_max_prompts_per_owner == 1000
    assert defaults.prompt_library_max_versions_per_prompt == 1000
    monkeypatch.setenv("PROMPT_LIBRARY_MAX_PROMPTS_PER_OWNER", "0")
    monkeypatch.setenv("PROMPT_LIBRARY_MAX_VERSIONS_PER_PROMPT", "25")
    configured = AppSettings()
    assert configured.prompt_library_max_prompts_per_owner == 0
    assert configured.prompt_library_max_versions_per_prompt == 25
