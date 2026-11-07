"""Run-control transport over Redis Streams (issue #1128).

Two replicas on one fakeredis server, each a ``RedisRunCoordinator`` (run
admission, which writes the run record) and a ``RedisRunControlBus`` built on
its keys and replica id, as the app factory binds them. These cover the
transport contract: routing by the shared record, correlated replies,
request-id deduplication, deadlines, timeouts and generation fencing.
"""

import asyncio
import json
import time

import fakeredis
import pytest

from atlas.application.chat.runs import control_bus
from atlas.application.chat.runs.control_bus import (
    COMMAND_EXPIRED,
    COMMAND_TIMEOUT,
    REQUEST_CONFLICT,
    STALE_GENERATION,
    RedisRunControlBus,
    command_scope,
    reply,
)
from atlas.infrastructure.redis import RedisClientManager, RedisSettings
from atlas.infrastructure.redis.run_coordinator import RedisRunCoordinator

USER = {"kind": "user", "email": "owner@example.com"}


class Replica:
    def __init__(self, server, replica_id, **coordinator_kwargs):
        manager = RedisClientManager(
            RedisSettings(url="redis://fake/0", key_prefix="t"),
            client=fakeredis.FakeAsyncRedis(server=server),
        )
        self.coordinator = RedisRunCoordinator(manager, replica_id=replica_id, **coordinator_kwargs)
        self.bus = RedisRunControlBus(manager, self.coordinator)

    async def admit(self, run_id="run-1", conversation_id="conv-1", user="owner@example.com"):
        return await self.coordinator.claim(
            run_id=run_id, conversation_id=conversation_id, user_email=user, max_runs_per_user=5
        )


@pytest.fixture
async def pair():
    server = fakeredis.FakeServer()
    owner, sender = Replica(server, "replica-a"), Replica(server, "replica-b")
    yield owner, sender
    await owner.bus.stop()
    await sender.bus.stop()


def _counting_handler(calls):
    async def handle(envelope):
        calls.append(envelope)
        return reply(True, echoed=envelope["args"].get("n"))
    return handle


async def _send(sender, command="stop", args=None, *, run_id="run-1", actor=USER, timeout=3, **kwargs):
    """Route the way ``run_control`` does: by the record, with its generation."""
    entry = await sender.bus.owner_of(run_id)
    assert entry is not None, f"{run_id} is not routable"
    return await sender.bus.send(entry["replica"], command, run_id, actor, args or {},
                                 generation=entry["generation"], timeout=timeout, **kwargs)


async def test_directory_reads_the_coordinators_record(pair):
    owner, sender = pair
    claim = await owner.admit()
    entry = await sender.bus.owner_of("run-1")
    assert entry["replica"] == "replica-a" and entry["user_email"] == "owner@example.com"
    assert entry["generation"] == claim.generation
    assert (await sender.bus.run_for_conversation("conv-1"))["run_id"] == "run-1"
    await owner.coordinator.release(claim, "completed")
    # The released record is kept for a while, but nothing routes to it.
    assert await sender.bus.owner_of("run-1") is None
    assert await sender.bus.run_for_conversation("conv-1") is None


async def test_command_is_answered_with_a_correlated_reply(pair):
    owner, sender = pair
    await owner.admit()
    calls = []
    owner.bus.start(_counting_handler(calls))
    answer = await _send(sender, args={"n": 7})
    assert answer["ok"] is True and answer["payload"] == {"echoed": 7}
    assert answer["replica"] == "replica-a"
    assert len(calls) == 1 and calls[0]["actor"] == USER and calls[0]["sent_by"] == "replica-b"


async def test_retried_request_id_executes_once(pair):
    owner, sender = pair
    await owner.admit()
    calls = []
    owner.bus.start(_counting_handler(calls))
    first = await _send(sender, "steer", {"n": 1}, request_id="r1")
    second = await _send(sender, "steer", {"n": 1}, request_id="r1")
    assert first["ok"] and second["ok"]
    assert second.get("duplicate") is True and not first.get("duplicate")
    assert len(calls) == 1


async def test_same_request_id_from_another_actor_is_independent(pair):
    owner, sender = pair
    await owner.admit()
    calls = []
    owner.bus.start(_counting_handler(calls))
    other = {"kind": "user", "email": "other@example.com"}
    await _send(sender, "steer", request_id="r1")
    answer = await _send(sender, "steer", actor=other, request_id="r1")
    assert not answer.get("duplicate")
    assert len(calls) == 2


