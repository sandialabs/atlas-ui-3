"""``admit_run``: one admission path, and no slot leaked by a failed admission (#1128)."""

import asyncio
from types import SimpleNamespace

import fakeredis
import pytest

from atlas.application.chat.runs import admission
from atlas.application.chat.runs.admission import (
    admit_run,
    apply_run_limit,
    renew_live_claims,
)
from atlas.application.chat.runs.coordination import (
    InProcessRunCoordinator,
    RunCoordinationUnavailableError,
    drain_pending_releases,
    get_run_coordinator,
    set_run_coordinator,
)
from atlas.application.chat.runs.registry import (
    ConcurrencyLimitError,
    ConversationBusyError,
    RunStatus,
    get_run_registry,
    reset_run_registry,
)
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator

USER = "bob@example.com"


def _coordinator(server, replica_id="a"):
    manager = RedisClientManager(
        RedisSettings(url="redis://fake/0", key_prefix="t"),
        client=fakeredis.FakeAsyncRedis(server=server),
    )
    return RedisRunCoordinator(manager, replica_id=replica_id)


@pytest.fixture(autouse=True)
def _isolated():
    reset_run_registry()
    set_run_coordinator(None)
    yield
    reset_run_registry()
    set_run_coordinator(None)


@pytest.fixture(autouse=True)
async def _no_heartbeat_left():
    yield
    await admission.stop_lease_heartbeat()


@pytest.fixture
def server():
    return fakeredis.FakeServer()


@pytest.fixture
def coord(server):
    c = _coordinator(server)
    set_run_coordinator(c)
    return c


def _settings(cap):
    return SimpleNamespace(max_concurrent_runs_per_user=cap)


async def _slots(coord, user=USER):
    return await coord._redis.client.zcard(coord.keys.user(user))


async def _conversation_holder(coord, conversation_id, user=USER):
    return await coord._redis.client.get(coord.keys.conversation(conversation_id))


async def test_admitted_run_holds_a_claim_released_when_it_ends(coord):
    record = await admit_run(conversation_id="c1", user_email=USER, app_settings=_settings(5))
    assert record.claim is not None and record.claim.run_id == record.run_id
    assert await _slots(coord) == 1

    get_run_registry().set_status(record.run_id, RunStatus.COMPLETED)
    await drain_pending_releases()

    assert record.claim is None
    assert await _slots(coord) == 0
    assert await _conversation_holder(coord, "c1") is None
    assert (await coord.read(record.run_id))["status"] == "completed"


async def test_cancelled_run_releases_its_claim(coord):
    record = await admit_run(conversation_id="c1", user_email=USER, app_settings=_settings(5))
    get_run_registry().cancel(record.run_id, USER)
    await drain_pending_releases()
    assert await _slots(coord) == 0
    assert (await coord.read(record.run_id))["status"] == "cancelled"


async def test_precheck_refusal_after_the_claim_releases_it(coord):
    """The caller's own check refuses after Redis granted the claim."""
    calls = []

    def precheck():
        calls.append(1)
        if len(calls) == 2:  # the re-check after the claim
            raise RuntimeError("too deep")

    with pytest.raises(RuntimeError):
        await admit_run(
            conversation_id="c1", user_email=USER, app_settings=_settings(1), precheck=precheck
        )
    await drain_pending_releases()

    assert get_run_registry().active_for_user(USER) == []
    assert await _slots(coord) == 0
    assert await _conversation_holder(coord, "c1") is None
    # The slot really is free: a cap of one admits the next run.
    assert await admit_run(conversation_id="c2", user_email=USER, app_settings=_settings(1))


async def test_local_refusal_after_the_claim_releases_it(coord, monkeypatch):
    """The registry refuses after Redis admitted (a local run raced in)."""
    registry = get_run_registry()

    def refuse(**_kwargs):
        raise ConversationBusyError("busy locally")

    monkeypatch.setattr(registry, "start", refuse)
    with pytest.raises(ConversationBusyError):
        await admit_run(conversation_id="c1", user_email=USER, app_settings=_settings(1))
    await drain_pending_releases()
    assert await _slots(coord) == 0
    assert await _conversation_holder(coord, "c1") is None


