"""
Tests for graceful handling of a websocket that closes mid-turn.

Background: a blocking tokenizer download inside LiteLLM stalled the single
shared event loop long enough for uvicorn's websocket keepalive to close live
connections.  Two latent defects turned that stall into a much worse incident:

1. ``websocket_update_callback`` sent unconditionally, so every producer in the
   chat pipeline (file ingest, canvas updates, tool notifications) raised once
   the socket was gone -- spamming tracebacks and aborting file ingest partway.
2. The background chat task was never cancelled on disconnect, so it kept
   streaming tokens and holding MCP sessions against a torn-down session.

These tests pin both behaviors, plus the LiteLLM setting that prevents the
blocking download in the first place.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from main import cleanup_disconnected_session, websocket_update_callback
from starlette.websockets import WebSocketDisconnect, WebSocketState

from atlas.infrastructure.transport.websocket_connection_adapter import (
    WebSocketConnectionAdapter,
)


def _fake_websocket(state=WebSocketState.CONNECTED, send_side_effect=None):
    """Minimal websocket double exposing client_state and send_json."""
    ws = MagicMock()
    ws.client_state = state
    ws.send_json = AsyncMock(side_effect=send_side_effect)
    return ws


# --- websocket_update_callback ------------------------------------------------


@pytest.mark.asyncio
async def test_update_callback_sends_when_connected():
    ws = _fake_websocket()
    message = {"type": "intermediate_update", "update_type": "files_update"}

    await websocket_update_callback(ws, message)

    ws.send_json.assert_awaited_once_with(message)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [WebSocketState.DISCONNECTED, WebSocketState.CONNECTING],
)
async def test_update_callback_drops_when_not_connected(state):
    """No send is attempted at all once the socket has left CONNECTED."""
    ws = _fake_websocket(state=state)

    await websocket_update_callback(ws, {"type": "canvas_content", "content": "x"})

    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_callback_swallows_disconnect_race():
    """The socket can die between the state check and the send."""
    ws = _fake_websocket(send_side_effect=WebSocketDisconnect(code=1006))

    await websocket_update_callback(ws, {"type": "tool_start"})

    ws.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_callback_swallows_send_after_close():
    """Starlette raises RuntimeError when the server already sent its close."""
    ws = _fake_websocket(
        send_side_effect=RuntimeError('Cannot call "send" once a close message has been sent.')
    )

    await websocket_update_callback(ws, {"type": "tool_start"})

    ws.send_json.assert_awaited_once()


# --- WebSocketConnectionAdapter ----------------------------------------------
#
# The adapter is a second, independent send path: the event publisher reaches
# the client through it rather than through websocket_update_callback, so it
# needs the same guard.  An end-to-end disconnect test caught this after the
# callback alone had been fixed.


@pytest.mark.asyncio
async def test_adapter_sends_when_connected():
    ws = _fake_websocket()
    adapter = WebSocketConnectionAdapter(ws, "alice@example.com")

    await adapter.send_json({"type": "warning", "message": "hi"})

    ws.send_json.assert_awaited_once_with({"type": "warning", "message": "hi"})


@pytest.mark.asyncio
async def test_adapter_drops_when_not_connected():
    ws = _fake_websocket(state=WebSocketState.DISCONNECTED)
    adapter = WebSocketConnectionAdapter(ws, "alice@example.com")

    await adapter.send_json({"type": "warning", "message": "hi"})

    ws.send_json.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        WebSocketDisconnect(code=1006),
        RuntimeError('Cannot call "send" once a close message has been sent.'),
        # Raised by uvicorn when the endpoint already returned and the ASGI
        # response is complete -- the socket object still looks connected.
        RuntimeError(
            "Unexpected ASGI message 'websocket.send', after sending "
            "'websocket.close' or response already completed."
        ),
    ],
)
async def test_adapter_swallows_closed_socket_errors(error):
    ws = _fake_websocket(send_side_effect=error)
    adapter = WebSocketConnectionAdapter(ws, "alice@example.com")

    await adapter.send_json({"type": "warning", "message": "hi"})

    ws.send_json.assert_awaited_once()


# --- disconnect cancels the in-flight turn ------------------------------------


def _chat_service_double():
    service = MagicMock()
    service.end_session = AsyncMock()
    service.session_repository.get = AsyncMock(return_value=None)
    return service


@pytest.mark.asyncio
async def test_disconnect_cancels_in_flight_turn():
    """A closed socket must stop the turn, not leave it running.

    Otherwise the background chat task keeps streaming tokens, executing tools
    and holding MCP sessions against a session that end_session() has already
    torn down.
    """
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def never_ending():
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(never_ending())
    await started.wait()

    service = _chat_service_double()
    await cleanup_disconnected_session(
        service, "session-1", "alice@example.com", {"task": task}
    )

    await asyncio.wait_for(cancelled.wait(), timeout=1)
    assert task.cancelled()
    service.end_session.assert_awaited_once_with("session-1")


@pytest.mark.asyncio
async def test_disconnect_cancels_before_tearing_down_the_session():
    """Ordering matters: request cancellation, *then* release resources.

    Delivery of the CancelledError happens on a later loop tick, so what is
    asserted here is that the request precedes teardown -- tearing the session
    down first is what leaves the turn writing into a dead session.
    """
    observed = {}

    async def never_ending():
        await asyncio.sleep(30)

    task = asyncio.create_task(never_ending())
    await asyncio.sleep(0)  # let the task reach its first await

    def record_end_session(*args, **kwargs):
        observed["cancel_requested"] = task.cancelling() > 0

    service = _chat_service_double()
    service.end_session = AsyncMock(side_effect=record_end_session)

    await cleanup_disconnected_session(
        service, "session-1", "alice@example.com", {"task": task}
    )

    assert observed.get("cancel_requested") is True, (
        "session was torn down before the in-flight turn was cancelled"
    )


@pytest.mark.asyncio
async def test_disconnect_cleanup_tolerates_no_active_turn():
    """Idle sessions (no chat running) must still be torn down cleanly."""
    service = _chat_service_double()

    await cleanup_disconnected_session(
        service, "session-1", "alice@example.com", {"task": None}
    )

    service.end_session.assert_awaited_once_with("session-1")


@pytest.mark.asyncio
async def test_disconnect_cleanup_leaves_finished_turn_alone():
    """A turn that already completed must not be re-cancelled."""

    async def already_done():
        return "done"

    task = asyncio.create_task(already_done())
    await task

    service = _chat_service_double()
    await cleanup_disconnected_session(
        service, "session-1", "alice@example.com", {"task": task}
    )

    assert not task.cancelled()
    assert task.result() == "done"
    service.end_session.assert_awaited_once_with("session-1")


# --- LiteLLM must not reach out to huggingface.co -----------------------------


def test_hf_tokenizer_download_disabled():
    """Importing the caller must opt out of LiteLLM's blocking HF download.

    LiteLLM reconstructs token counts locally when a streamed response carries
    no usage block; for llama-family model names that path calls a blocking
    Tokenizer.from_pretrained() from inside the event loop, stalling the whole
    server in a network-restricted deployment.  It already falls back to
    tiktoken on failure, so opting out does not change token counts.
    """
    import litellm

    import atlas.modules.llm.litellm_caller  # noqa: F401  (import for side effect)

    assert litellm.disable_hf_tokenizer_download is True
