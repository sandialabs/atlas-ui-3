"""Bounded responses in the team prompt library (issue #1169, fourth review).

Version history is paged and carries no prompt text (one version's text is
fetched on its own); exports hold a bounded number of versions; the groups
offered as sharing targets are checked within a group-count and time bound,
and only groups confirmed as the caller's are ever offered.
"""

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from atlas.modules.chat_history.database import reset_engine
from atlas.modules.prompts import prompt_library_service as service_module
from atlas.modules.prompts.prompt_library_service import (
    PromptLibraryService,
    PromptNotFound,
    PromptValidationError,
)

OWNER = "owner@example.com"
ALICE = "alice@example.com"
MALLORY = "mallory@example.com"


class Groups:
    def __init__(self, members=None, delay=None, fail=()):
        self.members = members or {}
        self.delay = delay or {}
        self.fail = set(fail)
        self.calls = []

    async def check(self, user, group):
        self.calls.append(group)
        if group in self.delay:
            await asyncio.sleep(self.delay[group])
        if group in self.fail:
            raise RuntimeError("authorizer down")
        return user in self.members.get(group, set())


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


def _service(tmp_path, groups, candidates=("eng",)):
    from atlas.modules.chat_history import PromptLibraryRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'bounds.db'}")
    repo = PromptLibraryRepository(get_session_factory())
    return PromptLibraryService(repo, group_check=groups.check, group_candidates=lambda: list(candidates))


async def _history(lib, versions, share_with=("eng",)):
    prompt = await lib.create_prompt(OWNER, "Reviewer", "text v1")
    for n in range(1, versions + 1):
        detail = await lib.get_prompt(OWNER, prompt["id"])
        if n > 1:
            detail = await lib.update_prompt(OWNER, prompt["id"], detail["draft_revision"], content=f"text v{n}")
        await lib.publish(OWNER, prompt["id"], detail["draft_revision"], f"change {n}")
    if share_with:
        sharing = await lib.get_sharing(OWNER, prompt["id"])
        await lib.set_sharing(OWNER, prompt["id"], list(share_with), [], sharing["revision"])
    return prompt


# --- Version history ------------------------------------------------------


@pytest.mark.asyncio
async def test_history_is_paged_newest_first_without_prompt_text(tmp_path):
    lib = _service(tmp_path, Groups({"eng": {OWNER, ALICE}}))
    prompt = await _history(lib, 7)
    first = await lib.list_versions(ALICE, prompt["id"], limit=3, offset=0)
    assert first["total"] == 7 and first["limit"] == 3 and first["offset"] == 0
    assert [v["version_number"] for v in first["versions"]] == [7, 6, 5]
    assert all("content" not in v for v in first["versions"])
    assert set(first["versions"][0]) == {
        "id", "prompt_id", "version_number", "title", "change_description", "author_email", "created_at",
    }
    rest = await lib.list_versions(ALICE, prompt["id"], limit=3, offset=6)
    assert [v["version_number"] for v in rest["versions"]] == [1] and rest["total"] == 7
    past_end = await lib.list_versions(ALICE, prompt["id"], limit=3, offset=50)
    assert past_end["versions"] == [] and past_end["total"] == 7


@pytest.mark.asyncio
async def test_pages_of_a_large_history_are_consistent(tmp_path):
    lib = _service(tmp_path, Groups({"eng": {OWNER}}))
    prompt = await _history(lib, 60, share_with=())
    seen = []
    offset = 0
    while True:
        page = await lib.list_versions(OWNER, prompt["id"], limit=25, offset=offset)
        assert page["total"] == 60 and len(page["versions"]) <= 25
        if not page["versions"]:
            break
        seen.extend(v["version_number"] for v in page["versions"])
        offset += len(page["versions"])
    assert seen == list(range(60, 0, -1))  # every version once, in order
    default = await lib.list_versions(OWNER, prompt["id"])
    assert len(default["versions"]) == service_module.DEFAULT_VERSION_PAGE_SIZE


@pytest.mark.asyncio
async def test_one_versions_full_text_is_still_available(tmp_path):
    lib = _service(tmp_path, Groups({"eng": {OWNER, ALICE}}))
    prompt = await _history(lib, 3)
    page = await lib.list_versions(ALICE, prompt["id"])
    v2 = next(v for v in page["versions"] if v["version_number"] == 2)
    full = await lib.get_version(ALICE, prompt["id"], v2["id"])
    assert full["content"] == "text v2"
    assert {k: full[k] for k in v2} == v2  # the summary is the same version


