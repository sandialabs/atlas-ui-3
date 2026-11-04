"""Virtual users: authentication, WebSocket connections and chat turns.

Two ways to be a user:

- ``header``: the trusted-header mode a reverse proxy provides
  (``X-User-Email``; the local replicas run with ``DEBUG_MODE`` so no proxy
  secret is needed, ``--proxy-secret`` adds one for a remote target).
- ``oidc``: a real Authorization Code + PKCE login through Atlas
  (``/auth/oidc/login`` -> IdP ``/authorize`` -> ``/auth/oidc/callback``),
  ending in Atlas's signed session cookie. The user's cookie jar is shared by
  every replica it talks to, as it would be behind one load-balanced host
  name. Atlas keeps OIDC sessions in process memory, so a socket presented to
  a replica that did not issue the session is refused; the client then logs
  in again on that replica and counts it (``oidc_relogin``) instead of hiding
  it.

Each operation is timed into ``Metrics`` under a fixed name (``ws_connect``,
``chat_turn``, ``chat_first_token``, ``oidc_login``...) so reports from
different runs line up.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urlencode, urlsplit

import httpx
import websockets
import websockets.exceptions

from .metrics import Metrics, classify_error

TURN_DONE = ("conversation_saved",)
RUN_TERMINAL = ("completed", "failed", "cancelled")


class AuthRefused(Exception):
    """The replica refused the socket's credentials (HTTP 401/403 on upgrade)."""


class VirtualUser:
    def __init__(self, index: int, auth: str, metrics: Metrics, proxy_secret: Optional[str] = None,
                 user_prefix: str = "loaduser"):
        self.index = index
        self.auth = auth
        self.metrics = metrics
        self.name = f"{user_prefix}-{index:04d}"
        self.email = f"{self.name}@load.test"
        self.proxy_secret = proxy_secret
        # One cookie jar per user, shared across replicas (one host name).
        self.http = httpx.AsyncClient(timeout=30.0, follow_redirects=False)
        self._logged_in: set = set()
        self._login_lock = asyncio.Lock()

    def headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self.auth == "header":
            headers["X-User-Email"] = self.email
            if self.proxy_secret:
                headers["X-Proxy-Secret"] = self.proxy_secret
        return headers

    def cookie_header(self, base_url: str) -> Optional[str]:
        host = urlsplit(base_url).hostname
        pairs = [f"{c.name}={c.value}" for c in self.http.cookies.jar if host and c.domain.lstrip(".") in (host, "")]
        return "; ".join(pairs) or None

    async def ensure_login(self, base_url: str, force: bool = False) -> None:
        """OIDC only: log in on ``base_url`` unless this user holds a session.

        Without ``force`` an existing cookie is reused whichever replica issued
        it, as a browser behind a load balancer would; the caller logs in again
        (``force=True``) only when a replica refuses it.
        """
        if self.auth != "oidc":
            return
        async with self._login_lock:
            if self._logged_in and not force:
                return
            started = time.monotonic()
            try:
                await self._oidc_login(base_url)
            except Exception as exc:
                self.metrics.record("oidc_login", time.monotonic() - started, classify_error(exc), repr(exc))
                raise
            self.metrics.record("oidc_login", time.monotonic() - started)
            # A new session cookie replaces the old one in the shared jar, so
            # it is now valid only on this replica.
            self._logged_in = {base_url}

    async def _oidc_login(self, base_url: str) -> None:
        start = await self.http.get(base_url + "/auth/oidc/login")
        if start.status_code != 302:
            raise RuntimeError(f"/auth/oidc/login answered {start.status_code}")
        authorize = start.headers["location"] + "&" + urlencode({"login_hint": self.name})
        idp = await self.http.get(authorize)
        if idp.status_code != 302:
            raise RuntimeError(f"IdP /authorize answered {idp.status_code}: {idp.text[:200]}")
        callback = await self.http.get(idp.headers["location"])
        location = callback.headers.get("location", "")
        if callback.status_code != 302 or "oidc_error" in location:
            raise RuntimeError(f"/auth/oidc/callback answered {callback.status_code} -> {location}")

    async def close(self) -> None:
        await self.http.aclose()


