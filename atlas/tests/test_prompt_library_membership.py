"""Group-membership load, reuse and failure handling in the prompt library
(issue #1169, maintainer review findings 3 and 6).

Black-box tests build the service the way the app does (``AppFactory``) and
count calls into ``atlas.core.auth``; unit tests cover the resolver's
expiry, single flight, concurrency bound, cross-user isolation and the
Redis-backed shared table (with ``ATLAS_TEST_REDIS_URL``).
"""

import asyncio
import os
import time
import uuid

import pytest

from atlas.modules.chat_history.database import reset_engine

OWNER = "owner@example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
GROUPS = [f"team-{i:02d}" for i in range(20)]


@pytest.fixture(autouse=True)
def _clean_engine():
    reset_engine()
    yield
    reset_engine()


class Authorizer:
    """Stands in for atlas.core.auth: counts calls, answers from a table."""

    def __init__(self, members, delay=0.0):
        self.members = members
        self.delay = delay
        self.calls = []
        self.down = False

    async def check(self, user, group):
        self.calls.append((user, group))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.down:
            return None
        return user in self.members.get(group, set())

    async def legacy(self, user, group):  # is_user_in_group: down -> False
        return bool(await self.check(user, group))


@pytest.fixture
def app_service(tmp_path, monkeypatch):
    """The library service exactly as AppFactory builds it, with auth patched."""
    from atlas.core import auth

    authorizer = Authorizer({g: {OWNER, ALICE} for g in GROUPS})
    monkeypatch.setattr(auth, "is_user_in_group", authorizer.legacy)
    monkeypatch.setattr(auth, "check_user_in_group", authorizer.check, raising=False)
    for name, value in {
        "FEATURE_CHAT_HISTORY_ENABLED": "true",
        "FEATURE_CUSTOM_PROMPTS_ENABLED": "true",
        "FEATURE_PROMPT_LIBRARY_ENABLED": "true",
        "CHAT_HISTORY_DB_URL": f"duckdb:///{tmp_path / 'membership.db'}",
        "SESSION_REPOSITORY_TYPE": "memory",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PROMPT_LIBRARY_MEMBERSHIP_CACHE_SECONDS", raising=False)
    from atlas.infrastructure.app_factory import AppFactory

    factory = AppFactory()
    service = factory.prompt_library_service
    assert service is not None
    service._group_candidates = lambda: list(GROUPS)
    return service, authorizer


async def _shared(lib, title, groups):
    prompt = await lib.create_prompt(OWNER, title, f"{title} text")
    detail = await lib.get_prompt(OWNER, prompt["id"])
    await lib.publish(OWNER, prompt["id"], detail["draft_revision"], "first")
    sharing = await lib.get_sharing(OWNER, prompt["id"])
    await lib.set_sharing(OWNER, prompt["id"], groups, [], sharing["revision"])
    return prompt


# --- Finding 3: request fanout (black box, AppFactory-built service) -----------


@pytest.mark.asyncio
async def test_repeated_listings_reuse_membership_answers(app_service):
    lib, authorizer = app_service
    for i in range(4):
        await _shared(lib, f"Prompt {i}", GROUPS[i * 5:(i + 1) * 5])
    authorizer.calls.clear()

    first = await lib.list_prompts(ALICE, scope="all")
    after_first = len(authorizer.calls)
    assert len(first["prompts"]) == 4 and "incomplete" not in first
    assert after_first <= len(GROUPS)  # at most one question per granted group

    for _ in range(5):  # five more page views by the same user
        again = await lib.list_prompts(ALICE, scope="all")
        assert [p["id"] for p in again["prompts"]] == [p["id"] for p in first["prompts"]]
    assert len(authorizer.calls) == after_first, "every listing asked the authorizer again"


@pytest.mark.asyncio
async def test_a_slow_authorizer_only_costs_the_first_listing(app_service):
    lib, authorizer = app_service
    await _shared(lib, "Shared", GROUPS[:16])
    authorizer.delay = 0.1
    started = time.monotonic()
    await lib.list_prompts(ALICE, scope="team")
    first = time.monotonic() - started
    started = time.monotonic()
    await lib.list_prompts(ALICE, scope="team")
    second = time.monotonic() - started
    assert first >= 0.1 and second < first / 2


@pytest.mark.asyncio
async def test_answers_are_never_shared_between_users(app_service):
    lib, authorizer = app_service
    await _shared(lib, "Team only", ["team-00"])
    assert len((await lib.list_prompts(ALICE, scope="team"))["prompts"]) == 1
    # Bob is not in team-00: Alice's cached "member" must not answer for him.
    assert (await lib.list_prompts(BOB, scope="team"))["prompts"] == []
    assert (BOB, "team-00") in authorizer.calls


# --- Finding 6: an unavailable authorizer is unknown, not "not a member" -------


@pytest.mark.asyncio
async def test_an_authorizer_outage_marks_the_listing_incomplete(app_service):
    lib, authorizer = app_service
    await _shared(lib, "Team only", ["team-00"])
    authorizer.down = True
    page = await lib.list_prompts(ALICE, scope="team")
    # Not "you are in no groups": the answer is unknown, and the page says so.
    assert page["prompts"] == [] and page.get("incomplete") is True


@pytest.mark.asyncio
async def test_failures_are_not_cached(app_service):
    lib, authorizer = app_service
    await _shared(lib, "Team only", ["team-00"])
    authorizer.down = True
    assert (await lib.list_prompts(ALICE, scope="team")).get("incomplete") is True
    authorizer.down = False
    page = await lib.list_prompts(ALICE, scope="team")  # asks again at once
    assert len(page["prompts"]) == 1 and "incomplete" not in page


@pytest.mark.asyncio
async def test_sharing_into_a_group_during_an_outage_is_503_not_403(app_service):
    from atlas.modules.prompts.prompt_library_policy import PromptLibraryUnavailable

    lib, authorizer = app_service
    prompt = await _shared(lib, "Mine", [])
    authorizer.down = True
    sharing = await lib.get_sharing(OWNER, prompt["id"])
    with pytest.raises(PromptLibraryUnavailable):
        await lib.set_sharing(OWNER, prompt["id"], ["team-01"], [], sharing["revision"])


@pytest.mark.asyncio
async def test_real_check_user_in_group_reports_http_failures_as_unknown(monkeypatch):
    import httpx

    from atlas.core import auth
    from atlas.modules.config.config_manager import config_manager

    settings = config_manager.app_settings
    monkeypatch.setattr(settings, "auth_group_check_url", "https://authz.invalid/check")
    monkeypatch.setattr(settings, "auth_group_check_api_key", "k")
    monkeypatch.setattr(settings, "skip_authorization_checks", False)
    replies = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kwargs):
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return httpx.Response(reply[0], json=reply[1], request=httpx.Request("POST", url))

    monkeypatch.setattr(auth.httpx, "AsyncClient", lambda *a, **k: Client())
    cases = [
        (httpx.ConnectError("down"), None, False),
        ((500, {"error": "boom"}), None, False),
        ((200, {"unexpected": True}), None, False),
        ((200, {"is_member": False}), False, False),
        ((200, {"is_member": True}), True, True),
        ((200, {"is_member": 1}), True, True),  # legacy truthiness kept
    ]
    for reply, tri_state, legacy in cases:
        replies.append(reply)
        assert await auth.check_user_in_group(ALICE, "eng") is tri_state, reply
        replies.append(reply)
        assert bool(await auth.is_user_in_group(ALICE, "eng")) is legacy, reply  # unchanged for every other caller


