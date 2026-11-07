"""A chat WebSocket to one replica, as a browser tab attached to it.

Synchronous (``websockets.sync``) so tests stay plain pytest functions. Every
frame received is kept in ``frames``; ``recv_until`` unwraps the
``background_activity`` envelope a replica uses for frames of a conversation
the tab is not displaying, so tests match on the inner frame either way.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from websockets.sync.client import connect

from .replica import Replica

Frame = Dict[str, Any]


def chat_frame(content: str, tools: List[str], *, conversation_id: Optional[str] = None,
               request_id: Optional[str] = None) -> Frame:
    """An agent-mode chat turn, the frame the UI sends."""
    frame: Frame = {
        "type": "chat", "content": content, "model": "scripted-mock", "save_mode": "server",
        "agent_mode": True, "agent_max_steps": 8, "selected_tools": tools,
    }
    if conversation_id:
        frame["conversation_id"] = conversation_id
    if request_id:
        frame["request_id"] = request_id
    return frame


class WsClient:
    """``auto_approve`` names tools this tab approves as soon as asked, the way
    the UI's auto-approve setting does (the built-in ``atlas_sleep`` asks)."""

    def __init__(self, replica: Replica, user: str, timeout: float = 15.0, auto_approve=()):
        self.replica = replica
        self.user = user
        self.auto_approve = set(auto_approve)
        self.frames: List[Frame] = []
        self._ws = connect(
            f"ws://127.0.0.1:{replica.port}/ws",
            additional_headers={"X-User-Email": user},
            open_timeout=timeout,
            max_size=None,
        )

    def send(self, frame: Frame) -> None:
        self._ws.send(json.dumps(frame))

    def recv_until(self, pred: Callable[[Frame], bool], timeout: float = 30.0) -> Frame:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                tail = [f.get("type") for f in self.frames[-15:]]
                raise AssertionError(f"no matching frame from {self.replica.name} in {timeout}s; last: {tail}")
            try:
                raw = self._ws.recv(timeout=remaining)
            except TimeoutError:
                continue
            frame = json.loads(raw)
            if frame.get("type") == "background_activity" and isinstance(frame.get("frame"), dict):
                frame = frame["frame"]
            self.frames.append(frame)
            if frame.get("type") == "tool_approval_request" and frame.get("tool_name") in self.auto_approve:
                self.send({
                    "type": "tool_approval_response", "approved": True,
                    "arguments": frame.get("arguments"),
                    **{k: frame.get(k) for k in ("tool_call_id", "invocation_id", "run_id", "conversation_id")},
                })
            if pred(frame):
                return frame

    def close(self) -> None:
        try:
            self._ws.close()
        except Exception:
            pass  # the replica may already be gone

    def __enter__(self) -> "WsClient":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
