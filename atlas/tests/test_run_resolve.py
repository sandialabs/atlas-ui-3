"""``resolve``: which run an actor addresses, and where it is (issue #1128).

Replica A owns the "remote" runs (``cluster.registry``); the registry passed to
``resolve`` is replica B's, the replica that received the frame.
"""

# ruff: noqa: F811
import asyncio

import pytest
from test_run_control_service import OTHER, OWNER, _release, cluster  # noqa: F401  (fixture and helpers)

from atlas.application.chat.runs.registry import RunRegistry, RunStatus
from atlas.application.chat.runs.resolve import Address, Local, Nothing, Remote, Unknown, resolve
from atlas.application.chat.runs.run_control import SERVICE_ACTOR, user_actor

CONV = "conv-local"


@pytest.fixture
async def local():
    # Requested after ``cluster``: let replica A's inbox reader take its first
    # turn, because the shared fixture's teardown cannot stop a reader that
    # never started (it hangs on Python 3.11).
    await asyncio.sleep(0.05)
    registry = RunRegistry()
    yield registry
    tasks = [r.task for r in registry.all_records() if r.task is not None]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _local_run(local, conversation_id=CONV, user_email=OWNER):
    record = local.start(conversation_id=conversation_id, user_email=user_email)
    local.attach_task(record.run_id, asyncio.create_task(asyncio.sleep(30)))
    return record


async def _unreadable(cluster):
    async def fail(*_a, **_k):
        raise TimeoutError("redis timed out")

    cluster.b.bus.owner_of = fail
    cluster.b.bus.run_for_conversation = fail


async def _resolve(local, run_id=None, conversation_id=None, actor=None, **kwargs):
    return await resolve(local, actor or user_actor(OWNER), Address(run_id, conversation_id), **kwargs)


async def test_named_run_in_this_registry_is_local(cluster, local):
    run = _local_run(local)
    assert await _resolve(local, run.run_id) == Local(run)


async def test_named_run_wins_over_the_conversation(cluster, local):
    run = _local_run(local)
    other = _local_run(local, "conv-other")
    assert await _resolve(local, run.run_id, "conv-other") == Local(run)
    assert await _resolve(local, other.run_id, CONV) == Local(other)


async def test_named_run_in_the_directory_is_remote(cluster, local):
    run = await cluster.run()
    resolution = await _resolve(local, run.run_id)
    assert isinstance(resolution, Remote) and resolution.entry["replica"] == "replica-a"
    assert resolution.entry["run_id"] == run.run_id


async def test_named_run_beats_this_replicas_conversation_run(cluster, local):
    remote = await cluster.run()
    mine = _local_run(local)
    resolution = await _resolve(local, remote.run_id, mine.conversation_id)
    assert isinstance(resolution, Remote) and resolution.entry["run_id"] == remote.run_id


async def test_finished_remote_run_is_found_only_when_terminal_records_are_asked_for(cluster, local):
    run = await cluster.run()
    cluster.registry.set_status(run.run_id, RunStatus.COMPLETED)
    await _release(cluster, run)
    assert await _resolve(local, run.run_id) == Nothing()
    resolution = await _resolve(local, run.run_id, include_terminal=True)
    assert isinstance(resolution, Remote) and resolution.entry["terminal"] is True


async def test_stale_run_id_falls_back_to_the_local_conversation_run(cluster, local):
    run = _local_run(local)
    assert await _resolve(local, "stale", CONV) == Local(run)


async def test_no_run_id_resolves_the_local_conversation_run(cluster, local):
    run = _local_run(local)
    assert await _resolve(local, None, CONV) == Local(run)


async def test_conversation_in_the_directory_is_remote(cluster, local):
    run = await cluster.run()
    resolution = await _resolve(local, "stale", run.conversation_id)
    assert isinstance(resolution, Remote) and resolution.entry["run_id"] == run.run_id


async def test_a_finished_runs_conversation_is_nothing(cluster, local):
    run = await cluster.run()
    cluster.registry.set_status(run.run_id, RunStatus.COMPLETED)
    await _release(cluster, run)
    assert await _resolve(local, None, run.conversation_id, include_terminal=True) == Nothing()


async def test_unreadable_directory_is_unknown_never_nothing(cluster, local):
    run = await cluster.run()
    await _unreadable(cluster)
    assert isinstance(await _resolve(local, run.run_id), Unknown)
    assert isinstance(await _resolve(local, "stale", run.conversation_id), Unknown)
    assert isinstance(await _resolve(local, None, run.conversation_id), Unknown)


async def test_unreadable_directory_still_finds_the_local_conversation_run(cluster, local):
    run = _local_run(local)
    await _unreadable(cluster)
    assert await _resolve(local, "stale", CONV) == Local(run)


async def test_nothing_when_no_run_matches(cluster, local):
    assert await _resolve(local, "stale", "conv-none") == Nothing()
    assert await _resolve(local, "stale") == Nothing()


async def test_empty_address_is_nothing_without_reading_the_directory(cluster, local):
    await _unreadable(cluster)
    assert await _resolve(local) == Nothing()


async def test_a_run_id_is_only_located_among_the_actors_own_runs(cluster, local):
    mine = await cluster.run()
    theirs = _local_run(local, user_email=OTHER)
    assert await _resolve(local, theirs.run_id) == Nothing()
    assert await _resolve(local, mine.run_id, actor=user_actor(OTHER)) == Nothing()


async def test_another_users_conversation_run_is_returned_for_the_caller_to_refuse(cluster, local):
    run = await cluster.run()
    resolution = await _resolve(local, None, run.conversation_id, actor=user_actor(OTHER))
    assert isinstance(resolution, Remote) and resolution.entry["user_email"] == OWNER


async def test_the_service_actor_addresses_by_run_id_alone(cluster, local):
    run = await cluster.run()
    resolution = await resolve(local, SERVICE_ACTOR, Address(run.run_id, run.conversation_id))
    assert isinstance(resolution, Remote)
    assert await resolve(local, SERVICE_ACTOR, Address("stale", run.conversation_id)) == Nothing()
    mine = _local_run(local)
    assert await resolve(local, SERVICE_ACTOR, Address(mine.run_id, None)) == Local(mine)


async def test_an_entry_naming_this_replica_is_not_remote(cluster, local):
    # The registry already answered for runs this replica owns.
    run = await cluster.run()
    cluster.b.bus.replica_id = "replica-a"
    assert await _resolve(local, run.run_id) == Nothing()
    assert await _resolve(local, None, run.conversation_id) == Nothing()
