"""Authorization tests for the team-shared prompt library (issue #1169).

Runs the real service over a temporary DuckDB with an injected group check, so
each test states group membership explicitly and can change it mid-test the
way an IdP would. Covers privacy defaults, owner/editor/manager rights, group
grants (use only), multi-group semantics, list/search filtering, ID probing,
version-history access, revocation, authorizer failure, forks and audit.
"""

import pytest

from atlas.modules.chat_history.database import reset_engine
from atlas.modules.prompts.prompt_library_service import (
    PromptForbidden,
    PromptLibraryService,
    PromptNotFound,
    PromptUnavailable,
    PromptValidationError,
)

OWNER = "owner@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
MALLORY = "mallory@example.com"


class Groups:
    """Mutable membership table standing in for the authorizer."""

    def __init__(self):
        self.members = {}
        self.fail = False
        self.calls = 0

    def add(self, user, group):
        self.members.setdefault(group, set()).add(user)

    def remove(self, user, group):
        self.members.get(group, set()).discard(user)

    async def check(self, user, group):
        self.calls += 1
        if self.fail:
            raise RuntimeError("authorizer down")
        return user in self.members.get(group, set())


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


@pytest.fixture
def groups():
    g = Groups()
    for user in (OWNER, ALICE, BOB):
        g.add(user, "eng")
    g.add(OWNER, "ops")
    g.add(BOB, "ops")
    return g


@pytest.fixture
def lib(tmp_path, groups):
    from atlas.modules.chat_history import PromptLibraryRepository, get_session_factory, init_database

    init_database(f"duckdb:///{tmp_path / 'library.db'}")
    repo = PromptLibraryRepository(get_session_factory())
    return PromptLibraryService(repo, group_check=groups.check, group_candidates=lambda: ["eng", "ops", "hr"])


async def _published(lib, title="Reviewer", content="v1 text", groups_=None, users=None):
    prompt = await lib.create_prompt(OWNER, title, content, description="desc")
    await lib.publish(OWNER, prompt["id"], "first")
    if groups_ is not None or users is not None:
        await lib.set_sharing(OWNER, prompt["id"], groups_ or [], users or [])
    return prompt


@pytest.mark.asyncio
async def test_new_prompts_are_private(lib):
    prompt = await _published(lib)
    assert prompt["permission"] == "owner"
    assert await lib.list_prompts(ALICE) == []
    with pytest.raises(PromptNotFound):
        await lib.get_prompt(ALICE, prompt["id"])
    with pytest.raises(PromptUnavailable):
        await lib.resolve_for_execution(ALICE, prompt["id"])


@pytest.mark.asyncio
async def test_owner_has_full_control(lib):
    prompt = await _published(lib)
    updated = await lib.update_prompt(OWNER, prompt["id"], content="v2 text")
    assert updated["draft_content"] == "v2 text"
    assert updated["has_unpublished_changes"] is True
    v2 = await lib.publish(OWNER, prompt["id"], "second")
    assert v2["version_number"] == 2
    assert (await lib.get_sharing(OWNER, prompt["id"])) == {"groups": [], "users": []}
    await lib.archive(OWNER, prompt["id"])
    assert await lib.list_prompts(OWNER) == []


@pytest.mark.asyncio
async def test_group_membership_grants_use_not_edit_or_share(lib):
    prompt = await _published(lib, groups_=["eng"])
    listed = await lib.list_prompts(ALICE)
    assert [p["id"] for p in listed] == [prompt["id"]]
    assert listed[0]["permission"] == "use"
    assert listed[0]["access_via"] == ["eng"]
    assert "sharing" not in listed[0] and "draft_content" not in listed[0]
    detail = await lib.get_prompt(ALICE, prompt["id"])
    assert detail["latest_content"] == "v1 text"
    assert "draft_content" not in detail
    assert len(await lib.list_versions(ALICE, prompt["id"])) == 1
    version = await lib.resolve_for_execution(ALICE, prompt["id"])
    assert version["content"] == "v1 text"

    with pytest.raises(PromptForbidden):
        await lib.update_prompt(ALICE, prompt["id"], content="hijack")
    with pytest.raises(PromptForbidden):
        await lib.publish(ALICE, prompt["id"])
    with pytest.raises(PromptForbidden):
        await lib.set_sharing(ALICE, prompt["id"], ["eng", "ops"], [])
    with pytest.raises(PromptForbidden):
        await lib.list_audit_events(ALICE, prompt["id"])
    with pytest.raises(PromptForbidden):
        await lib.archive(ALICE, prompt["id"])


