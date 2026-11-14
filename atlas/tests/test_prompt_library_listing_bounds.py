"""Group checks in the ``mine`` and ``shared`` listings (issue #1169, review nit 3).

The ``all``/``team`` listings check membership within a group-count and time
bound. ``mine`` and ``shared`` used the unbounded check for every group on the
page -- including groups on prompts the caller owns, where membership cannot
change anything -- so one slow or hanging authorizer call stalled the
caller's own list.

In these scopes visibility never depends on groups: ``mine`` is ownership,
``shared`` is a direct grant (always at least ``use``, the most a group can
give). Groups only add the "via group" labels. Now:

* owned entries are never group-checked (the owner's level is ``owner``);
* other entries' groups are checked within the listing bounds, and a group
  whose check did not finish is simply not shown as a route (fail closed);
* who sees what is unchanged.
"""

import asyncio
import time

import pytest

from atlas.modules.prompts import prompt_library_service as service_module
from atlas.tests.test_prompt_library_bounds import ALICE, MALLORY, OWNER, Groups, _clean_engine, _service  # noqa: F401

GROUPS = ["eng", "ops", "slow", "flaky"]


async def _published(lib, owner, title, groups=(), users=()):
    prompt = await lib.create_prompt(owner, title, f"{title} text")
    detail = await lib.get_prompt(owner, prompt["id"])
    await lib.publish(owner, prompt["id"], detail["draft_revision"], "first")
    sharing = await lib.get_sharing(owner, prompt["id"])
    await lib.set_sharing(owner, prompt["id"], list(groups), [dict(u) for u in users], sharing["revision"])
    return prompt


async def _setup(tmp_path):
    everyone = {OWNER, ALICE}
    groups = Groups({g: set(everyone) for g in GROUPS})
    lib = _service(tmp_path, groups, candidates=GROUPS)
    mine = await _published(lib, OWNER, "Owner's reviewer", groups=GROUPS)
    shared = await _published(lib, OWNER, "Shared with Alice", groups=GROUPS,
                              users=[{"email": ALICE, "permission": "use"}])
    team_only = await _published(lib, OWNER, "Team only", groups=["eng"])
    alice_own = await _published(lib, ALICE, "Alice's own", groups=["eng", "slow"])
    groups.calls.clear()
    # From here on one group hangs and one authorizer call fails.
    groups.delay = {"slow": 30}
    groups.fail = {"flaky"}
    return lib, groups, {"mine": mine, "shared": shared, "team_only": team_only, "alice_own": alice_own}


@pytest.mark.asyncio
async def test_mine_never_checks_groups_on_owned_prompts(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "LISTING_GROUP_CHECK_TIMEOUT_S", 0.3)
    lib, groups, ids = await _setup(tmp_path)
    started = time.monotonic()
    page = await asyncio.wait_for(lib.list_prompts(OWNER, scope="mine"), 5)
    assert time.monotonic() - started < 1.0
    assert groups.calls == []  # nothing to decide: the owner owns them
    assert {p["id"] for p in page["prompts"]} == {ids["mine"]["id"], ids["shared"]["id"], ids["team_only"]["id"]}
    assert all(p["permission"] == "owner" for p in page["prompts"])
    assert "incomplete" not in page


@pytest.mark.asyncio
async def test_shared_checks_are_bounded_and_unfinished_groups_are_not_shown(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "LISTING_GROUP_CHECK_TIMEOUT_S", 0.3)
    lib, groups, ids = await _setup(tmp_path)
    started = time.monotonic()
    page = await asyncio.wait_for(lib.list_prompts(ALICE, scope="shared"), 5)
    assert time.monotonic() - started < 2.0, "a hanging group check stalled the shared listing"
    assert [p["id"] for p in page["prompts"]] == [ids["shared"]["id"]]
    entry = page["prompts"][0]
    assert entry["permission"] == "use"  # from the direct grant, whatever the groups said
    assert "user" in entry["access_via"] and {"eng", "ops"} <= set(entry["access_via"])
    assert "slow" not in entry["access_via"] and "flaky" not in entry["access_via"]  # unconfirmed: not claimed
    assert "incomplete" not in page  # nothing is missing; only labels were unconfirmed


@pytest.mark.asyncio
async def test_shared_group_checks_are_capped_in_number(tmp_path, monkeypatch):
    lib, groups, _ = await _setup(tmp_path)
    groups.delay, groups.fail = {}, set()
    monkeypatch.setattr(service_module, "MAX_LISTING_GROUP_CHECKS", 2)
    page = await lib.list_prompts(ALICE, scope="shared")
    assert len(page["prompts"]) == 1
    assert len(groups.calls) <= 2


@pytest.mark.asyncio
async def test_visibility_in_every_scope_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "LISTING_GROUP_CHECK_TIMEOUT_S", 0.3)
    lib, groups, ids = await _setup(tmp_path)
    groups.delay, groups.fail = {}, set()
    titles = lambda page: sorted(p["title"] for p in page["prompts"])  # noqa: E731
    assert titles(await lib.list_prompts(ALICE, scope="mine")) == ["Alice's own"]
    assert titles(await lib.list_prompts(ALICE, scope="shared")) == ["Shared with Alice"]
    assert titles(await lib.list_prompts(ALICE, scope="team")) == ["Owner's reviewer", "Shared with Alice", "Team only"]
    assert len((await lib.list_prompts(ALICE, scope="all"))["prompts"]) == 4
    # An outsider sees nothing in any scope, group or not.
    for scope in ("mine", "shared", "team", "all"):
        assert (await lib.list_prompts(MALLORY, scope=scope))["prompts"] == []


@pytest.mark.asyncio
async def test_a_revoked_direct_grant_leaves_the_shared_listing(tmp_path):
    lib, groups, ids = await _setup(tmp_path)
    groups.delay, groups.fail = {}, set()
    sharing = await lib.get_sharing(OWNER, ids["shared"]["id"])
    await lib.set_sharing(OWNER, ids["shared"]["id"], sharing["groups"], [], sharing["revision"])
    assert (await lib.list_prompts(ALICE, scope="shared"))["prompts"] == []
    # Still visible through the team scope, because Alice is in its groups.
    assert ids["shared"]["id"] in {p["id"] for p in (await lib.list_prompts(ALICE, scope="team"))["prompts"]}


@pytest.mark.asyncio
async def test_an_authorizer_outage_does_not_hide_direct_shares(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "LISTING_GROUP_CHECK_TIMEOUT_S", 0.3)
    lib, groups, ids = await _setup(tmp_path)
    groups.delay, groups.fail = {}, set(GROUPS)
    page = await lib.list_prompts(ALICE, scope="shared")
    assert [p["id"] for p in page["prompts"]] == [ids["shared"]["id"]]
    assert page["prompts"][0]["access_via"] == ["user"]