async def test_request_id_reused_for_another_command_is_refused(pair):
    owner, sender = pair
    await owner.admit()
    calls = []
    owner.bus.start(_counting_handler(calls))
    await _send(sender, "steer", request_id="r1")
    answer = await _send(sender, "stop", request_id="r1")
    assert answer["ok"] is False and answer["code"] == REQUEST_CONFLICT
    assert len(calls) == 1


async def test_owner_that_never_answers_times_out(pair):
    owner, sender = pair
    await owner.admit()
    started = time.monotonic()
    answer = await _send(sender, timeout=0.3)
    assert answer["ok"] is False and answer["code"] == COMMAND_TIMEOUT
    assert time.monotonic() - started < 2


async def test_command_read_after_its_deadline_is_not_executed(pair):
    owner, sender = pair
    await owner.admit()
    calls = []
    # The sender gives up while the owner is not consuming.
    answer = await _send(sender, request_id="late", timeout=0.2)
    assert answer["code"] == COMMAND_TIMEOUT
    owner.bus.start(_counting_handler(calls))
    reply_key = owner.coordinator.keys.control_reply(command_scope(USER, "late"))
    stored = None
    for _ in range(50):
        stored = await owner.bus._client().xread({reply_key: "0-0"})
        if stored:
            break
        await asyncio.sleep(0.05)
    late = json.loads(stored[0][1][0][1][b"c"])
    assert late["code"] == COMMAND_EXPIRED
    assert calls == []
    # The id was released, so a retry executes.
    retry = await _send(sender, request_id="late")
    assert retry["ok"] is True and len(calls) == 1


async def test_envelope_with_a_forged_scope_is_dropped(pair):
    owner, _sender = pair
    claim = await owner.admit()
    calls = []
    owner.bus._handler = _counting_handler(calls)
    await owner.bus._dispatch({
        "v": control_bus.PROTOCOL_VERSION,
        "request_id": "r1",
        "scope": command_scope({"kind": "service"}, "r1"),
        "command": "stop",
        "run_id": "run-1",
        "generation": claim.generation,
        "actor": USER,
        "args": {},
        "deadline": time.time() + 5,
    })
    assert calls == []


async def test_refusal_is_not_remembered_so_a_retry_runs(pair):
    owner, sender = pair
    await owner.admit()
    calls = []

    async def handle(envelope):
        calls.append(envelope)
        return reply(len(calls) > 1, "ok" if len(calls) > 1 else "steering_queue_full")

    owner.bus.start(handle)
    first = await _send(sender, "steer", request_id="r1")
    second = await _send(sender, "steer", request_id="r1")
    assert first["ok"] is False and second["ok"] is True and not second.get("duplicate")
    assert len(calls) == 2


async def test_command_with_another_generation_is_fenced(pair):
    owner, sender = pair
    claim = await owner.admit()
    calls = []
    owner.bus.start(_counting_handler(calls))
    for generation in (claim.generation - 1, None):
        answer = await sender.bus.send("replica-a", "stop", "run-1", USER,
                                       generation=generation, request_id=f"g{generation}", timeout=3)
        assert answer["ok"] is False and answer["code"] == STALE_GENERATION, answer
    assert calls == []
    # Refused before the dedup record was taken: the same id with the current
    # generation runs.
    retry = await sender.bus.send("replica-a", "stop", "run-1", USER,
                                  generation=claim.generation, request_id="gNone", timeout=3)
    assert retry["ok"] is True and len(calls) == 1


async def test_owner_whose_claim_moved_on_refuses(pair):
    server = fakeredis.FakeServer()
    owner = Replica(server, "replica-a", lease_seconds=0.2)
    newer = Replica(server, "replica-b")
    try:
        stale = await owner.admit()
        stale_entry = await newer.bus.owner_of("run-1")
        await asyncio.sleep(0.3)  # A's lease lapses (a long pause, say)
        fresh = await newer.admit()  # and the run is claimed again elsewhere
        assert fresh.generation > stale.generation
        calls = []
        owner.bus.start(_counting_handler(calls))
        # A sender still holding the old routing reaches A, which must not act.
        answer = await newer.bus.send(stale_entry["replica"], "stop", "run-1", USER,
                                      generation=stale_entry["generation"], timeout=3)
        assert answer["code"] == STALE_GENERATION and calls == []
    finally:
        await owner.bus.stop()
        await newer.bus.stop()


