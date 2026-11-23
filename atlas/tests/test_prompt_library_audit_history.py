"""Audit history cannot be flooded by refused uses (issue #1169, review finding 4).

Every refused chat turn on an existing library entry records ``use_denied``.
That is security evidence, but one row per refusal let a single user (or a
script) push sharing, publishing and management events out of the newest-200
view. Refused uses are now one counted row per actor, prompt and hour (the
first insert wins a unique key, across replicas; later ones increment
``occurrences``), and the trail is paged and filterable by category.
"""

import asyncio
import os
import threading
import time
import uuid

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import sessionmaker

from atlas.modules.chat_history.database import reset_engine
from atlas.modules.prompts import prompt_library_service as service_module
from atlas.modules.prompts.prompt_library_service import PromptLibraryService, PromptUnavailable

OWNER = "owner@example.com"
ALICE = "alice@example.com"
MALLORY = "mallory@example.com"
PG_URL = os.environ.get("ATLAS_TEST_POSTGRES_URL")


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


class Groups:
    def __init__(self):
        self.members = {"eng": {OWNER, ALICE}}

    async def check(self, user, group):
        return user in self.members.get(group, set())


@pytest.fixture
def lib(tmp_path):
    from atlas.modules.chat_history import PromptLibraryRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'audit.db'}")
    groups = Groups()
    return PromptLibraryService(PromptLibraryRepository(get_session_factory()), group_check=groups.check,
                                group_candidates=lambda: ["eng"])


async def _managed_prompt(lib):
    prompt = await lib.create_prompt(OWNER, "Reviewer", "text")
    detail = await lib.get_prompt(OWNER, prompt["id"])
    await lib.publish(OWNER, prompt["id"], detail["draft_revision"], "first")
    sharing = await lib.get_sharing(OWNER, prompt["id"])
    await lib.set_sharing(OWNER, prompt["id"], ["eng"], [], sharing["revision"])
    return prompt


async def _refused(lib, user, prompt_id, times):
    for _ in range(times):
        with pytest.raises(PromptUnavailable):
            await lib.resolve_for_execution(user, prompt_id)


@pytest.mark.asyncio
async def test_a_flood_of_refused_uses_is_one_counted_row(lib):
    prompt = await _managed_prompt(lib)
    await _refused(lib, MALLORY, prompt["id"], 60)
    page = await lib.list_audit_events(OWNER, prompt["id"])
    denied = [e for e in page["events"] if e["event_type"] == "use_denied"]
    assert len(denied) == 1 and denied[0]["occurrences"] == 60
    assert denied[0]["actor_email"] == MALLORY and denied[0]["last_occurred_at"]
    # The management events are all still in the default (newest-200) view.
    kinds = {e["event_type"] for e in page["events"]}
    assert {"create", "publish", "share"} <= kinds
    assert page["total"] == len(page["events"]) == 4


@pytest.mark.asyncio
async def test_refusals_cannot_push_management_events_out_of_the_newest_200(lib):
    """Black box (the repository's newest-200 list exists before and after)."""
    prompt = await _managed_prompt(lib)
    await _refused(lib, MALLORY, prompt["id"], 205)  # more than the newest-200 view
    newest = lib.repository.list_audit_events(prompt["id"])
    kinds = [e["event_type"] for e in newest]
    assert {"create", "publish", "share"} <= set(kinds), "refused uses displaced the management events"
    assert kinds.count("use_denied") == 1


@pytest.mark.asyncio
async def test_many_identities_cannot_hide_management_events(lib):
    prompt = await _managed_prompt(lib)
    for i in range(40):  # 40 distinct identities, each refused
        await _refused(lib, f"bot{i}@example.com", prompt["id"], 1)
    everything = await lib.list_audit_events(OWNER, prompt["id"])
    assert everything["total"] == 43
    management = await lib.list_audit_events(OWNER, prompt["id"], category="management")
    assert management["total"] == 3
    assert {e["event_type"] for e in management["events"]} == {"create", "publish", "share"}
    denied = await lib.list_audit_events(OWNER, prompt["id"], category="denied", limit=10, offset=35)
    assert denied["total"] == 40 and len(denied["events"]) == 5


@pytest.mark.asyncio
async def test_a_new_window_starts_a_new_row(lib, monkeypatch):
    monkeypatch.setattr(service_module, "USE_DENIED_DEDUP_WINDOW_S", 1)
    prompt = await _managed_prompt(lib)
    await _refused(lib, MALLORY, prompt["id"], 3)
    await asyncio.sleep(1.1)
    await _refused(lib, MALLORY, prompt["id"], 2)
    denied = (await lib.list_audit_events(OWNER, prompt["id"], category="denied"))["events"]
    assert sorted(e["occurrences"] for e in denied) in ([2, 3], [1, 2, 2], [1, 1, 3])  # window edges
    assert sum(e["occurrences"] for e in denied) == 5


@pytest.mark.asyncio
async def test_refusals_never_reveal_whether_a_prompt_exists(lib):
    prompt = await _managed_prompt(lib)
    with pytest.raises(PromptUnavailable) as existing:
        await lib.resolve_for_execution(MALLORY, prompt["id"])
    with pytest.raises(PromptUnavailable) as missing:
        await lib.resolve_for_execution(MALLORY, str(uuid.uuid4()))
    assert existing.value.message == missing.value.message
    assert lib.repository.list_audit_events(str(uuid.uuid4())) == []  # nothing recorded for a missing id


