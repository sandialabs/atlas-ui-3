"""What a client and an operator see when a run loses its claim (#1218).

The run is cancelled and its turn is not saved (the maintainer's decision), so
the user must be told, the log must say what happened, and an outage must not
fill the log with one traceback per orphan sweep.
"""

import asyncio
import logging
from unittest.mock import MagicMock
from uuid import uuid4

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

# Fixtures and the two-replica helper live next to the lease tests.
from test_run_leases import EMAIL, LEASE, Replica, _isolation  # noqa: F401

from atlas.application.chat.runs import leases as leases_module
from atlas.application.chat.runs.leases import RunFencedError, set_lease_guard
from atlas.application.chat.runs.registry import RunStatus


@pytest.fixture
def server():
    return fakeredis.FakeServer()


def _frames(registry):
    """The public run records a connected client would receive, in order."""
    frames = []
    registry.add_listener(EMAIL, lambda record: frames.append(record.to_public_dict()))
    return frames


async def test_fence_during_a_redis_outage_reaches_the_client_as_a_fenced_failure(server):
    a = Replica(server, "A")
    frames = _frames(a.registry)
    record, task = await a.start()
    await a.guard.ensure_may_act(record.run_id)  # a proven renewal

    async def unreachable(claims, statuses):
        raise RedisConnectionError("redis is down")

    a.coordinator.renew = unreachable
    a.scripts.mirror = unreachable
    a.clock.now += LEASE  # a whole lease without proof
    with pytest.raises(RunFencedError):
        await a.guard.ensure_may_act(record.run_id)

    last = frames[-1]
    assert last["run_id"] == record.run_id
    assert last["status"] == "failed"
    assert last["end_reason"] == "fenced"
    assert last["conversation_id"] == record.conversation_id
    task.cancel()


async def test_a_user_stop_is_not_reported_as_a_fence(server):
    a = Replica(server, "A")
    frames = _frames(a.registry)
    record, task = await a.start()

    assert a.registry.cancel(record.run_id, EMAIL)

    last = frames[-1]
    assert last["status"] == "cancelled"
    assert last["end_reason"] == "user_cancelled"
    assert not any(f["end_reason"] == "fenced" for f in frames)
    assert a.registry.get(record.run_id).status is RunStatus.CANCELLED


async def _commit_as(chat, record, session):
    from atlas.application.chat.runs.context import set_current_run

    set_current_run(record.run_id, record.conversation_id)
    await chat._commit_turn(session, uuid4(), EMAIL, "m", None, is_incognito=False, save_floor=0)


def _chat_service():
    from atlas.application.chat import service as service_module

    chat = service_module.ChatService(
        llm=MagicMock(), tool_manager=MagicMock(), connection=MagicMock(),
        config_manager=MagicMock(), session_repository=MagicMock(),
        conversation_repository=MagicMock(),
    )
    chat._save_conversation = MagicMock(return_value=True)
    session = MagicMock()
    session.history.messages = []
    session.context = {}
    return chat, session


async def test_skipped_save_log_names_a_lapse_without_proof(server, caplog):
    from atlas.application.chat.runs import registry as registry_module

    a = Replica(server, "A")
    set_lease_guard(a.guard)
    record, task = await a.start()
    registry_module._registry = a.registry
    chat, session = _chat_service()

    a.guard._fence_local(a.guard._leases[record.run_id], record.run_id, leases_module.FENCE_REASON_NO_PROOF)
    with caplog.at_level(logging.WARNING):
        await asyncio.get_running_loop().create_task(_commit_as(chat, record, session))

    assert chat._save_conversation.call_count == 0
    messages = [r.getMessage() for r in caplog.records if "Not saving the turn" in r.getMessage()]
    assert len(messages) == 1
    assert leases_module.FENCE_REASON_NO_PROOF in messages[0]
    assert "takeover" not in messages[0]
    task.cancel()