async def test_command_for_a_run_without_a_record_is_fenced(pair):
    owner, sender = pair
    calls = []
    owner.bus.start(_counting_handler(calls))
    answer = await sender.bus.send("replica-a", "stop", "ghost", USER, generation=1, timeout=3)
    assert answer["code"] == STALE_GENERATION and calls == []


def test_a_run_the_leases_interrupted_is_not_routed():
    from atlas.application.chat.runs.run_directory import owner_entry

    live = {"run_id": "r", "conversation_id": "c", "user_email": "u@x", "owner": "replica-a",
            "generation": "4", "status": "running", "lease_expires_at": "123"}
    assert owner_entry(live)["generation"] == 4
    # The orphan sweep's takeover: owner emptied, generation bumped, failed.
    taken_over = {**live, "owner": "", "generation": "5", "status": "failed",
                  "interrupted": "1", "interrupted_by": "replica-b"}
    assert owner_entry(taken_over) is None


async def test_a_claim_the_heartbeat_found_lost_is_fenced_at_once(pair):
    from types import SimpleNamespace

    from atlas.application.chat.runs import admission

    owner, sender = pair
    await owner.admit()
    calls = []
    owner.bus.start(_counting_handler(calls))
    assert owner.bus._claim_lost in admission._claim_lost_listeners
    # What renew_live_claims does when renewal refuses a live run's claim. The
    # shared record still names A at its generation; A refuses anyway.
    for listener in list(admission._claim_lost_listeners):
        listener(SimpleNamespace(run_id="run-1"))
    answer = await _send(sender)
    assert answer["code"] == STALE_GENERATION and calls == []
    await owner.bus.stop()
    assert owner.bus._claim_lost not in admission._claim_lost_listeners


def test_a_stop_may_still_reach_the_last_owner_of_a_finished_run():
    from atlas.application.chat.runs.run_directory import owner_entry

    released = {"run_id": "r", "conversation_id": "c", "user_email": "u@x", "owner": "replica-a",
                "generation": "4", "status": "completed", "lease_expires_at": "0"}
    assert owner_entry(released) is None
    entry = owner_entry(released, include_terminal=True)
    assert entry["replica"] == "replica-a" and entry["terminal"] is True
    # A run the orphan sweep took over names no owner, so not even a stop goes.
    taken_over = {**released, "owner": "", "status": "failed", "interrupted": "1"}
    assert owner_entry(taken_over, include_terminal=True) is None


async def test_waiting_senders_are_bounded_and_the_excess_is_never_sent(pair):
    owner, sender = pair
    await owner.admit()  # A never consumes: every send waits for its timeout
    sender.bus._send_slots = asyncio.Semaphore(1)
    first = asyncio.create_task(_send(sender, request_id="held", timeout=1.0))
    await asyncio.sleep(0.1)
    excess = await _send(sender, request_id="excess", timeout=0.2)
    assert excess["code"] == COMMAND_TIMEOUT
    assert (await first)["code"] == COMMAND_TIMEOUT
    inbox = owner.coordinator.keys.control_inbox("replica-a")
    sent = [json.loads(fields[b"c"])["request_id"]
            for _id, fields in await sender.bus._client().xrange(inbox)]
    assert sent == ["held"]


async def test_deadlines_use_the_shared_clock_not_the_replicas(pair, monkeypatch):
    # Both replicas' wall clocks are an hour off; the deadline is stamped and
    # checked on Redis TIME, so the command still executes.
    owner, sender = pair
    await owner.admit()
    calls = []
    owner.bus.start(_counting_handler(calls))
    from types import SimpleNamespace

    skewed = time.time() + 3600
    monkeypatch.setattr(control_bus, "time", SimpleNamespace(time=lambda: skewed, monotonic=time.monotonic))
    answer = await _send(sender, request_id="skewed")
    assert answer["ok"] is True and len(calls) == 1
    shared = await sender.bus.shared_now()
    assert abs(calls[0]["deadline"] - shared) < 10


async def test_read_commands_are_deleted_from_the_inbox(pair):
    # A command's arguments (a steering message) are not retained in Redis
    # once the owner has read them.
    owner, sender = pair
    await owner.admit()
    owner.bus.start(_counting_handler([]))
    answer = await _send(sender, "steer", {"content": "secret"}, request_id="r1")
    assert answer["ok"] is True
    inbox = owner.coordinator.keys.control_inbox("replica-a")
    assert await sender.bus._client().xrange(inbox) == []


