"""End-to-end elicitation and background-task tests against real FastMCP servers.

These start the bundled demo servers over STDIO and drive them through
``MCPToolManager``, so they cover what mock-based tests cannot: the wire shape
of MCP 2026-07-28 multi round-trip requests (SEP-2322), which task the
elicitation handler runs in, and the SEP-2663 task lifecycle.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from atlas.application.chat.elicitation_manager import get_elicitation_manager
from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools.client import MCPToolManager

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ELICITATION_DEMO = PROJECT_ROOT / "atlas" / "mcp" / "elicitation_demo" / "main.py"
TASK_DEMO = PROJECT_ROOT / "atlas" / "mcp" / "task_demo" / "main.py"

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


def _stdio_client(manager: MCPToolManager, server_name: str, script: Path, mode: str) -> Client:
    env = {**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    transport = StdioTransport(command=sys.executable, args=[str(script)], env=env)
    return Client(transport, mode=mode, **manager._client_input_kwargs(server_name))


@pytest.fixture
def manager():
    return MCPToolManager(config_path="/tmp/nonexistent_mcp_test.json")


class _Answering:
    """An update callback that answers each elicitation as its owner would."""

    def __init__(self, user_email: str, answers: Dict[str, Any]):
        self.user_email = user_email
        self.answers = answers
        self.events: List[Dict[str, Any]] = []

    async def __call__(self, event: Dict[str, Any]) -> None:
        self.events.append(event)
        if event.get("type") != "elicitation_request":
            return
        handled = get_elicitation_manager().handle_elicitation_response(
            elicitation_id=event["elicitation_id"],
            action="accept",
            data=self.answers[event["message"]],
            user_email=self.user_email,
        )
        assert handled is True

    @property
    def requests(self) -> List[Dict[str, Any]]:
        return [e for e in self.events if e.get("type") == "elicitation_request"]


async def _call(manager, server_name, tool_name, tool_call_id, update_cb, user_email):
    tool_call = ToolCall(id=tool_call_id, name=f"{server_name}_{tool_name}", arguments={})
    async with manager._use_elicitation_context(server_name, tool_call, update_cb, user_email):
        result = await manager.call_tool(
            server_name,
            tool_name,
            {},
            meta={"tool_call_id": tool_call_id},
            update_cb=update_cb,
        )
    return result.content[0].text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,protocol", [("auto", MODERN), ("legacy", LEGACY)])
async def test_demo_elicits_on_both_protocol_eras(manager, mode, protocol):
    """One tool body serves a 2026-07-28 client and a handshake-era client."""
    client = _stdio_client(manager, "elicitation_demo", ELICITATION_DEMO, mode)
    manager.clients["elicitation_demo"] = client
    update_cb = _Answering("alice@example.com", {"What's your name?": "Ada"})

    async with client:
        assert client.protocol_version == protocol
        text = await _call(
            manager, "elicitation_demo", "get_user_name", "call_1", update_cb, "alice@example.com"
        )

    assert text == "Hello, Ada! Nice to meet you."
    (request,) = update_cb.requests
    assert request["tool_call_id"] == "call_1"
    assert request["mode"] == "form"
    assert request["response_schema"]["properties"]["value"]["type"] == "string"


@pytest.mark.asyncio
async def test_multi_turn_answers_survive_each_round_trip(manager):
    """Each question is a separate tools/call; earlier answers ride in request_state."""
    client = _stdio_client(manager, "elicitation_demo", ELICITATION_DEMO, "auto")
    manager.clients["elicitation_demo"] = client
    update_cb = _Answering(
        "alice@example.com",
        {
            "What's your name?": "Ada",
            "What's your age?": 36,
            "What's your favorite food?": "sushi",
            "How satisfied are you with this survey? (1-10)": 9,
        },
    )

    async with client:
        text = await _call(
            manager, "elicitation_demo", "multi_turn_survey", "call_1", update_cb, "alice@example.com"
        )

    assert [r["message"] for r in update_cb.requests] == [
        "What's your name?",
        "What's your age?",
        "What's your favorite food?",
        "How satisfied are you with this survey? (1-10)",
    ]
    assert "Name: Ada" in text and "Age: 36" in text
    assert "Favorite Food: sushi" in text and "Satisfaction Rating: 9/10" in text


@pytest.mark.asyncio
async def test_one_round_trip_can_carry_several_questions(manager):
    """A single InputRequiredResult may hold more than one request."""
    client = _stdio_client(manager, "elicitation_demo", ELICITATION_DEMO, "auto")
    manager.clients["elicitation_demo"] = client
    update_cb = _Answering(
        "alice@example.com",
        {"What size pizza?": "m", "Pick any toppings you like:": ["olives", "onions"]},
    )

    async with client:
        text = await _call(
            manager, "elicitation_demo", "choose_toppings", "call_1", update_cb, "alice@example.com"
        )

    assert text == "Ordered a size m pizza with olives, onions."
    assert {r["message"] for r in update_cb.requests} == {
        "What size pizza?",
        "Pick any toppings you like:",
    }
    assert all(r["tool_call_id"] == "call_1" for r in update_cb.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_concurrent_users_on_a_shared_client_stay_isolated(manager, mode):
    """Two users calling the same STDIO server at once each see only their own prompt.

    On a 2026-07-28 session the prompt is routed by the calling task. On a
    handshake-era session it arrives on the shared receive loop with nothing
    to tell the two calls apart, so both are cancelled rather than guessed at.
    """
    client = _stdio_client(manager, "elicitation_demo", ELICITATION_DEMO, mode)
    manager.clients["elicitation_demo"] = client
    alice = _Answering("alice@example.com", {"What's your name?": "Alice"})
    bob = _Answering("bob@example.com", {"What's your name?": "Bob"})

    async with client:
        alice_text, bob_text = await asyncio.gather(
            _call(manager, "elicitation_demo", "get_user_name", "call_a", alice, "alice@example.com"),
            _call(manager, "elicitation_demo", "get_user_name", "call_b", bob, "bob@example.com"),
        )

    if mode == "auto":
        assert alice_text == "Hello, Alice! Nice to meet you."
        assert bob_text == "Hello, Bob! Nice to meet you."
        assert [r["tool_call_id"] for r in alice.requests] == ["call_a"]
        assert [r["tool_call_id"] for r in bob.requests] == ["call_b"]
    else:
        assert alice_text == bob_text == "Operation cancelled."
        assert alice.requests == [] and bob.requests == []


@pytest.mark.asyncio
async def test_other_user_cannot_answer_a_round_trip_elicitation(manager):
    """The ownership binding holds for elicitations raised by a round trip."""
    client = _stdio_client(manager, "elicitation_demo", ELICITATION_DEMO, "auto")
    manager.clients["elicitation_demo"] = client
    rejected: List[bool] = []

    async def update_cb(event: Dict[str, Any]) -> None:
        if event.get("type") != "elicitation_request":
            return
        elicitations = get_elicitation_manager()
        rejected.append(
            elicitations.handle_elicitation_response(
                elicitation_id=event["elicitation_id"],
                action="accept",
                data="Mallory",
                user_email="mallory@example.com",
            )
        )
        assert elicitations.handle_elicitation_response(
            elicitation_id=event["elicitation_id"],
            action="accept",
            data="Alice",
            user_email="alice@example.com",
        )

    async with client:
        text = await _call(
            manager, "elicitation_demo", "get_user_name", "call_1", update_cb, "alice@example.com"
        )

    assert rejected == [False]
    assert text == "Hello, Alice! Nice to meet you."


@pytest.mark.asyncio
async def test_round_trip_without_a_routing_context_is_cancelled(manager):
    """A call made outside _use_elicitation_context cannot prompt anyone."""
    client = _stdio_client(manager, "elicitation_demo", ELICITATION_DEMO, "auto")
    manager.clients["elicitation_demo"] = client
    bystander = _Answering("bob@example.com", {"What's your name?": "Bob"})
    other_call = ToolCall(id="call_b", name="elicitation_demo_get_user_name", arguments={})

    async with client:
        # Bob has a live entry for this server, registered from another task.
        async def hold_entry(ready: asyncio.Event, release: asyncio.Event) -> None:
            async with manager._use_elicitation_context(
                "elicitation_demo", other_call, bystander, "bob@example.com"
            ):
                ready.set()
                await release.wait()

        ready, release = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(hold_entry(ready, release))
        await ready.wait()
        try:
            result = await manager.call_tool("elicitation_demo", "get_user_name", {})
        finally:
            release.set()
            await holder

    assert result.content[0].text == "Operation cancelled."
    assert bystander.requests == []


@pytest.mark.asyncio
async def test_background_task_result_is_returned(manager):
    """A tool the server runs as a task comes back as an ordinary result."""
    client = _stdio_client(manager, "task_demo", TASK_DEMO, "auto")
    manager.clients["task_demo"] = client
    manager._task_timeout = 0.2
    events: List[Dict[str, Any]] = []

    async def update_cb(event: Dict[str, Any]) -> None:
        events.append(event)

    async with client:
        result = await manager.call_tool(
            "task_demo",
            "data_pipeline",
            {"records": 50, "batch_size": 25},
            meta={"tool_call_id": "call_1"},
            update_cb=update_cb,
        )

    assert result.is_error is False
    assert "50" in result.content[0].text
    # Two 0.5s batches outlast the 0.2s threshold, so the UI is told.
    assert [e["type"] for e in events] == ["tool_task_started", "tool_task_completed"]
    assert events[0]["tool_call_id"] == "call_1"


@pytest.mark.asyncio
async def test_cancelling_the_call_cancels_the_server_task(manager):
    """Stopping a run must not leave its background task executing."""
    from fastmcp_tasks.client_models import ClientGetTaskResult, GetTaskRequest, GetTaskRequestParams

    from atlas.modules.mcp_tools.mcp_task_extension import TaskCallTracker, track_task_call

    client = _stdio_client(manager, "task_demo", TASK_DEMO, "auto")

    async with client:
        tracker = TaskCallTracker()

        async def run() -> Any:
            with track_task_call(tracker):
                return await client.call_tool("data_pipeline", {"records": 500, "batch_size": 5})

        call = asyncio.create_task(run())
        await asyncio.wait_for(tracker.created.wait(), timeout=10)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call

        # Cancellation is cooperative, so give the server a moment to settle.
        async with asyncio.timeout(10):
            while True:
                status = await client.session.send_request(
                    GetTaskRequest(params=GetTaskRequestParams(task_id=tracker.task_id)),
                    ClientGetTaskResult,
                )
                if status.status != "working":
                    break
                await asyncio.sleep(0.05)
        assert status.status == "cancelled"