@pytest.mark.asyncio
async def test_other_events_are_never_merged(lib):
    prompt = await _managed_prompt(lib)
    for _ in range(3):
        detail = await lib.get_prompt(OWNER, prompt["id"])
        await lib.update_prompt(OWNER, prompt["id"], detail["draft_revision"], content=f"text {time.time()}")
    events = (await lib.list_audit_events(OWNER, prompt["id"], category="management"))["events"]
    assert sum(1 for e in events if e["event_type"] == "update_draft") == 3
    assert all(e["occurrences"] == 1 for e in events)


def test_the_route_pages_filters_and_keeps_manage_permission(lib):
    from atlas.core.log_sanitizer import get_current_user
    from atlas.routes import prompt_library_routes

    prompt = asyncio.run(_managed_prompt(lib))
    asyncio.run(_refused(lib, MALLORY, prompt["id"], 5))
    app = FastAPI()
    app.include_router(prompt_library_routes.router)

    async def identity(request: Request) -> str:
        return request.headers["x-test-user"]

    app.dependency_overrides[get_current_user] = identity
    prompt_library_routes._get_service, original = (lambda: lib), prompt_library_routes._get_service
    prompt_library_routes._library_enabled, original_enabled = (lambda: True), prompt_library_routes._library_enabled
    try:
        client = TestClient(app)
        url = f"/api/prompts/{prompt['id']}/audit"
        body = client.get(url, params={"category": "management", "limit": 2}, headers={"x-test-user": OWNER}).json()
        assert body["total"] == 3 and len(body["events"]) == 2 and body["category"] == "management"
        body = client.get(url, params={"category": "denied"}, headers={"x-test-user": OWNER}).json()
        assert body["events"][0]["occurrences"] == 5
        assert client.get(url, params={"category": "bogus"}, headers={"x-test-user": OWNER}).status_code == 400
        assert client.get(url, params={"limit": 0}, headers={"x-test-user": OWNER}).status_code == 400
        assert client.get(url, headers={"x-test-user": ALICE}).status_code == 403  # use only
        assert client.get(url, headers={"x-test-user": MALLORY}).status_code == 404  # no access: no existence
    finally:
        prompt_library_routes._get_service = original
        prompt_library_routes._library_enabled = original_enabled


# --- PostgreSQL: replicas recording the same refusal at once -----------------------


@pytest.fixture
def pg_url():
    if not PG_URL:
        pytest.skip("ATLAS_TEST_POSTGRES_URL not set")
    from alembic.config import Config

    from alembic import command
    from atlas.modules.chat_history.schema_migration import default_alembic_ini

    admin = create_engine(PG_URL, isolation_level="AUTOCOMMIT")
    name = f"atlas_audit_{uuid.uuid4().hex[:10]}"
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


def test_concurrent_replicas_record_one_counted_row_postgresql(pg_url):
    from atlas.modules.chat_history import PromptLibraryRepository

    engines = [create_engine(pg_url) for _ in range(4)]
    try:
        repos = [PromptLibraryRepository(sessionmaker(bind=e, expire_on_commit=False)) for e in engines]
        prompt = repos[0].create_prompt(OWNER, "T", None, "body")
        repos[0].add_audit_event(prompt["id"], "share", OWNER, None, {"groups_added": ["eng"]})
        start = threading.Barrier(16)
        errors = []

        def refuse(repo, actor):
            start.wait()
            try:
                for _ in range(10):
                    repo.add_audit_event(prompt["id"], "use_denied", actor, None, None, 3600)
            except Exception as e:  # pragma: no cover - reported below
                errors.append(e)

        threads = [
            threading.Thread(target=refuse, args=(repos[i % 4], MALLORY if i < 12 else f"x{i}@example.com"))
            for i in range(16)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        assert errors == []
        page = repos[1].page_audit_events(prompt["id"], 200, 0, ["use_denied"], None)
        mallory = [e for e in page["events"] if e["actor_email"] == MALLORY]
        assert len(mallory) == 1 and mallory[0]["occurrences"] == 120  # 12 threads x 10
        assert page["total"] == 5  # Mallory's row + 4 other actors
        management = repos[2].page_audit_events(prompt["id"], 200, 0, None, ["use", "use_denied"])
        assert [e["event_type"] for e in management["events"]] == ["share"]
    finally:
        for e in engines:
            e.dispose()


@pytest.mark.asyncio
async def test_the_first_refusal_is_kept_when_the_driver_reports_rowcount_minus_one(lib, monkeypatch):
    """DuckDB 1.5.1 (the locked version) reports ``rowcount`` -1 for UPDATE.

    The dedup path once read that -1 from ``Query.update`` as "a row was
    counted" and dropped every refusal. Emulate the driver here so the
    regression is caught whichever DuckDB the environment has.
    """
    from sqlalchemy.orm import Query

    original = Query.update

    def driver_rowcount(self, *args, **kwargs):
        original(self, *args, **kwargs)
        return -1

    monkeypatch.setattr(Query, "update", driver_rowcount)
    prompt = await _managed_prompt(lib)
    await _refused(lib, MALLORY, prompt["id"], 3)
    page = await lib.list_audit_events(OWNER, prompt["id"])
    denied = [e for e in page["events"] if e["event_type"] == "use_denied"]
    assert len(denied) == 1 and denied[0]["occurrences"] == 3