async def test_coordinator_refusal_registers_nothing(server, coord):
    other = _coordinator(server, "b")
    await other.claim(run_id="elsewhere", conversation_id="c1", user_email=USER, max_runs_per_user=5)

    with pytest.raises(ConversationBusyError):
        await admit_run(conversation_id="c1", user_email=USER, app_settings=_settings(5))
    assert get_run_registry().active_for_user(USER) == []

    await other.claim(run_id="elsewhere-2", conversation_id="c2", user_email=USER, max_runs_per_user=5)
    with pytest.raises(ConcurrencyLimitError):
        await admit_run(conversation_id="c3", user_email=USER, app_settings=_settings(2))
    assert get_run_registry().active_for_user(USER) == []
    assert await _slots(coord) == 2


async def test_coordinator_outage_fails_closed():
    class _Down(InProcessRunCoordinator):
        async def claim(self, **_kwargs):
            raise RunCoordinationUnavailableError("ConnectionError")

    set_run_coordinator(_Down())
    with pytest.raises(RunCoordinationUnavailableError):
        await admit_run(conversation_id="c1", user_email=USER, app_settings=_settings(5))
    assert get_run_registry().active_for_user(USER) == []


async def test_caller_cancelled_mid_claim_releases_the_late_claim(server):
    """Cancelled after Redis granted the claim but before the reply was read."""
    real = _coordinator(server)
    gate = asyncio.Event()

    class _Slow:
        replica_id = real.replica_id

        async def claim(self, **kwargs):
            await gate.wait()
            return await real.claim(**kwargs)

        async def release(self, claim, status):
            return await real.release(claim, status)

    set_run_coordinator(_Slow())
    task = asyncio.create_task(
        admit_run(conversation_id="c1", user_email=USER, app_settings=_settings(5))
    )
    await asyncio.sleep(0.01)
    task.cancel()
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(20):  # let the shielded claim land and its release run
        await asyncio.sleep(0.01)
        await drain_pending_releases()
    assert await _slots(real) == 0
    assert await _conversation_holder(real, "c1") is None
    assert get_run_registry().active_for_user(USER) == []


async def test_cap_comes_from_settings_on_every_admission():
    """The REST-only replica bug from PR #1146: the configured cap, not the default 5."""
    for i in range(2):
        await admit_run(conversation_id=f"c{i}", user_email=USER, app_settings=_settings(2))
    with pytest.raises(ConcurrencyLimitError) as exc:
        await admit_run(conversation_id="c9", user_email=USER, app_settings=_settings(2))
    assert exc.value.limit == 2


def test_apply_run_limit_ignores_settings_without_a_cap():
    registry = get_run_registry()
    registry.set_max_concurrent_runs_per_user(4)
    assert apply_run_limit(None) == 4
    assert apply_run_limit(SimpleNamespace(max_concurrent_runs_per_user=object())) == 4
    assert apply_run_limit(_settings(7)) == 7


async def test_in_process_admission_never_suspends():
    """Without Redis, admission is as atomic on one loop as the old sync start."""
    assert isinstance(get_run_coordinator(), InProcessRunCoordinator)
    coro = admit_run(conversation_id="c1", user_email=USER, app_settings=_settings(5))
    with pytest.raises(StopIteration) as done:
        coro.send(None)
    assert done.value.value.conversation_id == "c1"


async def test_heartbeat_renews_live_claims_only(coord):
    live = await admit_run(conversation_id="c1", user_email=USER, app_settings=_settings(5))
    ended = await admit_run(conversation_id="c2", user_email=USER, app_settings=_settings(5))
    get_run_registry().set_status(ended.run_id, RunStatus.FAILED)
    await drain_pending_releases()

    await renew_live_claims()
    # The heartbeat carries the registry's status into the shared record.
    assert (await coord.read(live.run_id))["status"] == live.status.value
    assert (await coord.read(ended.run_id))["status"] == "failed"
    assert await _slots(coord) == 1


def test_release_without_a_loop_is_a_no_op(coord):
    claim = SimpleNamespace(run_id="r")
    admission.release_claim_soon(claim, "failed")  # must not raise


async def test_first_claim_starts_the_lease_heartbeat(coord):
    """Headless clients get renewal too: the heartbeat starts with the first claim."""
    assert admission._heartbeat is None or admission._heartbeat.done()
    await admit_run(conversation_id="c1", user_email=USER, app_settings=_settings(5))
    first = admission._heartbeat
    assert first is not None and not first.done()
    await admit_run(conversation_id="c2", user_email=USER, app_settings=_settings(5))
    assert admission._heartbeat is first  # one loop per event loop


async def test_in_process_admission_starts_no_heartbeat():
    await admit_run(conversation_id="c1", user_email=USER, app_settings=_settings(5))
    assert admission._heartbeat is None or admission._heartbeat.done()