@pytest.mark.asyncio
async def test_a_customised_is_user_in_group_is_honoured_by_the_library(tmp_path, monkeypatch):
    """docs/admin/authentication.md tells operators to customise
    is_user_in_group; the library must keep using their rules."""
    from atlas.core import auth
    from atlas.modules.chat_history import PromptLibraryRepository, get_session_factory, init_database
    from atlas.modules.prompts.prompt_library_service import PromptLibraryService

    async def site_rules(user, group):  # a deployment's own group logic
        return group == "team-00" and user.endswith("@example.com")

    monkeypatch.setattr(auth, "is_user_in_group", site_rules)
    assert await auth.check_user_in_group(ALICE, "team-00") is True
    assert await auth.check_user_in_group(ALICE, "team-01") is False
    init_database(f"duckdb:///{tmp_path / 'custom.db'}")
    lib = PromptLibraryService(PromptLibraryRepository(get_session_factory()), group_candidates=lambda: GROUPS)
    await _shared(lib, "Team only", ["team-00"])
    assert len((await lib.list_prompts(ALICE, scope="team"))["prompts"]) == 1


# --- The resolver: expiry, revocation, single flight, bounds, Redis -----------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _resolver(check, ttl=30, store=None, max_concurrent=32):
    from atlas.modules.prompts.prompt_library_membership import LocalMembershipCache, MembershipResolver

    store = store if store is not None else LocalMembershipCache()
    return MembershipResolver(check=lambda: check, ttl_seconds=lambda: ttl, store=lambda: store,
                              max_concurrent=max_concurrent), store