async def test_skipped_save_log_names_a_refused_renewal_without_claiming_a_holder(server, caplog):
    from atlas.application.chat.runs import registry as registry_module

    a = Replica(server, "A")
    set_lease_guard(a.guard)
    record, task = await a.start()
    registry_module._registry = a.registry
    chat, session = _chat_service()

    a.guard._fence_local(a.guard._leases[record.run_id], record.run_id, leases_module.FENCE_REASON_RENEWAL_REFUSED)
    with caplog.at_level(logging.WARNING):
        await asyncio.get_running_loop().create_task(_commit_as(chat, record, session))

    messages = [r.getMessage() for r in caplog.records if "Not saving the turn" in r.getMessage()]
    assert len(messages) == 1
    # Redis refuses a lapsed lease and a held one alike: the text says so
    # rather than asserting either.
    assert "lapsed or another replica holds the run" in messages[0]
    assert "no takeover" not in messages[0]
    task.cancel()


class _OneSweep:
    """An ``asyncio.sleep`` that lets one sweep run, then ends the loop."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, _delay):
        self.calls += 1
        if self.calls > 1:
            raise asyncio.CancelledError


async def _run_sweep_loop(a, monkeypatch, error):
    async def failing_sweep():
        raise error

    a.guard.sweep_once = failing_sweep
    monkeypatch.setattr(leases_module.asyncio, "sleep", _OneSweep())
    with pytest.raises(asyncio.CancelledError):
        await a.guard._sweep_loop()


def _sweep_records(caplog):
    return [r for r in caplog.records if r.name == leases_module.logger.name and r.levelno >= logging.WARNING]


async def test_orphan_sweep_logs_one_line_when_redis_is_unreachable(server, monkeypatch, caplog):
    a = Replica(server, "A")
    with caplog.at_level(logging.WARNING):
        await _run_sweep_loop(a, monkeypatch, RedisConnectionError("Connection refused"))

    (record,) = _sweep_records(caplog)
    assert record.levelno == logging.WARNING
    assert "Redis unreachable" in record.getMessage()
    assert "ConnectionError" in record.getMessage()
    assert record.exc_info is None
    assert "Traceback" not in caplog.text


async def test_orphan_sweep_keeps_the_traceback_for_an_unexpected_error(server, monkeypatch, caplog):
    a = Replica(server, "A")
    with caplog.at_level(logging.WARNING):
        await _run_sweep_loop(a, monkeypatch, ValueError("bug"))

    (record,) = _sweep_records(caplog)
    assert record.getMessage() == "run orphan sweep failed"
    assert record.exc_info is not None


async def test_own_sweep_takeover_replaces_an_earlier_fence_reason(server):
    a = Replica(server, "A")
    record, task = await a.start()
    a.guard._fence_local(a.guard._leases[record.run_id], record.run_id, leases_module.FENCE_REASON_NO_PROOF)
    assert a.guard.fence_reason(record.run_id) == leases_module.FENCE_REASON_NO_PROOF

    await a.expire(record)
    assert await a.guard.sweep_once() == [record.run_id]

    assert a.guard.fence_reason(record.run_id) == leases_module.FENCE_REASON_TAKEN_OVER
    task.cancel()


async def test_projection_close_failure_during_an_outage_feeds_the_dependency_state(monkeypatch, caplog):
    from atlas.application.chat.runs import execution_state
    from atlas.core.coordination_metrics import coordination_metrics

    class DownStore:
        async def apply_event(self, event):
            raise RedisConnectionError("redis is down")

    monkeypatch.setattr(execution_state, "get_execution_store", lambda: DownStore())
    with caplog.at_level(logging.WARNING):
        closed = await leases_module._close_projection("run-x", {"conversation_id": "c", "user_email": EMAIL})

    assert closed is False
    assert coordination_metrics().dependencies()["redis"]["status"] == "unavailable"
    (record,) = [r for r in caplog.records if r.name == leases_module.logger.name]
    assert record.exc_info is None