@pytest.mark.asyncio
async def test_group_grants_cannot_carry_edit(lib):
    prompt = await _published(lib)
    # The API only accepts group names; the service always stores them as use.
    await lib.set_sharing(OWNER, prompt["id"], ["eng"], [])
    grants = lib.repository.list_grants(prompt["id"])
    assert [(g["grantee"], g["permission"]) for g in grants] == [("eng", "use")]


@pytest.mark.asyncio
async def test_explicit_editor_can_edit_and_publish_but_not_share(lib):
    prompt = await _published(lib, users=[{"email": ALICE, "permission": "edit"}])
    await lib.update_prompt(ALICE, prompt["id"], content="alice edit")
    version = await lib.publish(ALICE, prompt["id"], "alice change")
    assert version["author_email"] == ALICE and version["content"] == "alice edit"
    with pytest.raises(PromptForbidden):
        await lib.set_sharing(ALICE, prompt["id"], [], [{"email": ALICE, "permission": "edit"}])
    with pytest.raises(PromptForbidden):
        await lib.archive(ALICE, prompt["id"])


@pytest.mark.asyncio
async def test_manager_can_share_but_only_owner_controls_manage(lib):
    prompt = await _published(lib, users=[{"email": ALICE, "permission": "manage"}])
    sharing = await lib.set_sharing(
        ALICE, prompt["id"], ["eng"],
        [{"email": ALICE, "permission": "manage"}, {"email": BOB, "permission": "edit"}],
    )
    assert sharing["groups"] == ["eng"]
    with pytest.raises(PromptForbidden):
        await lib.set_sharing(
            ALICE, prompt["id"], ["eng"],
            [{"email": ALICE, "permission": "manage"}, {"email": BOB, "permission": "manage"}],
        )
    with pytest.raises(PromptForbidden):
        await lib.set_sharing(ALICE, prompt["id"], ["eng"], [{"email": BOB, "permission": "edit"}])


@pytest.mark.asyncio
async def test_sharing_requires_membership_of_the_target_group(lib):
    prompt = await _published(lib)
    with pytest.raises(PromptForbidden):
        await lib.set_sharing(OWNER, prompt["id"], ["hr"], [])
    with pytest.raises(PromptValidationError):
        await lib.set_sharing(OWNER, prompt["id"], ["bad group"], [])
    with pytest.raises(PromptValidationError):
        await lib.set_sharing(OWNER, prompt["id"], [], [{"email": OWNER, "permission": "use"}])
    assert await lib.shareable_groups(OWNER) == ["eng", "ops"]
    assert await lib.shareable_groups(MALLORY) == []


@pytest.mark.asyncio
async def test_multiple_groups_are_a_union(lib, groups):
    prompt = await _published(lib, groups_=["eng", "ops"])
    listed = await lib.list_prompts(BOB)
    assert listed[0]["access_via"] == ["eng", "ops"]
    # Losing one group keeps access through the other...
    groups.remove(BOB, "eng")
    assert (await lib.get_prompt(BOB, prompt["id"]))["access_via"] == ["ops"]
    # ...losing both removes it.
    groups.remove(BOB, "ops")
    with pytest.raises(PromptNotFound):
        await lib.get_prompt(BOB, prompt["id"])


@pytest.mark.asyncio
async def test_list_scopes_and_search_only_return_authorized_entries(lib):
    shared = await _published(lib, title="Shared reviewer", groups_=["eng"])
    await _published(lib, title="Private reviewer")
    direct = await _published(lib, title="Direct note", users=[{"email": ALICE, "permission": "use"}])
    own = await lib.create_prompt(ALICE, "Alice reviewer", "mine")

    ids = lambda items: sorted(p["id"] for p in items)  # noqa: E731
    assert ids(await lib.list_prompts(ALICE)) == sorted([shared["id"], direct["id"], own["id"]])
    assert ids(await lib.list_prompts(ALICE, scope="mine")) == [own["id"]]
    assert ids(await lib.list_prompts(ALICE, scope="shared")) == [direct["id"]]
    assert ids(await lib.list_prompts(ALICE, scope="team")) == [shared["id"]]
    assert ids(await lib.list_prompts(ALICE, query="reviewer")) == sorted([shared["id"], own["id"]])
    # A search term matching only the private entry finds nothing.
    assert await lib.list_prompts(ALICE, query="Private") == []
    with pytest.raises(PromptValidationError):
        await lib.list_prompts(ALICE, scope="everyone")