@pytest.mark.asyncio
async def test_answers_expire_so_revocation_takes_at_most_the_ttl():
    from atlas.modules.prompts.prompt_library_membership import LocalMembershipCache

    authorizer = Authorizer({"eng": {ALICE}})
    clock = Clock()
    resolver, _ = _resolver(authorizer.check, ttl=30, store=LocalMembershipCache(clock=clock))
    assert await resolver.membership(ALICE, "eng") is True
    authorizer.members["eng"].discard(ALICE)  # removed in the authorization service
    clock.now += 29
    assert await resolver.membership(ALICE, "eng") is True  # within the documented latency
    clock.now += 2
    assert await resolver.membership(ALICE, "eng") is False  # never past the deadline
    assert len(authorizer.calls) == 2


@pytest.mark.asyncio
async def test_ttl_zero_disables_reuse():
    authorizer = Authorizer({"eng": {ALICE}})
    resolver, _ = _resolver(authorizer.check, ttl=0)
    for _ in range(3):
        await resolver.membership(ALICE, "eng")
    assert len(authorizer.calls) == 3


@pytest.mark.asyncio
async def test_concurrent_identical_checks_share_one_call():
    authorizer = Authorizer({"eng": {ALICE}}, delay=0.05)
    resolver, _ = _resolver(authorizer.check)
    answers = await asyncio.gather(*(resolver.membership(ALICE, "eng") for _ in range(50)))
    assert answers == [True] * 50 and len(authorizer.calls) == 1


@pytest.mark.asyncio
async def test_a_caller_that_stops_waiting_does_not_end_the_shared_check():
    """Cancelling one caller (a bound ran out, or it only needed one group)
    must not give the other callers sharing that check "unknown": the check
    finishes, they get the real answer, and it is cached (November 24 review,
    independent-review follow-up)."""
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def check(user, group):
        calls.append(group)
        started.set()
        await release.wait()
        return True

    resolver, store = _resolver(check)
    first = asyncio.create_task(resolver.membership(ALICE, "eng"))
    await started.wait()
    waiter = asyncio.create_task(resolver.membership(ALICE, "eng"))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.wait([first])
    with pytest.raises(asyncio.CancelledError):
        first.result()  # the cancelled caller still sees its cancellation
    release.set()
    assert await asyncio.wait_for(waiter, 1) is True
    assert len(calls) == 1 and len(store._entries) == 1
    assert await resolver.membership(ALICE, "eng") is True and len(calls) == 1  # reused


@pytest.mark.asyncio
async def test_a_check_nobody_waits_for_any_more_still_fills_the_cache():
    """A check every caller gave up on (an authorizer slower than the turn's
    bound) still completes and is reused, so the next turn does not wait the
    same way again."""
    release = asyncio.Event()

    async def check(user, group):
        await release.wait()
        return True

    resolver, store = _resolver(check)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(resolver.membership(ALICE, "eng"), 0.05)
    release.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert await asyncio.wait_for(resolver.membership(ALICE, "eng"), 0.05) is True


@pytest.mark.asyncio
async def test_concurrent_checks_are_bounded_per_process():
    running = peak = 0

    async def check(user, group):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return True

    resolver, _ = _resolver(check, max_concurrent=4)
    await asyncio.gather(*(resolver.membership(f"u{i}@x.com", "eng") for i in range(40)))
    assert peak <= 4


@pytest.mark.asyncio
async def test_a_failing_cache_degrades_to_asking():
    class Broken:
        async def get(self, key):
            raise ConnectionError("cache down")

        async def put(self, key, value, ttl):
            raise ConnectionError("cache down")

    authorizer = Authorizer({"eng": {ALICE}})
    resolver, _ = _resolver(authorizer.check, store=Broken())
    assert await resolver.membership(ALICE, "eng") is True
    assert await resolver.membership(BOB, "eng") is False


@pytest.mark.asyncio
async def test_the_local_table_is_bounded():
    from atlas.modules.prompts.prompt_library_membership import LocalMembershipCache

    store = LocalMembershipCache(max_entries=10)
    authorizer = Authorizer({})
    resolver, _ = _resolver(authorizer.check, store=store)
    for i in range(50):
        await resolver.membership(f"u{i}@x.com", "eng")
    assert len(store) == 10


