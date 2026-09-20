"""A run pauses -- and is resumable -- whichever path its request took.

The agent loop publishes tool approval requests through the connection
adapter, not the turn callback. Before ``RunRegistry.note_event`` only the
turn callback (and a launched run's child connection) updated the registry,
so a run started from the UI never reached ``waiting_for_input``: no "Needs
approval" marker, no stored request, nothing to replay when the conversation
was reopened, and the run sat until the approval timed out.
"""


import pytest
from starlette.websockets import WebSocketState

from atlas.application.chat.runs import RunRegistry, RunStatus
from atlas.application.chat.runs import registry as registry_module
from atlas.application.chat.runs.context import clear_current_run, set_current_run
from atlas.infrastructure.transport.websocket_connection_adapter import (
    WebSocketConnectionAdapter,
)

USER = "u@example.com"


class _FakeWebSocket:
    def __init__(self):
        self.client_state = WebSocketState.CONNECTED
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


@pytest.fixture
def registry(monkeypatch):
    reg = RunRegistry()
    monkeypatch.setattr(registry_module, "_registry", reg)
    yield reg
    clear_current_run()


def test_note_event_pauses_and_stores_the_request(registry):
    run = registry.start(conversation_id="c", user_email=USER)
    frame = {"type": "tool_approval_request", "tool_call_id": "t1", "run_id": run.run_id}

    registry.note_event(run.run_id, frame)

    record = registry.get(run.run_id)
    assert record.status == RunStatus.WAITING_FOR_INPUT
    assert record.waiting_on == "tool_approval_request"
    assert registry.pending_requests_for_conversation("c", USER) == [frame]


def test_note_event_is_idempotent_for_the_same_request(registry):
    run = registry.start(conversation_id="c", user_email=USER)
    seen = []
    registry.add_listener(USER, lambda r: seen.append(r.status))
    frame = {"type": "tool_approval_request", "tool_call_id": "t1"}

    registry.note_event(run.run_id, frame)
    registry.note_event(run.run_id, frame)

    assert seen.count(RunStatus.WAITING_FOR_INPUT) == 1


def test_note_event_clears_the_pause_when_the_tool_settles(registry):
    run = registry.start(conversation_id="c", user_email=USER)
    registry.note_event(run.run_id, {"type": "tool_approval_request", "tool_call_id": "t1"})

    registry.note_event(run.run_id, {"type": "tool_complete", "tool_call_id": "t1"})

    record = registry.get(run.run_id)
    assert record.status == RunStatus.RUNNING
    assert record.pending_request is None


def test_note_event_keeps_the_pause_when_a_sibling_tool_settles(registry):
    run = registry.start(conversation_id="c", user_email=USER)
    registry.note_event(run.run_id, {"type": "tool_approval_request", "tool_call_id": "t1"})

    registry.note_event(run.run_id, {"type": "tool_complete", "tool_call_id": "t2"})

    record = registry.get(run.run_id)
    assert record.status == RunStatus.WAITING_FOR_INPUT
    assert record.pending_request["tool_call_id"] == "t1"
    assert registry.pending_requests_for_conversation("c", USER) == [
        {"type": "tool_approval_request", "tool_call_id": "t1"}
    ]


def test_note_event_matches_elicitation_ids(registry):
    run = registry.start(conversation_id="c", user_email=USER)
    registry.note_event(run.run_id, {"type": "elicitation_request", "elicitation_id": "e1"})

    registry.note_event(run.run_id, {"type": "tool_complete", "elicitation_id": "e2"})

    record = registry.get(run.run_id)
    assert record.status == RunStatus.WAITING_FOR_INPUT

    registry.note_event(run.run_id, {"type": "tool_complete", "elicitation_id": "e1"})
    assert registry.get(run.run_id).status == RunStatus.RUNNING


def test_note_event_treats_an_unidentified_settle_as_clearing(registry):
    run = registry.start(conversation_id="c", user_email=USER)
    registry.note_event(run.run_id, {"type": "tool_approval_request", "tool_call_id": "t1"})

    registry.note_event(run.run_id, {"type": "tool_error"})

    record = registry.get(run.run_id)
    assert record.status == RunStatus.RUNNING
    assert record.pending_request is None


def test_note_event_leaves_a_run_with_no_pending_request_alone(registry):
    run = registry.start(conversation_id="c", user_email=USER)

    registry.note_event(run.run_id, {"type": "tool_complete", "tool_call_id": "t2"})

    record = registry.get(run.run_id)
    assert record.status == RunStatus.QUEUED
    assert record.pending_request is None


def test_note_event_ignores_terminal_runs_and_garbage(registry):
    run = registry.start(conversation_id="c", user_email=USER)
    registry.cancel(run.run_id, USER)

    registry.note_event(run.run_id, {"type": "tool_approval_request", "tool_call_id": "t1"})
    registry.note_event(run.run_id, "not a frame")
    registry.note_event(None, {"type": "tool_approval_request"})

    assert registry.get(run.run_id).status == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_adapter_marks_the_current_run_waiting(registry):
    run = registry.start(conversation_id="c", user_email=USER)
    ws = _FakeWebSocket()
    adapter = WebSocketConnectionAdapter(ws, USER)
    set_current_run(run.run_id, "c")

    await adapter.send_json({"type": "tool_approval_request", "tool_call_id": "t1"})

    record = registry.get(run.run_id)
    assert record.status == RunStatus.WAITING_FOR_INPUT
    assert record.pending_request["run_id"] == run.run_id
    assert record.pending_request["conversation_id"] == "c"
    assert ws.sent[0]["run_id"] == run.run_id

    await adapter.send_json({"type": "tool_complete", "tool_call_id": "t1"})
    assert registry.get(run.run_id).status == RunStatus.RUNNING


@pytest.mark.asyncio
async def test_adapter_leaves_untracked_turns_alone(registry):
    ws = _FakeWebSocket()
    adapter = WebSocketConnectionAdapter(ws, USER)
    clear_current_run()

    await adapter.send_json({"type": "tool_approval_request", "tool_call_id": "t1"})

    assert ws.sent[0] == {"type": "tool_approval_request", "tool_call_id": "t1"}
    assert registry.active_for_user(USER) == []


@pytest.mark.asyncio
async def test_adapter_bookkeeping_survives_a_dead_socket(registry):
    run = registry.start(conversation_id="c", user_email=USER)
    ws = _FakeWebSocket()
    ws.client_state = WebSocketState.DISCONNECTED
    adapter = WebSocketConnectionAdapter(ws, USER)
    set_current_run(run.run_id, "c")

    await adapter.send_json({"type": "tool_approval_request", "tool_call_id": "t1"})

    assert ws.sent == []
    assert registry.get(run.run_id).status == RunStatus.WAITING_FOR_INPUT
