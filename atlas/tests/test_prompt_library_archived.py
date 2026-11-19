"""Archived team-library prompts (issue #1169, fifth review).

Archiving is a soft delete: the entry leaves the library and can no longer be
run, edited, shared or forked, but its published versions are history -- a
pinned conversation's version "always resolves to its original text" (design
note). So the read-only history operations (version list, one version's text,
export) stay available to exactly the callers the normal rules admit: the
owner and current grantees. Revocation still applies, nothing else is opened,
and every refusal is the same 404 as a missing id.
"""

import pytest

from atlas.modules.chat_history.database import reset_engine
from atlas.modules.prompts.prompt_library_service import (
    PromptForbidden,
    PromptLibraryService,
    PromptNotFound,
    PromptUnavailable,
)

OWNER = "owner@example.com"
ALICE = "alice@example.com"  # in group eng
EDITOR = "editor@example.com"  # direct edit grant
MALLORY = "mallory@example.com"


class Groups:
    def __init__(self):
        self.members = {"eng": {OWNER, ALICE}}

    async def check(self, user, group):
        return user in self.members.get(group, set())


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def setup(tmp_path):
    from atlas.modules.chat_history import PromptLibraryRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'archived.db'}")
    groups = Groups()
    lib = PromptLibraryService(
        PromptLibraryRepository(get_session_factory()), group_check=groups.check, group_candidates=lambda: ["eng"]
    )
    return lib, groups


async def _archived_prompt(lib):
    prompt = await lib.create_prompt(OWNER, "Reviewer", "v1 text")
    detail = await lib.get_prompt(OWNER, prompt["id"])
    v1 = await lib.publish(OWNER, prompt["id"], detail["draft_revision"], "first")
    detail = await lib.update_prompt(OWNER, prompt["id"], detail["draft_revision"], content="v2 text")
    v2 = await lib.publish(OWNER, prompt["id"], detail["draft_revision"], "second")
    sharing = await lib.get_sharing(OWNER, prompt["id"])
    await lib.set_sharing(OWNER, prompt["id"], ["eng"], [{"email": EDITOR, "permission": "edit"}], sharing["revision"])
    await lib.archive(OWNER, prompt["id"])
    return prompt, v1, v2


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [OWNER, ALICE, EDITOR])
async def test_authorized_callers_keep_read_only_history(setup, user):
    lib, _ = setup
    prompt, v1, v2 = await _archived_prompt(lib)
    page = await lib.list_versions(user, prompt["id"])
    assert [v["version_number"] for v in page["versions"]] == [2, 1] and page["total"] == 2
    assert (await lib.get_version(user, prompt["id"], v1["id"]))["content"] == "v1 text"
    exported = await lib.export_prompt(user, prompt["id"])
    assert exported["archived"] is True
    assert [v["content"] for v in exported["versions"]] == ["v2 text", "v1 text"]
    # The history is the same immutable text it was before archiving.
    assert (await lib.get_version(user, prompt["id"], v2["id"]))["content"] == "v2 text"


@pytest.mark.asyncio
async def test_export_of_an_archived_prompt_keeps_the_detail_rules(setup):
    lib, _ = setup
    prompt, _, _ = await _archived_prompt(lib)
    owner_view = await lib.export_prompt(OWNER, prompt["id"])
    member_view = await lib.export_prompt(ALICE, prompt["id"])
    assert "draft_content" in owner_view and "sharing" in owner_view
    assert "draft_content" not in member_view and "sharing" not in member_view


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [MALLORY, "nobody@example.com"])
async def test_outsiders_get_the_same_not_found(setup, user):
    lib, _ = setup
    prompt, v1, _ = await _archived_prompt(lib)
    for call in (
        lambda: lib.list_versions(user, prompt["id"]),
        lambda: lib.get_version(user, prompt["id"], v1["id"]),
        lambda: lib.export_prompt(user, prompt["id"]),
    ):
        with pytest.raises(PromptNotFound):
            await call()


@pytest.mark.asyncio
async def test_revocation_still_applies_to_history(setup):
    lib, groups = setup
    prompt, v1, _ = await _archived_prompt(lib)
    assert (await lib.get_version(ALICE, prompt["id"], v1["id"]))["content"] == "v1 text"
    groups.members["eng"].discard(ALICE)
    with pytest.raises(PromptNotFound):
        await lib.get_version(ALICE, prompt["id"], v1["id"])
    with pytest.raises(PromptNotFound):
        await lib.list_versions(ALICE, prompt["id"])


@pytest.mark.asyncio
async def test_archived_prompts_cannot_be_used_or_changed(setup):
    lib, _ = setup
    prompt, v1, _ = await _archived_prompt(lib)
    for user in (OWNER, ALICE, EDITOR):
        assert prompt["id"] not in [p["id"] for p in (await lib.list_prompts(user))["prompts"]]
        with pytest.raises(PromptNotFound):
            await lib.get_prompt(user, prompt["id"])  # the library detail view
        with pytest.raises(PromptUnavailable):
            await lib.resolve_for_execution(user, prompt["id"], v1["id"])
        with pytest.raises((PromptNotFound, PromptForbidden)):
            await lib.fork(user, prompt["id"], v1["id"])
    with pytest.raises(PromptNotFound):
        await lib.update_prompt(OWNER, prompt["id"], 3, content="new")
    with pytest.raises(PromptNotFound):
        await lib.publish(OWNER, prompt["id"], 3)
    with pytest.raises(PromptNotFound):
        await lib.get_sharing(OWNER, prompt["id"])
    with pytest.raises(PromptNotFound):
        await lib.archive(OWNER, prompt["id"])


@pytest.mark.asyncio
async def test_a_version_of_another_prompt_is_not_reachable_through_an_archived_one(setup):
    lib, _ = setup
    prompt, _, _ = await _archived_prompt(lib)
    other = await lib.create_prompt(OWNER, "Secret", "secret text")
    detail = await lib.get_prompt(OWNER, other["id"])
    secret = await lib.publish(OWNER, other["id"], detail["draft_revision"])
    with pytest.raises(PromptNotFound):
        await lib.get_version(ALICE, prompt["id"], secret["id"])