@pytest.mark.asyncio
@pytest.mark.parametrize("limit, offset", [(0, 0), (201, 0), (10, -1), (True, 0), ("5", 0)])
async def test_page_bounds_are_validated(tmp_path, limit, offset):
    lib = _service(tmp_path, Groups())
    prompt = await _history(lib, 1, share_with=())
    with pytest.raises(PromptValidationError):
        await lib.list_versions(OWNER, prompt["id"], limit=limit, offset=offset)


@pytest.mark.asyncio
async def test_history_and_versions_keep_their_authorization(tmp_path):
    lib = _service(tmp_path, Groups({"eng": {OWNER, ALICE}}))
    prompt = await _history(lib, 2)
    version_id = (await lib.list_versions(OWNER, prompt["id"]))["versions"][0]["id"]
    for call in (
        lambda: lib.list_versions(MALLORY, prompt["id"]),
        lambda: lib.get_version(MALLORY, prompt["id"], version_id),
        lambda: lib.export_prompt(MALLORY, prompt["id"]),
    ):
        with pytest.raises(PromptNotFound):
            await call()


@pytest.mark.asyncio
async def test_export_is_bounded_and_resumable(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "MAX_EXPORT_VERSIONS", 3)
    lib = _service(tmp_path, Groups({"eng": {OWNER}}))
    prompt = await _history(lib, 7, share_with=())
    first = await lib.export_prompt(OWNER, prompt["id"])
    assert [v["version_number"] for v in first["versions"]] == [7, 6, 5]
    assert all(v["content"] == f"text v{v['version_number']}" for v in first["versions"])
    assert first["versions_total"] == 7 and first["versions_truncated"] is True and first["versions_offset"] == 0
    assert first["export_format"] == "atlas-prompt-library/1"
    collected = list(first["versions"])
    offset = 3
    while True:
        page = await lib.export_prompt(OWNER, prompt["id"], offset=offset)
        collected.extend(page["versions"])
        if not page["versions_truncated"]:
            break
        offset += len(page["versions"])
    assert [v["version_number"] for v in collected] == [7, 6, 5, 4, 3, 2, 1]
    small = await lib.export_prompt(OWNER, (await _history(lib, 2, share_with=()))["id"])
    assert small["versions_truncated"] is False and small["versions_total"] == 2


# --- Shareable groups -----------------------------------------------------


@pytest.mark.asyncio
async def test_a_small_group_list_is_checked_completely(tmp_path):
    groups = Groups({"eng": {OWNER}, "ops": {OWNER}})
    lib = _service(tmp_path, groups, candidates=["eng", "ops", "hr", "users"])
    assert await lib.shareable_groups_checked(OWNER) == {"groups": ["eng", "ops"], "incomplete": False}
    assert sorted(groups.calls) == ["eng", "hr", "ops"]  # the everyone group is never checked
    assert await lib.shareable_groups(OWNER) == ["eng", "ops"]


@pytest.mark.asyncio
async def test_a_large_group_list_is_capped_and_marked_incomplete(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "MAX_SHAREABLE_GROUP_CHECKS", 10)
    names = [f"g{n:03d}" for n in range(40)]
    groups = Groups({name: {OWNER} for name in names})
    lib = _service(tmp_path, groups, candidates=names)
    result = await lib.shareable_groups_checked(OWNER)
    assert result["incomplete"] is True
    assert result["groups"] == names[:10]  # deterministic: name order
    assert len(groups.calls) == 10  # the rest were never sent to the authorizer


@pytest.mark.asyncio
async def test_a_slow_authorizer_offers_only_confirmed_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "SHAREABLE_GROUP_CHECK_TIMEOUT_S", 0.3)
    groups = Groups({"eng": {OWNER}, "slow": {OWNER}}, delay={"slow": 5})
    lib = _service(tmp_path, groups, candidates=["eng", "slow"])
    started = asyncio.get_running_loop().time()
    result = await lib.shareable_groups_checked(OWNER)
    assert asyncio.get_running_loop().time() - started < 2
    # "slow" is the caller's group, but its check never finished: not offered.
    assert result == {"groups": ["eng"], "incomplete": True}


@pytest.mark.asyncio
async def test_authorizer_failures_are_not_membership_and_mark_the_result_incomplete(tmp_path):
    groups = Groups({"eng": {OWNER}, "ops": {OWNER}}, fail={"ops"})
    lib = _service(tmp_path, groups, candidates=["eng", "ops"])
    assert await lib.shareable_groups_checked(OWNER) == {"groups": ["eng"], "incomplete": True}


@pytest.mark.asyncio
async def test_a_failed_check_also_marks_the_library_listing_incomplete(tmp_path):
    groups = Groups({"eng": {OWNER, ALICE}})
    lib = _service(tmp_path, groups)
    prompt = await _history(lib, 1)
    groups.fail = {"eng"}
    page = await lib.list_prompts(ALICE)
    assert page["prompts"] == [] and page["incomplete"] is True  # fail closed, and said so
    groups.fail = set()
    page = await lib.list_prompts(ALICE)
    assert [p["id"] for p in page["prompts"]] == [prompt["id"]] and "incomplete" not in page