class Connection:
    """One chat WebSocket. A reader task routes frames to the waiting turn."""

    def __init__(self, user: VirtualUser, base_url: str, metrics: Metrics):
        self.user = user
        self.base_url = base_url
        self.metrics = metrics
        self.ws = None
        self.session_id: Optional[str] = None
        self._reader: Optional[asyncio.Task] = None
        self._frames: asyncio.Queue = asyncio.Queue()
        self.closed_by_server = asyncio.Event()
        self.had_turn = False
        # Set before the client closes or deliberately loses the socket, so
        # any other close is counted as the server dropping it.
        self.expect_close = False
        # Like the browser: once a turn is saved, later turns on this socket
        # continue that conversation (``conversation_id`` on the chat frame).
        self.conversation_id: Optional[str] = None

    @property
    def open(self) -> bool:
        return self.ws is not None and not self.closed_by_server.is_set()

    def _ws_url(self, resume: bool) -> str:
        scheme = "wss" if self.base_url.startswith("https") else "ws"
        url = scheme + self.base_url[self.base_url.index(":"):] + "/ws"
        # Opt in to session resumption (an empty value on the first connect).
        return url + "?" + urlencode({"resume_session": self.session_id or ""}) if resume else url

    async def connect(self, op: str = "ws_connect", resume: bool = True) -> None:
        """Open the socket, logging in first (and once more if refused) for OIDC."""
        started = time.monotonic()
        try:
            await self.user.ensure_login(self.base_url)
            try:
                await self._open(resume)
            except AuthRefused:
                if self.user.auth != "oidc":
                    raise
                self.metrics.count("oidc_relogin")
                await self.user.ensure_login(self.base_url, force=True)
                await self._open(resume)
        except Exception as exc:
            self.metrics.record(op, time.monotonic() - started, classify_error(exc), repr(exc))
            raise
        self.metrics.record(op, time.monotonic() - started)

    async def _open(self, resume: bool) -> None:
        headers = self.user.headers()
        cookie = self.user.cookie_header(self.base_url)
        if cookie:
            headers["Cookie"] = cookie
        try:
            self.ws = await websockets.connect(self._ws_url(resume), additional_headers=headers,
                                               open_timeout=30, max_size=None, ping_interval=None)
        except websockets.exceptions.InvalidStatus as exc:
            if exc.response.status_code in (401, 403):
                raise AuthRefused(f"HTTP {exc.response.status_code}") from exc
            raise
        self.closed_by_server.clear()
        self.expect_close = False
        self._frames = asyncio.Queue()
        self._reader = asyncio.create_task(self._read())
        if resume:
            frame = await self.next_frame(lambda f: f.get("type") == "connection_session", timeout=15)
            if frame.get("resumed"):
                self.metrics.count("session_resumed")
            elif self.session_id and not self.had_turn:
                # Atlas creates a connection's session lazily, at its first
                # turn: a socket that never chatted has nothing to resume.
                self.metrics.count("session_not_resumed_no_turn")
            elif self.session_id:
                # Usually the old socket's session is still marked active
                # (its teardown has not run yet), so the server starts fresh.
                self.metrics.count("session_not_resumed")
            self.session_id = frame.get("session_id")

    async def _read(self) -> None:
        try:
            async for raw in self.ws:
                try:
                    await self._frames.put(json.loads(raw))
                except ValueError:
                    continue  # not JSON; Atlas only sends JSON frames
        except websockets.exceptions.ConnectionClosed:
            pass  # reported through closed_by_server
        finally:
            if not self.expect_close:
                self.metrics.count("connections_closed_by_server")
            self.closed_by_server.set()
            code = getattr(self.ws, "close_code", None)
            reason = getattr(self.ws, "close_reason", None)
            await self._frames.put({"type": "__closed__", "code": code, "reason": reason})

    async def next_frame(self, predicate, timeout: float) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise asyncio.TimeoutError("no matching frame")
            frame = await asyncio.wait_for(self._frames.get(), remaining)
            if frame.get("type") == "__closed__":
                raise ConnectionError(f"socket closed (code {frame.get('code')}, reason {frame.get('reason')!r})")
            if predicate(frame):
                return frame

    async def chat_turn(self, content: str, model: str, agent: bool, timeout: float,
                        new_conversation: bool = False) -> None:
        """One chat turn; times first token and the whole turn (until saved)."""
        frame = {"type": "chat", "content": content, "model": model, "save_mode": "server"}
        if self.conversation_id and not new_conversation:
            frame["conversation_id"] = self.conversation_id
        self.had_turn = True
        if agent:
            frame.update({"agent_mode": True, "agent_max_steps": 10, "selected_tools": ["atlas_sleep"]})
        started = time.monotonic()
        first_token = None
        seen: list = []  # frame types, for the error sample when a turn fails
        run_id: Optional[str] = None
        run_ended: Optional[str] = None
        saved = False
        try:
            await self.ws.send(json.dumps(frame))
            deadline = started + timeout
            while True:
                got = await self.next_frame(lambda f: True, deadline - time.monotonic())
                kind = got.get("type")
                seen.append(kind)
                if kind == "token_stream" and first_token is None:
                    first_token = time.monotonic() - started
                if kind == "tool_approval_request":
                    # Answer like a user clicking Approve. The request frame
                    # carries the run tags the server needs to route it.
                    reply = {"type": "tool_approval_response", "approved": True,
                             "arguments": got.get("arguments")}
                    for key in ("tool_call_id", "invocation_id", "run_id", "conversation_id"):
                        if got.get(key) is not None:
                            reply[key] = got[key]
                    await self.ws.send(json.dumps(reply))
                    self.metrics.count("tool_approvals_sent")
                    continue
                if kind == "error" and got.get("error_type") == "unknown_tool_approval":
                    # The server's contract: this names one stale approval
                    # card and does not end the turn. Count it and go on.
                    self.metrics.count("approval_unknown")
                    continue
                if kind == "error":
                    raise TurnError(str(got.get("message") or got.get("error") or "error frame"))
                if kind == "run_started" and run_id is None:
                    run_id = got.get("run_id")
                if kind == "run_status":
                    run = got.get("run") or {}
                    if run_id is not None and run.get("run_id") == run_id and run.get("status") in RUN_TERMINAL:
                        run_ended = run["status"]
                if kind in TURN_DONE:
                    self.conversation_id = got.get("conversation_id") or self.conversation_id
                    saved = True
                # A tracked run saves its conversation before the run itself
                # is terminal; the next turn on that conversation is refused
                # until it is, so a turn ends when both have happened.
                if saved and (run_id is None or run_ended):
                    if run_ended not in (None, "completed"):
                        raise TurnError(f"run {run_ended}")
                    break
        except Exception as exc:
            frames = ",".join(seen[-12:])
            self.metrics.record("chat_turn", time.monotonic() - started, classify_error(exc),
                                f"{exc!r} after {len(seen)} frames, last: {frames}")
            raise
        self.metrics.record("chat_turn", time.monotonic() - started)
        if first_token is not None:
            self.metrics.record("chat_first_token", first_token)

    async def drop(self) -> None:
        """Close from the client side without ceremony (a network blip)."""
        self.expect_close = True
        if self.ws is not None:
            transport = getattr(self.ws, "transport", None)
            if transport is not None:
                transport.abort()
            else:
                await self.ws.close()
        await self._stop_reader()

    async def close(self) -> None:
        self.expect_close = True
        if self.ws is not None:
            try:
                await asyncio.wait_for(self.ws.close(), 5)
            except (asyncio.TimeoutError, websockets.exceptions.WebSocketException, OSError):
                pass  # closing a dead socket; nothing to report
        await self._stop_reader()

    async def _stop_reader(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):
                pass  # the reader's own exit; frames already drained
        self._reader = None
        self.ws = None


class TurnError(Exception):
    """Atlas answered a chat turn with an ``error`` frame."""