@pytest.mark.asyncio
async def test_guessed_ids_are_indistinguishable_from_missing(lib):
    prompt = await _published(lib)
    version = (await lib.list_versions(OWNER, prompt["id"]))[0]
    for call in (
        lambda: lib.get_prompt(MALLORY, prompt["id"]),
        lambda: lib.list_versions(MALLORY, prompt["id"]),
        lambda: lib.get_version(MALLORY, prompt["id"], version["id"]),
        lambda: lib.get_sharing(MALLORY, prompt["id"]),
        lambda: lib.export_prompt(MALLORY, prompt["id"]),
        lambda: lib.fork(MALLORY, prompt["id"]),
        lambda: lib.update_prompt(MALLORY, prompt["id"], content="x"),
        lambda: lib.publish(MALLORY, prompt["id"]),
        lambda: lib.set_sharing(MALLORY, prompt["id"], [], []),
        lambda: lib.list_audit_events(MALLORY, prompt["id"]),
        lambda: lib.get_prompt(MALLORY, "does-not-exist"),
    ):
        with pytest.raises(PromptNotFound) as exc:
            await call()
        assert exc.value.message == "Prompt not found"


@pytest.mark.asyncio
async def test_version_of_another_prompt_is_not_reachable(lib):
    mine = await _published(lib, groups_=["eng"])
    secret = await _published(lib, content="secret text")
    secret_version = (await lib.list_versions(OWNER, secret["id"]))[0]
    with pytest.raises(PromptNotFound):
        await lib.get_version(ALICE, mine["id"], secret_version["id"])
    with pytest.raises(PromptUnavailable):
        await lib.resolve_for_execution(ALICE, mine["id"], secret_version["id"])


@pytest.mark.asyncio
async def test_use_only_users_do_not_see_unpublished_entries(lib):
    prompt = await lib.create_prompt(OWNER, "Draft only", "draft")
    await lib.set_sharing(OWNER, prompt["id"], ["eng"], [])
    assert await lib.list_prompts(ALICE) == []
    with pytest.raises(PromptNotFound):
        await lib.get_prompt(ALICE, prompt["id"])


@pytest.mark.asyncio
async def test_revocation_applies_to_the_next_request(lib, groups):
    prompt = await _published(lib, groups_=["eng"])
    assert await lib.resolve_for_execution(ALICE, prompt["id"])
    groups.remove(ALICE, "eng")
    with pytest.raises(PromptUnavailable):
        await lib.resolve_for_execution(ALICE, prompt["id"])
    groups.add(ALICE, "eng")
    assert await lib.resolve_for_execution(ALICE, prompt["id"])
    # Unsharing the group revokes too.
    await lib.set_sharing(OWNER, prompt["id"], [], [])
    with pytest.raises(PromptUnavailable):
        await lib.resolve_for_execution(ALICE, prompt["id"])


@pytest.mark.asyncio
async def test_authorizer_failure_fails_closed(lib, groups):
    prompt = await _published(lib, groups_=["eng"], users=[{"email": BOB, "permission": "use"}])
    groups.fail = True
    assert await lib.list_prompts(ALICE) == []
    with pytest.raises(PromptNotFound):
        await lib.get_prompt(ALICE, prompt["id"])
    with pytest.raises(PromptUnavailable):
        await lib.resolve_for_execution(ALICE, prompt["id"])
    # A direct user grant does not depend on the authorizer.
    assert (await lib.get_prompt(BOB, prompt["id"]))["permission"] == "use"
    # Sharing into a group cannot be verified either, so it is refused.
    with pytest.raises(PromptForbidden):
        await lib.set_sharing(OWNER, prompt["id"], ["eng", "ops"], [])


@pytest.mark.asyncio
async def test_external_authorizer_errors_fail_closed(lib, groups, monkeypatch):
    """The real is_user_in_group denies when its HTTP endpoint fails.

    Shared with the configured admin group, which the debug mock table grants
    to TEST_USER when no authorizer is configured -- so the same request is
    granted before the outage and refused during it.
    """
    import httpx

    from atlas.core import auth
    from atlas.modules.config.config_manager import config_manager

    groups.add(OWNER, "admin")
    prompt = await _published(lib, groups_=["admin"])
    real = PromptLibraryService(lib.repository)  # default: atlas.core.auth.is_user_in_group
    settings = config_manager.app_settings
    monkeypatch.setattr(settings, "skip_authorization_checks", False)
    monkeypatch.setattr(settings, "debug_mode", True)
    monkeypatch.setattr(settings, "test_user", ALICE)
    monkeypatch.setattr(settings, "admin_group", "admin")
    monkeypatch.setattr(settings, "auth_group_check_url", "")
    assert (await real.resolve_for_execution(ALICE, prompt["id"]))["content"] == "v1 text"

    attempts = []

    class Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            attempts.append(k.get("json"))
            raise httpx.ConnectError("down")

    monkeypatch.setattr(settings, "auth_group_check_url", "https://authz.invalid/check")
    monkeypatch.setattr(settings, "auth_group_check_api_key", "k")
    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **k: Boom())
    with pytest.raises(PromptUnavailable):
        await real.resolve_for_execution(ALICE, prompt["id"])
    assert await real.list_prompts(ALICE) == []
    assert {"user_id": ALICE, "group_id": "admin"} in attempts