@pytest.mark.asyncio
async def test_an_unchecked_group_still_cannot_be_shared_with(tmp_path, monkeypatch):
    """Sharing re-checks every added group, whatever the offered list said."""
    from atlas.modules.prompts.prompt_library_policy import PromptLibraryUnavailable

    groups = Groups({"eng": {OWNER}}, fail={"ops"})
    lib = _service(tmp_path, groups, candidates=["eng", "ops"])
    prompt = await lib.create_prompt(OWNER, "Reviewer", "text")
    sharing = await lib.get_sharing(OWNER, prompt["id"])
    with pytest.raises(PromptLibraryUnavailable):  # unknown, so refused (503)
        await lib.set_sharing(OWNER, prompt["id"], ["ops"], [], sharing["revision"])


# --- Routes -----------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    from atlas.core.log_sanitizer import get_current_user
    from atlas.routes import prompt_library_routes

    groups = Groups({"eng": {OWNER, ALICE}})
    lib = _service(tmp_path, groups, candidates=["eng", "ops"])
    monkeypatch.setattr(prompt_library_routes, "_get_service", lambda: lib)
    monkeypatch.setattr(prompt_library_routes, "_library_enabled", lambda: True)
    app = FastAPI()
    app.include_router(prompt_library_routes.router)

    async def identity(request: Request) -> str:
        return request.headers["x-test-user"]

    app.dependency_overrides[get_current_user] = identity
    return TestClient(app), lib


def test_routes_page_history_and_keep_the_groups_shape(client):
    http, lib = client
    prompt = asyncio.run(_history(lib, 4))
    as_alice = {"x-test-user": ALICE}
    page = http.get(f"/api/prompts/{prompt['id']}/versions?limit=2&offset=1", headers=as_alice).json()
    assert page["total"] == 4 and page["limit"] == 2 and page["offset"] == 1
    assert [v["version_number"] for v in page["versions"]] == [3, 2]
    assert all("content" not in v for v in page["versions"])
    one = http.get(f"/api/prompts/{prompt['id']}/versions/{page['versions'][0]['id']}", headers=as_alice).json()
    assert one["version"]["content"] == "text v3"
    for bad in ("limit=0", "limit=201", "offset=-1"):
        assert http.get(f"/api/prompts/{prompt['id']}/versions?{bad}", headers=as_alice).status_code == 422
    assert http.get(f"/api/prompts/{prompt['id']}/versions", headers={"x-test-user": MALLORY}).status_code == 404
    exported = http.get(f"/api/prompts/{prompt['id']}/export", headers={"x-test-user": OWNER}).json()
    assert exported["versions_total"] == 4 and exported["versions_truncated"] is False
    assert http.get("/api/prompts/groups", headers={"x-test-user": OWNER}).json() == {
        "groups": ["eng"], "incomplete": False,
    }


# --- Sharing targets are restricted to configured groups (recommendation 6) ---


async def _owned_prompt(lib):
    return await lib.create_prompt(OWNER, "Reviewer", "text")


async def _share_groups(lib, prompt_id, groups):
    sharing = await lib.get_sharing(OWNER, prompt_id)
    return await lib.set_sharing(OWNER, prompt_id, groups, [], sharing["revision"])


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["not-configured", "eng,ops", "../admin", "EnG", "x" * 100])
async def test_unconfigured_group_names_never_reach_the_authorizer(tmp_path, name):
    from atlas.modules.prompts.prompt_library_policy import PromptForbidden

    groups = Groups({"eng": {OWNER}, name: {OWNER}})  # even a "yes" would not matter
    lib = _service(tmp_path, groups, candidates=["eng", "ops"])
    prompt = await _owned_prompt(lib)
    with pytest.raises(PromptForbidden) as exc:
        await _share_groups(lib, prompt["id"], ["eng", name])
    # One answer for "not configured" and "not a member": no probing which
    # group names exist.
    assert "groups you belong to" in exc.value.message
    assert name not in groups.calls
    assert (await lib.get_sharing(OWNER, prompt["id"]))["groups"] == []


@pytest.mark.asyncio
async def test_configured_groups_are_checked_for_membership(tmp_path):
    from atlas.modules.prompts.prompt_library_policy import PromptForbidden

    groups = Groups({"eng": {OWNER}})
    lib = _service(tmp_path, groups, candidates=["eng", "ops"])
    prompt = await _owned_prompt(lib)
    with pytest.raises(PromptForbidden):
        await _share_groups(lib, prompt["id"], ["ops"])  # configured, but not a member
    assert (await _share_groups(lib, prompt["id"], ["eng"]))["groups"] == ["eng"]
    assert groups.calls.count("eng") == 1 and groups.calls.count("ops") == 1