@pytest.mark.skipif(not os.environ.get("ATLAS_TEST_REDIS_URL"), reason="ATLAS_TEST_REDIS_URL not set")
@pytest.mark.asyncio
async def test_replicas_share_answers_through_redis_and_expire_them():
    import redis.asyncio as redis_asyncio

    from atlas.infrastructure.redis import RedisClientManager, RedisSettings
    from atlas.modules.prompts.prompt_library_membership import RedisMembershipCache

    url = os.environ["ATLAS_TEST_REDIS_URL"]
    prefix = f"atlas-test-{uuid.uuid4().hex[:8]}"
    managers = [
        RedisClientManager(RedisSettings(url=url, key_prefix=prefix), client=redis_asyncio.from_url(url))
        for _ in range(2)
    ]
    try:
        authorizer = Authorizer({"eng": {ALICE}})
        replica_a, _ = _resolver(authorizer.check, ttl=1, store=RedisMembershipCache(managers[0]))
        replica_b, _ = _resolver(authorizer.check, ttl=1, store=RedisMembershipCache(managers[1]))
        assert await replica_a.membership(ALICE, "eng") is True
        assert await replica_b.membership(ALICE, "eng") is True
        assert len(authorizer.calls) == 1  # the second replica reused the answer
        keys = [k async for k in managers[0].client.scan_iter(f"{prefix}:*")]
        assert keys and all(ALICE.encode() not in k for k in keys)  # no identity in clear
        assert 0 < await managers[0].client.ttl(keys[0]) <= 1  # always expires
        await asyncio.sleep(1.2)
        authorizer.members["eng"].discard(ALICE)
        assert await replica_b.membership(ALICE, "eng") is False
        assert len(authorizer.calls) == 2
    finally:
        for manager in managers:
            async for key in manager.client.scan_iter(f"{prefix}:*"):
                await manager.client.delete(key)
            await manager.client.aclose()


@pytest.mark.asyncio
async def test_a_check_that_never_returns_is_bounded_and_retried(monkeypatch):
    """Second independent-review follow-up: the shared check has a hard bound
    of its own, so one that never returns cannot hold its concurrency slot or
    answer "unknown" to every later caller for good."""
    from atlas.modules.prompts import prompt_library_membership as membership

    monkeypatch.setattr(membership, "CHECK_TIMEOUT_S", 0.1)
    calls, hang = [], {"eng"}

    async def check(user, group):
        calls.append(group)
        if group in hang:
            await asyncio.Event().wait()  # never returns
        return True

    resolver, store = _resolver(check, max_concurrent=1)
    assert await asyncio.wait_for(resolver.membership(ALICE, "eng"), 1) is None  # unknown, not cached
    assert len(store._entries) == 0
    # The slot was freed (max_concurrent=1): another group is answered.
    assert await asyncio.wait_for(resolver.membership(ALICE, "ops"), 1) is True
    # And the stuck group is asked again, not served the old stuck task.
    hang.clear()
    assert await asyncio.wait_for(resolver.membership(ALICE, "eng"), 1) is True
    assert calls == ["eng", "ops", "eng"]


@pytest.mark.asyncio
async def test_execution_rechecks_revoked_cached_membership(app_service):
    from atlas.modules.prompts.prompt_library_policy import (
        PromptMembershipUnknown,
        PromptUnavailable,
    )

    lib, authorizer = app_service
    prompt = await _shared(lib, "Team only", ["team-00"])

    page = await lib.list_prompts(ALICE, scope="team")
    assert len(page["prompts"]) == 1

    await lib.resolve_for_execution(ALICE, prompt["id"])

    authorizer.members["team-00"].remove(ALICE)
    authorizer.calls.clear()

    with pytest.raises(PromptUnavailable) as exc:
        await lib.resolve_for_execution(ALICE, prompt["id"])

    assert not isinstance(exc.value, PromptMembershipUnknown)
    assert (ALICE, "team-00") in authorizer.calls


@pytest.mark.asyncio
async def test_execution_fails_closed_despite_cached_membership(app_service):
    from atlas.modules.prompts.prompt_library_policy import PromptMembershipUnknown

    lib, authorizer = app_service
    prompt = await _shared(lib, "Team only", ["team-00"])

    page = await lib.list_prompts(ALICE, scope="team")
    assert len(page["prompts"]) == 1

    authorizer.down = True

    with pytest.raises(PromptMembershipUnknown):
        await lib.resolve_for_execution(ALICE, prompt["id"])
