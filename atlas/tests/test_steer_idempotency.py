"""A steer with a request id is queued at most once per run (issue #1128).

The registry owns the dedup, so it holds whichever way the steer arrived.
"""

from atlas.application.chat.agent.steering import (
    STEERING_QUEUE_MAXSIZE,
    STEERING_SEEN_MAXSIZE,
    SteeringChannel,
)
from atlas.application.chat.runs.registry import (
    STEER_NOT_DRAINING,
    STEER_QUEUE_FULL,
    STEER_QUEUED,
    STEER_RUN_FINISHED,
    RunRegistry,
    RunStatus,
)

USER = "owner@example.com"


def _run(registry, active=True, conversation_id="conv-1"):
    channel = SteeringChannel()
    if active:
        channel.activate()
    record = registry.start(conversation_id=conversation_id, user_email=USER, steering=channel)
    return record, channel


def test_a_repeated_request_id_is_queued_once():
    registry = RunRegistry()
    record, channel = _run(registry)
    assert registry.steer(record.run_id, "left", "r1") == STEER_QUEUED
    assert registry.steer(record.run_id, "left", "r1") == STEER_QUEUED
    assert channel.queue.qsize() == 1
    assert registry.steer(record.run_id, "right", "r2") == STEER_QUEUED
    assert channel.queue.qsize() == 2


def test_a_steer_without_a_request_id_is_never_deduplicated():
    registry = RunRegistry()
    record, channel = _run(registry)
    registry.steer(record.run_id, "left")
    registry.steer(record.run_id, "left")
    assert channel.queue.qsize() == 2


def test_a_full_queue_is_not_remembered_so_a_retry_can_succeed():
    registry = RunRegistry()
    record, channel = _run(registry)
    for i in range(STEERING_QUEUE_MAXSIZE):
        assert registry.steer(record.run_id, str(i), f"fill-{i}") == STEER_QUEUED
    assert registry.steer(record.run_id, "late", "late") == STEER_QUEUE_FULL
    channel.queue.get_nowait()
    assert registry.steer(record.run_id, "late", "late") == STEER_QUEUED
    assert channel.queue.qsize() == STEERING_QUEUE_MAXSIZE


def test_refusals_for_a_run_that_cannot_take_a_steer():
    registry = RunRegistry()
    idle, _ = _run(registry, active=False)
    assert registry.steer(idle.run_id, "x", "r1") == STEER_NOT_DRAINING
    assert registry.steer("no-such-run", "x", "r1") == STEER_RUN_FINISHED
    live, _ = _run(registry, conversation_id="conv-2")
    registry.set_status(live.run_id, RunStatus.COMPLETED)
    assert registry.steer(live.run_id, "x", "r1") == STEER_RUN_FINISHED


def test_the_same_request_id_on_two_runs_is_two_steers():
    registry = RunRegistry()
    first, first_channel = _run(registry)
    second, _ = _run(registry, conversation_id="conv-2")
    registry.steer(first.run_id, "x", "same")
    registry.steer(second.run_id, "x", "same")
    assert first_channel.queue.qsize() == 1 and second.steering.queue.qsize() == 1


def test_the_seen_set_is_bounded_and_forgets_the_oldest():
    channel = SteeringChannel()
    for i in range(STEERING_SEEN_MAXSIZE + 10):
        channel.remember_outcome(f"r{i}", STEER_QUEUED)
    assert channel.recall_outcome("r0") is None
    assert channel.recall_outcome(f"r{STEERING_SEEN_MAXSIZE + 9}") == STEER_QUEUED