@pytest.mark.asyncio
async def test_a_slow_authorizer_fails_the_change_closed(tmp_path, monkeypatch):
    from atlas.modules.prompts.prompt_library_policy import PromptLibraryUnavailable

    monkeypatch.setattr(service_module, "SHAREABLE_GROUP_CHECK_TIMEOUT_S", 0.3)
    groups = Groups({"eng": {OWNER}, "slow": {OWNER}}, delay={"slow": 5})
    lib = _service(tmp_path, groups, candidates=["eng", "slow"])
    prompt = await _owned_prompt(lib)
    started = asyncio.get_running_loop().time()
    with pytest.raises(PromptLibraryUnavailable) as exc:
        await _share_groups(lib, prompt["id"], ["eng", "slow"])
    assert asyncio.get_running_loop().time() - started < 2
    assert "could not be checked" in exc.value.message
    assert (await lib.get_sharing(OWNER, prompt["id"]))["groups"] == []  # nothing half-applied


@pytest.mark.asyncio
async def test_an_authorizer_failure_fails_the_change_closed(tmp_path):
    from atlas.modules.prompts.prompt_library_policy import PromptLibraryUnavailable

    groups = Groups({"eng": {OWNER}}, fail={"eng"})
    lib = _service(tmp_path, groups, candidates=["eng"])
    prompt = await _owned_prompt(lib)
    with pytest.raises(PromptLibraryUnavailable):
        await _share_groups(lib, prompt["id"], ["eng"])
    assert (await lib.get_sharing(OWNER, prompt["id"]))["groups"] == []


@pytest.mark.asyncio
async def test_the_status_code_does_not_reveal_which_names_are_configured(tmp_path):
    """Independent review: with the authorizer down, a configured name used to
    get 503 and an unconfigured one 403. Now a request that contains any
    configured name the authorizer could not answer is 503 as a whole, and
    with no configured name to check it is the same 403 as "not a member"."""
    from atlas.modules.prompts.prompt_library_policy import PromptForbidden, PromptLibraryUnavailable

    groups = Groups({"eng": {OWNER}}, fail={"eng", "ops"})
    lib = _service(tmp_path, groups, candidates=["eng", "ops"])
    prompt = await _owned_prompt(lib)
    for request in (["eng"], ["eng", "secret-unconfigured"], ["secret-unconfigured", "ops"]):
        with pytest.raises(PromptLibraryUnavailable):
            await _share_groups(lib, prompt["id"], request)
    with pytest.raises(PromptForbidden) as unconfigured:
        await _share_groups(lib, prompt["id"], ["secret-unconfigured"])
    groups.fail = set()
    with pytest.raises(PromptForbidden) as not_member:
        await _share_groups(lib, prompt["id"], ["ops"])
    assert type(unconfigured.value) is type(not_member.value)
    assert "secret-unconfigured" not in groups.calls
    assert (await lib.get_sharing(OWNER, prompt["id"]))["groups"] == []


@pytest.mark.asyncio
async def test_existing_groups_are_kept_and_removable_without_a_new_check(tmp_path):
    groups = Groups({"eng": {OWNER}})
    candidates = ["eng"]
    lib = _service(tmp_path, groups, candidates=candidates)
    prompt = await _owned_prompt(lib)
    await _share_groups(lib, prompt["id"], ["eng"])
    candidates.clear()  # "eng" is no longer configured (deployment change)
    groups.fail = {"eng"}  # and the authorizer is down
    sharing = await lib.get_sharing(OWNER, prompt["id"])
    kept = await lib.set_sharing(OWNER, prompt["id"], ["eng"], [{"email": ALICE, "permission": "use"}],
                                 sharing["revision"])
    assert kept["groups"] == ["eng"]  # an existing grant is not re-validated
    assert (await _share_groups(lib, prompt["id"], []))["groups"] == []  # and can always be removed


def test_sharing_route_status_codes(client):
    http, lib = client
    prompt = asyncio.run(lib.create_prompt(OWNER, "Reviewer", "text"))
    as_owner = {"x-test-user": OWNER}
    revision = http.get(f"/api/prompts/{prompt['id']}/sharing", headers=as_owner).json()["sharing"]["revision"]
    response = http.put(f"/api/prompts/{prompt['id']}/sharing", headers=as_owner,
                        json={"groups": ["not-configured"], "users": [], "expected_revision": revision})
    assert response.status_code == 403
