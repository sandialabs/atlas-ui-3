"""The library listing never loads prompt bodies (issue #1169, fifth review).

Listing, searching and paging used to read every page entry's full
``draft_content`` and its latest version's full ``content`` only to compare
them for ``has_unpublished_changes`` and throw them away. Now that comparison
runs in the database and the listing queries return only the columns the
summary needs. These tests watch the result columns of every statement a
listing runs, check the statement count does not grow with the page (no N+1),
and check that ordering and ``has_unpublished_changes`` are unchanged -- on
DuckDB and, with ``ATLAS_TEST_POSTGRES_URL``, PostgreSQL.
"""

import os
import uuid

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, make_url, text
from sqlalchemy.orm import sessionmaker

from alembic import command
from atlas.modules.chat_history.database import reset_engine
from atlas.modules.chat_history.schema_migration import default_alembic_ini
from atlas.modules.prompts.prompt_library_service import PromptLibraryService

PG_URL = os.environ.get("ATLAS_TEST_POSTGRES_URL")
OWNER = "owner@example.com"
ALICE = "alice@example.com"
BIG = "x" * 49_000  # a large prompt body (the limit is 50,000 characters)


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


async def _member(user, group):
    return user in {OWNER, ALICE} and group == "eng"


class Statements:
    """Every statement an engine runs, with the columns it returned."""

    def __init__(self, engine):
        self.runs = []
        self.recording = False
        event.listen(engine, "after_cursor_execute", self._after)

    def _after(self, conn, cursor, statement, parameters, context, executemany):
        if self.recording:
            columns = [d[0].lower() for d in (cursor.description or [])]
            self.runs.append((statement, columns))

    def returned_columns(self):
        return [c for _, cols in self.runs for c in cols]


def _duck(tmp_path):
    from atlas.modules.chat_history import PromptLibraryRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'listing.db'}")
    factory = get_session_factory()
    return PromptLibraryService(PromptLibraryRepository(factory), group_check=_member,
                                group_candidates=lambda: ["eng"]), factory.kw["bind"]


@pytest.fixture
def pg_service():
    if not PG_URL:
        pytest.skip("ATLAS_TEST_POSTGRES_URL not set")
    from atlas.modules.chat_history import PromptLibraryRepository

    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    name = f"atlas_list_{uuid.uuid4().hex[:10]}"
    with admin.connect() as conn:
        conn.execute(text(f"CREATE DATABASE {name}"))
    url = make_url(PG_URL).set(database=name).render_as_string(hide_password=False)
    engine = create_engine(url)
    cfg = Config(str(default_alembic_ini()))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
    try:
        yield PromptLibraryService(
            PromptLibraryRepository(sessionmaker(bind=engine, expire_on_commit=False)),
            group_check=_member, group_candidates=lambda: ["eng"],
        ), engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        admin.dispose()


async def _populate(lib, count):
    """``count`` shared, published prompts; every third has unpublished edits."""
    ids = []
    for n in range(count):
        prompt = await lib.create_prompt(OWNER, f"Prompt {n:03d}", BIG + str(n))
        detail = await lib.get_prompt(OWNER, prompt["id"])
        await lib.publish(OWNER, prompt["id"], detail["draft_revision"])
        if n % 3 == 0:
            detail = await lib.get_prompt(OWNER, prompt["id"])
            await lib.update_prompt(OWNER, prompt["id"], detail["draft_revision"], content=BIG + "edited")
        sharing = await lib.get_sharing(OWNER, prompt["id"])
        await lib.set_sharing(OWNER, prompt["id"], ["eng"], [], sharing["revision"])
        ids.append(prompt["id"])
    unpublished = await lib.create_prompt(OWNER, "Draft only", BIG)
    return ids, unpublished["id"]


async def _check_listing(lib, engine):
    ids, draft_only = await _populate(lib, 12)
    watch = Statements(engine)

    async def listed(user, **kwargs):
        watch.runs.clear()
        watch.recording = True
        try:
            return await lib.list_prompts(user, **kwargs)
        finally:
            watch.recording = False

    # Bodies are never returned by any listing statement, for any caller.
    for user, kwargs in ((OWNER, {}), (ALICE, {}), (OWNER, {"query": "prompt 00"}), (OWNER, {"limit": 5, "offset": 5})):
        await listed(user, **kwargs)
        returned = watch.returned_columns()
        # SQLAlchemy labels columns "<table>_<column>", so match the name part.
        bodies = [c for c in returned if c == "content" or c.endswith("_content")]
        assert bodies == [], (user, kwargs, bodies)
        assert returned, "the listing ran no statements?"

    # has_unpublished_changes and ordering are what they were.
    page = await listed(OWNER, limit=50)
    by_id = {p["id"]: p for p in page["prompts"]}
    for n, prompt_id in enumerate(ids):
        assert by_id[prompt_id]["has_unpublished_changes"] is (n % 3 == 0), n
    assert by_id[draft_only]["has_unpublished_changes"] is True
    stamps = [p["updated_at"] for p in page["prompts"]]
    assert stamps == sorted(stamps, reverse=True)
    assert all("draft_content" not in p for p in page["prompts"])

    # No N+1: a page of 2 and a page of 12 run the same number of statements.
    await listed(OWNER, limit=2)
    small = len(watch.runs)
    await listed(OWNER, limit=13)
    assert len(watch.runs) == small, (small, len(watch.runs))

    # The detail view still has the full text where it is asked for.
    detail = await lib.get_prompt(OWNER, ids[0])
    assert detail["draft_content"] == BIG + "edited" and detail["latest_content"] == BIG + "0"


@pytest.mark.asyncio
async def test_listing_loads_no_prompt_bodies_duckdb(tmp_path):
    lib, engine = _duck(tmp_path)
    await _check_listing(lib, engine)


@pytest.mark.asyncio
async def test_listing_loads_no_prompt_bodies_postgresql(pg_service):
    lib, engine = pg_service
    await _check_listing(lib, engine)