@pytest.mark.asyncio
async def test_fork_is_private_and_isolated(lib):
    prompt = await _published(lib, groups_=["eng"])
    fork = await lib.fork(ALICE, prompt["id"], title="Alice's copy")
    assert fork["is_owner"] and fork["permission"] == "owner"
    assert fork["draft_content"] == "v1 text"
    assert fork["forked_from_prompt_id"] == prompt["id"]
    assert fork["latest_version"] is None
    assert (await lib.get_sharing(ALICE, fork["id"])) == {"groups": [], "users": []}
    # Owner of the source cannot see the fork; edits do not cross over.
    with pytest.raises(PromptNotFound):
        await lib.get_prompt(OWNER, fork["id"])
    await lib.update_prompt(ALICE, fork["id"], content="alice only")
    assert (await lib.get_prompt(OWNER, prompt["id"]))["draft_content"] == "v1 text"


@pytest.mark.asyncio
async def test_published_versions_are_immutable(lib):
    prompt = await _published(lib)
    v1 = (await lib.list_versions(OWNER, prompt["id"]))[0]
    await lib.update_prompt(OWNER, prompt["id"], title="Renamed", content="v2 text")
    await lib.publish(OWNER, prompt["id"], "second")
    again = await lib.get_version(OWNER, prompt["id"], v1["id"])
    assert again["content"] == "v1 text" and again["title"] == "Reviewer"
    assert again["version_number"] == 1
    latest = await lib.resolve_for_execution(OWNER, prompt["id"])
    assert latest["content"] == "v2 text" and latest["version_number"] == 2
    # Archiving keeps the versions for history but stops new use.
    await lib.archive(OWNER, prompt["id"])
    assert lib.repository.get_version(prompt["id"], v1["id"])["content"] == "v1 text"
    with pytest.raises(PromptUnavailable):
        await lib.resolve_for_execution(OWNER, prompt["id"], v1["id"])


@pytest.mark.asyncio
async def test_audit_records_events_without_prompt_bodies(lib):
    prompt = await _published(lib, content="TOP SECRET BODY", groups_=["eng"])
    await lib.update_prompt(OWNER, prompt["id"], content="ANOTHER SECRET")
    await lib.fork(ALICE, prompt["id"])
    events = await lib.list_audit_events(OWNER, prompt["id"])
    kinds = [e["event_type"] for e in reversed(events)]
    assert kinds == ["create", "publish", "share", "update_draft", "fork"]
    by_kind = {e["event_type"]: e for e in events}
    assert by_kind["publish"]["details"] == {"version_number": 1}
    assert by_kind["publish"]["version_id"]
    assert by_kind["share"]["details"]["groups_added"] == ["eng"]
    assert by_kind["update_draft"]["details"] == {"fields": ["draft_content"]}
    assert by_kind["fork"]["actor_email"] == ALICE
    blob = repr(events)
    assert "SECRET" not in blob


@pytest.mark.asyncio
async def test_identities_are_case_insensitive(lib):
    prompt = await _published(lib, users=[{"email": "Alice@Example.com", "permission": "edit"}])
    assert (await lib.get_prompt("ALICE@example.com", prompt["id"]))["permission"] == "edit"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["users", " Users ", "USERS"])
async def test_org_wide_group_cannot_be_shared_with(lib, groups, name):
    """The MVP has no organization-wide catalog: 'users' (everyone) is refused server-side."""
    groups.add(OWNER, "users")
    prompt = await _published(lib)
    with pytest.raises(PromptValidationError):
        await lib.set_sharing(OWNER, prompt["id"], ["eng", name], [])
    assert lib.repository.list_grants(prompt["id"]) == []


@pytest.mark.asyncio
async def test_org_wide_group_is_never_offered_or_honoured(lib, groups):
    groups.add(OWNER, "users")
    groups.add(MALLORY, "users")
    lib._group_candidates = lambda: ["eng", "users", "Users"]
    assert await lib.shareable_groups(OWNER) == ["eng"]
    # A 'users' grant written behind the service's back still grants nothing.
    prompt = await _published(lib)
    lib.repository.replace_grants(
        prompt["id"], [{"grantee_type": "group", "grantee": "users", "permission": "use"}], OWNER
    )
    assert await lib.list_prompts(MALLORY) == []
    with pytest.raises(PromptNotFound):
        await lib.get_prompt(MALLORY, prompt["id"])
    with pytest.raises(PromptUnavailable):
        await lib.resolve_for_execution(MALLORY, prompt["id"])
