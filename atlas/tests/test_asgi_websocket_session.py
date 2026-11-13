"""The bounded WebSocket test client keeps its three outcomes apart (#1203).

A stalled app must fail the test within the deadline, and neither a stall nor
an ordinary disconnect may pass for the app error a test is waiting for.
"""

import asyncio
import time

import pytest

from atlas.tests.asgi_websocket_session import (
    AsgiWebSocketSession,
    WebSocketClosedError,
    WebSocketDeadlineError,
)

SHORT = 0.3
# Generous: the point is "bounded", not "fast" on a loaded CI runner.
BOUND = 10.0


async def _accept(receive, send):
    assert (await receive())["type"] == "websocket.connect"
    await send({"type": "websocket.accept"})


async def _silent_app(scope, receive, send):
    """Accepts, answers one frame, then waits for a frame that never comes."""
    await _accept(receive, send)
    await receive()
    await send({"type": "websocket.send", "text": '{"type": "run_status"}'})
    await receive()


async def _closing_app(scope, receive, send):
    await _accept(receive, send)
    await receive()
    await send({"type": "websocket.close", "code": 1011})


def _raising_app(error):
    async def app(scope, receive, send):
        await _accept(receive, send)
        await receive()
        await send({"type": "websocket.send", "text": '{"type": "run_status"}'})
        raise error

    return app


def _uncancellable_app(release: asyncio.Event):
    """Swallows cancellation until ``release`` is set."""

    async def app(scope, receive, send):
        await _accept(receive, send)
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    return app


async def test_a_stalled_app_fails_within_the_deadline_and_says_where():
    started = time.monotonic()
    with pytest.raises(WebSocketDeadlineError) as caught:
        async with AsgiWebSocketSession(_silent_app, "/ws", deadline=SHORT) as ws:
            await ws.send_json({"type": "chat"})
            await ws.run_until_app_exits()
    assert time.monotonic() - started < BOUND
    message = str(caught.value)
    assert "['run_status']" in message
    assert "_silent_app" in message, "the report names where the app is suspended"


async def test_a_stall_is_not_the_runtime_error_a_test_may_be_waiting_for():
    assert not issubclass(WebSocketDeadlineError, RuntimeError)
    with pytest.raises(WebSocketDeadlineError):
        with pytest.raises(RuntimeError):
            async with AsgiWebSocketSession(_silent_app, "/ws", deadline=SHORT) as ws:
                await ws.send_json({"type": "chat"})
                await ws.receive_json()
                await ws.receive_json()


async def test_an_app_error_is_returned_as_the_same_object():
    injected = RuntimeError("socket gone")
    async with AsgiWebSocketSession(_raising_app(injected), "/ws", deadline=BOUND) as ws:
        await ws.send_json({"type": "chat"})
        failure = await ws.run_until_app_exits()
    assert failure is injected
    assert [f["type"] for f in ws.frames] == ["run_status"]
    assert ws.closed_by_app is None


async def test_a_disconnect_is_not_an_app_error():
    async with AsgiWebSocketSession(_closing_app, "/ws", deadline=BOUND) as ws:
        await ws.send_json({"type": "chat"})
        failure = await ws.run_until_app_exits()
    assert failure is None
    assert ws.closed_by_app == 1011


async def test_a_close_while_a_frame_is_awaited_raises_closed_not_deadline():
    with pytest.raises(WebSocketClosedError) as caught:
        async with AsgiWebSocketSession(_closing_app, "/ws", deadline=BOUND) as ws:
            await ws.send_json({"type": "chat"})
            await ws.receive_json()
    assert caught.value.code == 1011


async def test_an_app_error_nobody_asked_about_is_raised_when_the_session_closes():
    injected = ValueError("application bug")
    with pytest.raises(ValueError) as caught:
        async with AsgiWebSocketSession(_raising_app(injected), "/ws", deadline=BOUND) as ws:
            await ws.send_json({"type": "chat"})
            assert (await ws.receive_json())["type"] == "run_status"
    assert caught.value is injected


