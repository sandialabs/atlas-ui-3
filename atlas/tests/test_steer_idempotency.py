"""A steer with a request id is queued at most once per run (issue #1128).

The registry owns the dedup, so it holds whichever way the steer arrived.
"""

from atlas.application.chat.agent.steering import (
    STEERING_QUEUE_MAXSIZE,
    STEERING_SEEN_MAXSIZE,
    SteeringChannel,
)
from atlas.application.chat.runs import get_run_registry
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


def test_a_non_string_request_id_is_treated_as_absent():
    channel = SteeringChannel()
    channel.remember_outcome(["x"], STEER_QUEUED)
    channel.remember_outcome({"a": 1}, STEER_QUEUED)
    assert channel.recall_outcome(["x"]) is None
    assert channel.recall_outcome({"a": 1}) is None
    registry = RunRegistry()
    record, queue_channel = _run(registry)
    assert registry.steer(record.run_id, "x", ["bad"]) == STEER_QUEUED
    assert registry.steer(record.run_id, "x", ["bad"]) == STEER_QUEUED
    assert queue_channel.queue.qsize() == 2


from test_websocket_conversation_classification import (  # noqa: E402,F401
    CONV,
    USER,
    _connect,
    chat_service,
)


def test_a_steer_repeat_for_a_finished_run_never_starts_a_turn(chat_service):
    from fastapi.testclient import TestClient
    from main import app

    with _connect(TestClient(app)) as ws:
        ws.send_json({"type": "chat", "content": "go left", "model": "m", "conversation_id": CONV,
                      "request_id": "r1", "steer_only": True})
        frame = ws.receive_json()
    assert frame["error_type"] == "steer_run_finished" and frame["steering"] is True
    chat_service.handle_chat_message.assert_not_called()


def test_a_retry_after_the_run_ended_answers_the_remembered_outcome():
    registry = RunRegistry()
    record, _ = _run(registry)
    assert registry.steer(record.run_id, "left", "r1") == STEER_QUEUED
    registry.set_status(record.run_id, RunStatus.COMPLETED)
    assert registry.steer(record.run_id, "left", "r1") == STEER_QUEUED
    # An id that was never queued still finds the run finished.
    assert registry.steer(record.run_id, "right", "r2") == STEER_RUN_FINISHED


def test_a_drained_leftover_is_not_acknowledged_again():
    registry = RunRegistry()
    record, channel = _run(registry)
    assert registry.steer(record.run_id, "late", "r1") == STEER_QUEUED
    assert registry.steer(record.run_id, "kept", "r2") == STEER_QUEUED
    assert list(channel.drain_leftovers()) == ["late", "kept"]
    channel.deactivate()
    # Nothing is queued any more, so the retry is not told it is.
    assert registry.steer(record.run_id, "late", "r1") == STEER_NOT_DRAINING
    assert channel.queue.qsize() == 0
    channel.activate()
    assert registry.steer(record.run_id, "late", "r1") == STEER_QUEUED
    assert channel.queue.qsize() == 1


def test_a_null_payload_is_queued_as_none_and_a_finished_run_keeps_ids_only():
    registry = RunRegistry()
    record, channel = _run(registry)
    assert registry.steer(record.run_id, None, "r1") == STEER_QUEUED
    assert channel.queue.get_nowait() is None
    assert registry.steer(record.run_id, "kept", "r2") == STEER_QUEUED
    registry.set_status(record.run_id, RunStatus.CANCELLED)
    # The queued text is not retained with the finished run, only the ids.
    assert record.steering is None
    assert record.steer_seen == {"r1": STEER_QUEUED, "r2": STEER_QUEUED}


def _local_run(_chat_service=None, active=True):
    registry = get_run_registry()
    run = registry.start(conversation_id=CONV, user_email=USER)
    steering = SteeringChannel()
    if active:
        steering.activate()
    run.steering = steering
    return registry, run, steering


def _next_non_status(ws, wanted):
    frame = ws.receive_json()
    while frame.get("type") != wanted:
        frame = ws.receive_json()
    return frame


def test_the_same_request_id_twice_over_one_socket_queues_once(chat_service):
    from fastapi.testclient import TestClient
    from main import app

    registry, run, steering = _local_run(chat_service)
    frame_in = {"type": "chat", "content": "go left", "model": "m", "conversation_id": CONV,
                "request_id": "r1"}
    try:
        with _connect(TestClient(app)) as ws:
            ws.send_json(frame_in)
            first = _next_non_status(ws, "agent_update")
            ws.send_json(frame_in)
            second = _next_non_status(ws, "agent_update")
    finally:
        registry.cancel(run.run_id, USER)
    assert first["update_type"] == second["update_type"] == "steering_queued"
    assert steering.queue.qsize() == 1


def test_local_steer_refusals_echo_the_request_id(chat_service):
    from fastapi.testclient import TestClient
    from main import app

    registry, run, steering = _local_run(chat_service)
    try:
        with _connect(TestClient(app)) as ws:
            for i in range(STEERING_QUEUE_MAXSIZE):
                ws.send_json({"type": "chat", "content": str(i), "model": "m",
                              "conversation_id": CONV, "request_id": f"fill-{i}"})
                _next_non_status(ws, "agent_update")
            ws.send_json({"type": "chat", "content": "late", "model": "m",
                          "conversation_id": CONV, "request_id": "late-1"})
            full = _next_non_status(ws, "error")
            steering.deactivate()
            ws.send_json({"type": "chat", "content": "later", "model": "m",
                          "conversation_id": CONV, "request_id": "later-1"})
            busy = _next_non_status(ws, "error")
            ws.send_json({"type": "chat", "content": "x", "model": "m", "conversation_id": "conv-gone",
                          "request_id": "gone-1", "steer_only": True})
            finished = _next_non_status(ws, "error")
    finally:
        registry.cancel(run.run_id, USER)
    assert (full["error_type"], full["steering"], full["request_id"]) == (
        "steering_queue_full", True, "late-1")
    assert (busy["error_type"], busy["steering"], busy["request_id"]) == (
        "conversation_busy", True, "later-1")
    assert (finished["error_type"], finished["steering"], finished["request_id"]) == (
        "steer_run_finished", True, "gone-1")