async def test_reader_waits_for_a_dispatch_slot_before_taking_more(pair):
    owner, sender = pair
    await owner.admit()
    gate = asyncio.Event()
    started = []

    async def slow(envelope):
        started.append(envelope["request_id"])
        await gate.wait()
        return reply(True)

    owner.bus._dispatch_slots = asyncio.Semaphore(1)
    owner.bus.start(slow)
    first = asyncio.create_task(_send(sender, request_id="a", timeout=3))
    second = asyncio.create_task(_send(sender, request_id="b", timeout=3))
    await asyncio.sleep(0.3)
    assert len(started) == 1
    assert len(owner.bus._handlers) == 1  # no task exists for the waiting command
    gate.set()
    assert (await first)["ok"] and (await second)["ok"]
    assert sorted(started) == ["a", "b"]


async def test_oversized_command_is_refused_before_it_is_written(pair):
    owner, sender = pair
    await owner.admit()
    calls = []
    owner.bus.start(_counting_handler(calls))
    big = "x" * (control_bus.MAX_COMMAND_BYTES + 1)
    answer = await _send(sender, "steer", {"content": big}, request_id="big")
    assert answer["ok"] is False and answer["code"] == control_bus.COMMAND_TOO_LARGE
    inbox = owner.coordinator.keys.control_inbox("replica-a")
    assert await sender.bus._client().xlen(inbox) == 0
    assert calls == []


async def test_unread_inbox_expires_quickly(pair):
    # Commands to a dead owner (and their arguments) do not outlive the
    # short inbox TTL; none of them could still execute anyway.
    owner, sender = pair
    await owner.admit()  # A never consumes
    await _send(sender, "steer", {"content": "private"}, request_id="r1", timeout=0.2)
    inbox = owner.coordinator.keys.control_inbox("replica-a")
    ttl = await sender.bus._client().ttl(inbox)
    assert 0 < ttl <= control_bus.INBOX_TTL_SECONDS <= 300


def test_oversized_steer_keeps_the_client_running_state():
    from atlas.application.chat.runs.remote_run_frames import steer_frame

    frame = steer_frame(reply(False, control_bus.COMMAND_TOO_LARGE, "too large"),
                        {"run_id": "run-1", "conversation_id": "conv-1"})
    assert frame["error_type"] == control_bus.COMMAND_TOO_LARGE and frame["steering"] is True
    assert frame["message"] == "too large"


async def test_off_record_command_is_refused_before_it_is_written(pair):
    # Incognito / not-saved content never reaches the shared inbox.
    owner, sender = pair
    await owner.admit()
    calls = []
    owner.bus.start(_counting_handler(calls))
    answer = await _send(sender, "steer", {"content": "private"}, request_id="r1", off_record=True)
    assert answer["ok"] is False and answer["code"] == control_bus.OFF_RECORD
    inbox = owner.coordinator.keys.control_inbox("replica-a")
    assert await sender.bus._client().xlen(inbox) == 0
    assert calls == []


def test_off_record_steer_keeps_the_client_running_state():
    from atlas.application.chat.runs.remote_run_frames import steer_frame

    frame = steer_frame(reply(False, control_bus.OFF_RECORD, "not saved"),
                        {"run_id": "run-1", "conversation_id": "conv-1"})
    assert frame["error_type"] == control_bus.OFF_RECORD and frame["steering"] is True
    assert frame["run_id"] == "run-1"


async def test_reply_that_lands_as_the_sender_gives_up_is_still_read(pair):
    # Every blocking read misses the reply (it lands just as the read
    # returns); the non-blocking read after the deadline must find it, or a
    # retry would execute the command a second time.
    owner, sender = pair
    await owner.admit()
    calls = []
    owner.bus.start(_counting_handler(calls))
    client = sender.bus._client()
    real_xread = client.xread

    async def missing_xread(streams, block=None, count=None):
        if block is not None:
            await asyncio.sleep(block / 1000)
            return []
        return await real_xread(streams, block=block, count=count)

    client.xread = missing_xread
    try:
        answer = await _send(sender, "steer", {"n": 3}, request_id="late", timeout=0.3)
    finally:
        client.xread = real_xread
    assert answer["ok"] is True and answer["payload"] == {"echoed": 3}
    assert len(calls) == 1