async def test_an_app_error_surfaces_from_receive_instead_of_a_stall():
    injected = ValueError("application bug")
    started = time.monotonic()
    with pytest.raises(ValueError) as caught:
        async with AsgiWebSocketSession(_raising_app(injected), "/ws", deadline=BOUND) as ws:
            await ws.send_json({"type": "chat"})
            await ws.receive_until(lambda f: f["type"] == "never", describe="a frame nobody sends")
    assert caught.value is injected
    assert time.monotonic() - started < BOUND


async def test_an_app_that_ignores_cancellation_cannot_hang_the_test(monkeypatch):
    monkeypatch.setattr("atlas.tests.asgi_websocket_session._CANCEL_GRACE_SECONDS", SHORT)
    release = asyncio.Event()
    started = time.monotonic()
    session = AsgiWebSocketSession(_uncancellable_app(release), "/ws", deadline=SHORT)
    try:
        with pytest.raises(WebSocketDeadlineError):
            async with session as ws:
                await ws.receive_json()
        assert time.monotonic() - started < BOUND
        assert not session._app_task.done(), "the app really did resist the cancellation"
    finally:
        # Leave nothing behind for the loop's own teardown to wait on.
        release.set()
        await asyncio.wait({session._app_task}, timeout=BOUND)


async def test_an_app_still_running_after_the_client_disconnects_is_reported():
    async def lingering_app(scope, receive, send):
        await _accept(receive, send)
        await asyncio.sleep(3600)

    with pytest.raises(WebSocketDeadlineError) as caught:
        async with AsgiWebSocketSession(lingering_app, "/ws", deadline=SHORT):
            pass
    assert "after the client disconnected" in str(caught.value)


async def test_an_app_error_is_noted_on_a_test_that_was_already_failing():
    injected = ValueError("application bug")
    with pytest.raises(AssertionError) as caught:
        async with AsgiWebSocketSession(_raising_app(injected), "/ws", deadline=BOUND) as ws:
            await ws.send_json({"type": "chat"})
            await asyncio.sleep(0.05)  # let the app fail before the body does
            raise AssertionError("the body's own failure")
    assert str(caught.value) == "the body's own failure"
    assert any("application bug" in note for note in caught.value.__notes__)


def test_every_backend_test_runs_under_the_suite_timeout(request):
    """The backstop for waits this client does not cover (conftest.py)."""
    from conftest import (  # the suite's own conftest
        BACKEND_TEST_TIMEOUT_SECONDS,
        default_timeout_is_overridden,
    )

    assert request.config.pluginmanager.hasplugin("timeout"), (
        "pytest-timeout is missing; reinstall the dev extra (uv pip install -e '.[dev]')"
    )
    if default_timeout_is_overridden(request.config):
        pytest.skip("a timeout was chosen for this run, which replaces the default")
    marker = request.node.get_closest_marker("timeout")
    assert marker is not None and marker.args == (BACKEND_TEST_TIMEOUT_SECONDS,)
    # Under the 300s the suite as a whole is given by test/atlas_tests.sh.
    assert 0 < BACKEND_TEST_TIMEOUT_SECONDS <= 120


class _Config:
    def __init__(self, option=None, ini=""):
        self._option, self._ini = option, ini

    def getoption(self, name, default=None):
        return self._option

    def getini(self, name):
        return self._ini


@pytest.mark.parametrize(
    "config, environ, overridden",
    [
        (_Config(), {}, False),
        (_Config(option=0.0), {}, True),
        (_Config(option=600.0), {}, True),
        (_Config(), {"PYTEST_TIMEOUT": "0"}, True),
        (_Config(), {"PYTEST_TIMEOUT": ""}, False),
        (_Config(ini="300"), {}, True),
    ],
    ids=["nothing-chosen", "cli-zero", "cli-value", "env-zero", "env-empty", "ini"],
)
def test_a_timeout_chosen_for_the_run_replaces_the_default(config, environ, overridden):
    from conftest import default_timeout_is_overridden  # the suite's own conftest

    assert default_timeout_is_overridden(config, environ) is overridden
