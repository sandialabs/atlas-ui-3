"""An in-loop ASGI WebSocket client whose every wait has a real deadline.

Starlette's ``TestClient`` runs the app on a second thread and its
``receive_json()`` blocks the test thread until the app sends a frame or
exits. If the app does neither (it is waiting for the client's next frame, or
it is stuck) the test blocks forever and nothing can interrupt it: a
``time.monotonic()`` check between two ``receive_json()`` calls never runs
(issue #1203).

This client runs the ASGI app as a task on the test's own event loop. Every
wait races the app's next frame against the app's exit and a deadline, so a
frame that never arrives becomes a :class:`WebSocketDeadlineError` naming what
was awaited, the frames seen so far and where the app is suspended, and the
app task is then cancelled.

Three outcomes stay distinct, so one can never pass for another:

* the app raised -- :meth:`AsgiWebSocketSession.run_until_app_exits` returns
  the exception object itself, for the test to compare by identity;
* the app returned or closed the socket -- the same call returns ``None``;
* nothing happened in time -- :class:`WebSocketDeadlineError`, an
  ``AssertionError``, which no ``pytest.raises(RuntimeError)`` can swallow.

An app exception the test never asked for is re-raised when the session
closes, as ``TestClient`` does, so an application error cannot go unnoticed.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping, Optional

DEFAULT_DEADLINE_SECONDS = 15.0
# How long a cancelled app gets to unwind before the session gives up on it.
_CANCEL_GRACE_SECONDS = 2.0


class WebSocketDeadlineError(AssertionError):
    """The app neither sent the awaited frame nor exited before the deadline."""


class WebSocketClosedError(AssertionError):
    """A frame was awaited and the app closed the socket or exited instead."""

    def __init__(self, message: str, *, code: Optional[int] = None):
        super().__init__(message)
        self.code = code


class AsgiWebSocketSession:
    """One WebSocket connection to an ASGI app, driven on the running loop.

    ``deadline`` bounds the whole session, from connect to close: every wait
    gets whatever is left of it.
    """

    def __init__(
        self,
        app,
        path: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        query_string: str = "",
        deadline: float = DEFAULT_DEADLINE_SECONDS,
    ):
        self._app = app
        self._scope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": path,
            "raw_path": path.encode("ascii"),
            "root_path": "",
            "query_string": query_string.encode("ascii"),
            "headers": [
                (name.lower().encode("latin-1"), value.encode("latin-1"))
                for name, value in {"host": "testserver", **(headers or {})}.items()
            ],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "subprotocols": [],
            "state": {},
        }
        self._deadline_seconds = deadline
        self._deadline_at = 0.0
        self._to_app: asyncio.Queue = asyncio.Queue()
        self._from_app: asyncio.Queue = asyncio.Queue()
        self._app_task: Optional[asyncio.Task] = None
        self._app_failure_reported = False
        self._closed_by_app: Optional[int] = None
        # Every JSON frame the app sent, in order, for failure messages.
        self.frames: list = []

    async def __aenter__(self) -> "AsgiWebSocketSession":
        loop = asyncio.get_running_loop()
        self._deadline_at = loop.time() + self._deadline_seconds
        self._app_task = loop.create_task(
            self._app(self._scope, self._to_app.get, self._from_app.put),
            name=f"asgi-websocket-app {self._scope['path']}",
        )
        await self._to_app.put({"type": "websocket.connect"})
        message = await self._next_message("the handshake to be accepted")
        if message["type"] != "websocket.accept":
            await self._stop_app()
            raise WebSocketClosedError(
                f"the app refused the handshake: {message!r}", code=message.get("code")
            )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        task = self._app_task
        if not task.done():
            await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
            await asyncio.wait({task}, timeout=self._remaining())
        if not task.done():
            where = self._where_the_app_is()
            await self._stop_app()
            if exc is None:
                raise WebSocketDeadlineError(
                    "the app was still running "
                    f"{self._deadline_seconds:g}s after the session opened, after "
                    f"the client disconnected.\n{self._frames_seen()}\n{where}"
                )
            return
        if task.cancelled() or exc is not None or self._app_failure_reported:
            return
        failure = task.exception()
        if failure is not None:
            raise failure

    async def send_json(self, data: Any) -> None:
        await self._to_app.put({"type": "websocket.receive", "text": json.dumps(data)})

    async def receive_json(self) -> Any:
        """The app's next frame. A close, an exit or a stall all raise."""
        message = await self._next_message("a frame")
        return self._decode(message)

    async def receive_until(self, predicate, *, describe: str) -> Any:
        """The first frame ``predicate`` accepts; the others are skipped."""
        while True:
            frame = self._decode(await self._next_message(describe))
            if predicate(frame):
                return frame

    async def run_until_app_exits(self) -> Optional[BaseException]:
        """Collect frames until the app finishes; return what it raised.

        ``None`` means the app returned without raising (a close it sent is in
        :attr:`closed_by_app`). A stall raises :class:`WebSocketDeadlineError`.
        """
        task = self._app_task
        while not task.done():
            getter = asyncio.ensure_future(self._from_app.get())
            try:
                await asyncio.wait(
                    {getter, task}, timeout=self._remaining(), return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                if not getter.done():
                    getter.cancel()
            if getter.done() and not getter.cancelled():
                self._record(getter.result())
            elif not task.done():
                await self._stalled("the app to exit")
        while not self._from_app.empty():
            self._record(self._from_app.get_nowait())
        self._app_failure_reported = True
        if task.cancelled():
            raise WebSocketDeadlineError("the app task was cancelled before it finished")
        return task.exception()

    @property
    def closed_by_app(self) -> Optional[int]:
        """The close code the app sent, or ``None`` if it sent no close frame."""
        return self._closed_by_app

    def _remaining(self) -> float:
        return max(0.0, self._deadline_at - asyncio.get_running_loop().time())

    def _record(self, message: dict) -> None:
        if message["type"] == "websocket.close":
            self._closed_by_app = message.get("code", 1000)
        elif message["type"] == "websocket.send":
            self._decode(message)

    def _decode(self, message: dict) -> Any:
        if message["type"] == "websocket.close":
            self._closed_by_app = message.get("code", 1000)
            raise WebSocketClosedError(
                f"the app closed the socket (code {self._closed_by_app}) while a frame "
                f"was awaited.\n{self._frames_seen()}",
                code=self._closed_by_app,
            )
        text = message.get("text")
        if text is None:
            text = message["bytes"].decode("utf-8")
        frame = json.loads(text)
        self.frames.append(frame)
        return frame

    async def _next_message(self, awaited: str) -> dict:
        task = self._app_task
        if self._from_app.empty() and not task.done():
            getter = asyncio.ensure_future(self._from_app.get())
            try:
                await asyncio.wait(
                    {getter, task}, timeout=self._remaining(), return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                if not getter.done():
                    getter.cancel()
            if getter.done() and not getter.cancelled():
                return getter.result()
        if not self._from_app.empty():
            return self._from_app.get_nowait()
        if not task.done():
            await self._stalled(awaited)
        # The app is gone and sent nothing more. Its own error is the most
        # useful thing to report; a clean exit is still not the awaited frame.
        self._app_failure_reported = True
        failure = None if task.cancelled() else task.exception()
        if failure is not None:
            raise failure
        raise WebSocketClosedError(
            f"the app exited while the test awaited {awaited}.\n{self._frames_seen()}"
        )

    async def _stalled(self, awaited: str) -> None:
        where = self._where_the_app_is()
        await self._stop_app()
        raise WebSocketDeadlineError(
            f"waited {self._deadline_seconds:g}s (the session deadline) for {awaited}, "
            f"which did not happen.\n{self._frames_seen()}\n{where}"
        )

    async def _stop_app(self) -> None:
        task = self._app_task
        self._app_failure_reported = True
        if task is None or task.done():
            return
        task.cancel()
        # Bounded: an app that swallows the cancellation must not turn the
        # timeout back into a hang.
        await asyncio.wait({task}, timeout=_CANCEL_GRACE_SECONDS)

    def _frames_seen(self) -> str:
        types = [f.get("type") if isinstance(f, dict) else type(f).__name__ for f in self.frames]
        return f"Frames received before that ({len(types)}): {types}"

    def _where_the_app_is(self) -> str:
        lines = ["The app task is suspended at (outermost call first):"]
        lines += _await_chain(self._app_task)
        others = [
            t for t in asyncio.all_tasks()
            if t is not self._app_task and t is not asyncio.current_task()
            and not t.done() and not t.cancelling()
        ]
        if others:
            lines.append(f"Other pending tasks ({len(others)}):")
            for task in others:
                chain = _await_chain(task)
                lines.append(f"-- {task.get_name()}:{chain[-1] if chain else ' (not started)'}")
        return "\n".join(lines)


def _await_chain(task: asyncio.Task) -> list:
    """Each coroutine the task is suspended in, from its entry point inwards.

    ``Task.print_stack`` shows a suspended task's outermost frame only on some
    Python versions, which for an ASGI app is always the middleware stack.
    Following the ``await`` chain reaches the line that is actually waiting.
    """
    lines = []
    awaited = task.get_coro()
    while awaited is not None and len(lines) < 60:
        frame = getattr(awaited, "cr_frame", None) or getattr(awaited, "gi_frame", None)
        if frame is None:
            break
        code = frame.f_code
        lines.append(f'  File "{code.co_filename}", line {frame.f_lineno}, in {code.co_name}')
        awaited = getattr(awaited, "cr_await", None) or getattr(awaited, "gi_yieldfrom", None)
    return lines
