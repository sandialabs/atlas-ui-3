#!/usr/bin/env python3
"""Check a multi-replica Atlas deployment, one replica at a time (issue #1129).

Each ``--replica`` URL must reach exactly one API process (staging.sh
port-forwards to each pod; the PR validation script starts local processes).
Requests go to a chosen replica, never through a load balancer, so every
cross-replica claim below is tested rather than left to chance.

Checks:

1. health      every replica reports healthy, chat history "ok" and the
               Redis session store "ok".
2. auth        every replica rejects requests without the proxy secret or
               with a wrong one, and accepts a correctly proxied user.
3. durable     a workspace created on one replica is visible on every other
               replica and deleted through another (shared PostgreSQL).
4. reconnect   a WebSocket session opened on replica 1 is resumed on
               replica 2, then on replica 3, ... (shared Redis, no sticky
               sessions), and another user cannot resume it.

The caller plays the trusted reverse proxy: it sends the user header and the
proxy secret itself. Never point this at a deployment that is reachable
without the proxy secret.

Subcommands for failover drills: ``hold-session`` opens a session on one
replica, prints its id and keeps the socket open until the server closes it
(terminate that replica meanwhile); ``resume-session`` then resumes the id on
another replica. ``open-session`` opens and closes a session.

Needs httpx and websockets (both Atlas dependencies).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from typing import Callable, Dict, List, Optional, Tuple

import httpx
from websockets.exceptions import ConnectionClosed, InvalidStatus, WebSocketException
from websockets.sync.client import connect

USER_HEADER = os.environ.get("ATLAS_AUTH_USER_HEADER", "X-User-Email")
SECRET_HEADER = os.environ.get("ATLAS_PROXY_SECRET_HEADER", "X-Proxy-Secret")


class CheckFailed(AssertionError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


class Replica:
    def __init__(self, label: str, url: str):
        self.label = label
        self.url = url.rstrip("/")

    @property
    def ws_url(self) -> str:
        if self.url.startswith("https://"):
            return "wss://" + self.url[len("https://"):]
        return "ws://" + self.url[len("http://"):]


class Validator:
    def __init__(self, replicas: List[Replica], proxy_secret: str, timeout: float = 15.0):
        self.replicas = replicas
        self.proxy_secret = proxy_secret
        self.timeout = timeout
        self.results: List[Tuple[str, bool, str]] = []

    # -- helpers ---------------------------------------------------------------

    def headers(self, user: Optional[str], secret: Optional[str] = "") -> Dict[str, str]:
        headers = {}
        if user:
            headers[USER_HEADER] = user
        secret = self.proxy_secret if secret == "" else secret
        if secret:
            headers[SECRET_HEADER] = secret
        return headers

    def request(self, replica: Replica, method: str, path: str, *, user: Optional[str] = None,
                secret: Optional[str] = "", body: object = None) -> Tuple[int, object]:
        response = httpx.request(
            method, replica.url + path, headers=self.headers(user, secret), json=body,
            timeout=self.timeout, follow_redirects=False,
        )
        try:
            return response.status_code, response.json()
        except json.JSONDecodeError:
            return response.status_code, response.text

    def _wait_for(self, ws, frame_type: str, label: str) -> dict:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            frame = json.loads(ws.recv(timeout=max(0.1, deadline - time.monotonic())))
            if frame.get("type") == frame_type:
                return frame
        raise CheckFailed(f"{label}: no {frame_type} frame within {self.timeout}s")

    def connection_session(self, replica: Replica, user: Optional[str], resume: str = "",
                           secret: Optional[str] = "", new_chat: bool = False) -> dict:
        """Open /ws naming ``resume`` and return the server's connection_session frame.

        ``new_chat`` also starts a conversation (the UI's New Chat), which is
        what first writes the session to the store: a connection that never
        did anything has no session to resume.
        """
        url = f"{replica.ws_url}/ws?resume_session={resume}"
        with connect(url, additional_headers=self.headers(user, secret), open_timeout=self.timeout,
                     close_timeout=5) as ws:
            frame = self._wait_for(ws, "connection_session", replica.label)
            if new_chat:
                ws.send(json.dumps({"type": "reset_session"}))
                reset = self._wait_for(ws, "session_reset", replica.label)
                require(reset.get("session_id") == frame.get("session_id"),
                        f"{replica.label}: New Chat answered for another session: {reset}")
            return frame

    def open_session(self, replica: Replica, user: str) -> str:
        frame = self.connection_session(replica, user, new_chat=True)
        require(frame.get("resumed") is False, f"{replica.label}: a new connection reported resumed: {frame}")
        session_id = frame.get("session_id")
        require(bool(session_id), f"{replica.label}: connection_session without session_id: {frame}")
        return session_id

    def hold_session(self, replica: Replica, user: str) -> None:
        """Open a session, print its id, and hold the socket until the server closes it.

        For failover drills: terminate the replica while this runs, then
        resume the printed session on another replica.
        """
        url = f"{replica.ws_url}/ws?resume_session="
        with connect(url, additional_headers=self.headers(user), open_timeout=self.timeout,
                     close_timeout=5) as ws:
            frame = self._wait_for(ws, "connection_session", replica.label)
            ws.send(json.dumps({"type": "reset_session"}))
            self._wait_for(ws, "session_reset", replica.label)
            print(frame["session_id"], flush=True)
            started = time.monotonic()
            try:
                while True:
                    ws.recv()
            except ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd else "none (no close frame)"
                print(f"closed by server after {time.monotonic() - started:.1f}s, code {code}", flush=True)

    def resume_session(self, replica: Replica, user: str, session_id: str, wait: float = 20.0) -> bool:
        """True once ``replica`` resumes ``session_id``.

        The previous connection marks the session inactive while it tears
        down; until then a reconnect deliberately starts fresh, so retry.
        """
        deadline = time.monotonic() + wait
        while True:
            frame = self.connection_session(replica, user, resume=session_id)
            if frame.get("resumed"):
                require(frame.get("session_id") == session_id,
                        f"{replica.label}: resumed a different session: {frame}")
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

    def check(self, name: str, fn: Callable[[], str]) -> None:
        try:
            detail = fn()
            self.results.append((name, True, detail))
            print(f"PASS  {name}: {detail}", flush=True)
        except (CheckFailed, httpx.HTTPError, WebSocketException, OSError, TimeoutError) as exc:
            self.results.append((name, False, str(exc)))
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}", flush=True)

    # -- checks ----------------------------------------------------------------

    def check_health(self, replica: Replica) -> str:
        status, report = self.request(replica, "GET", "/api/health", secret=None)
        require(status == 200, f"/api/health returned {status}: {report}")
        require(isinstance(report, dict), f"/api/health returned {report!r}")
        require(report.get("status") == "healthy", f"status is {report.get('status')!r}: {report}")
        require(report.get("checks", {}).get("chat_history") == "ok", f"chat history: {report.get('checks')}")
        store = report.get("session_store", {})
        require(store.get("backend") == "redis" and store.get("status") == "ok", f"session store: {store}")
        return f"healthy, version {report.get('version')}, redis {store.get('latency_ms')} ms"

    def check_auth(self, replica: Replica, user: str) -> str:
        status, _ = self.request(replica, "GET", "/api/config", user=user, secret=None)
        require(status in (401, 403), f"no proxy secret: expected 401/403, got {status}")
        status, _ = self.request(replica, "GET", "/api/config", user=user, secret="wrong-" + uuid.uuid4().hex)
        require(status in (401, 403), f"wrong proxy secret: expected 401/403, got {status}")
        status, body = self.request(replica, "GET", "/api/config", user=user)
        require(status == 200, f"proxied user: expected 200, got {status}: {str(body)[:300]}")
        try:
            self.connection_session(replica, user, secret=None)
        except InvalidStatus:
            pass
        except WebSocketException:
            pass  # rejected during or right after the handshake
        else:
            raise CheckFailed("WebSocket without the proxy secret was accepted")
        return "rejects missing/wrong proxy secret (HTTP and WebSocket), accepts proxied user"

    def check_durable(self, user: str) -> str:
        first, others = self.replicas[0], self.replicas[1:]
        name = f"k8s-validate-{uuid.uuid4().hex[:8]}"
        status, body = self.request(first, "POST", "/api/workspaces", user=user, body={
            "name": name, "description": "issue #1129 validation", "config": {},
        })
        require(status == 200, f"create on {first.label}: {status} {body}")
        workspace_id = body["workspace"]["id"]
        try:
            for replica in others:
                status, body = self.request(replica, "GET", "/api/workspaces", user=user)
                require(status == 200, f"list on {replica.label}: {status} {body}")
                names = [w.get("name") for w in body.get("workspaces", [])]
                require(name in names, f"{replica.label} does not see the workspace created on {first.label}")
        finally:
            last = self.replicas[-1]
            status, body = self.request(last, "DELETE", f"/api/workspaces/{workspace_id}", user=user)
        require(status == 200, f"delete on {last.label}: {status} {body}")
        status, body = self.request(first, "GET", "/api/workspaces", user=user)
        require(name not in [w.get("name") for w in body.get("workspaces", [])],
                f"{first.label} still lists the workspace deleted on {last.label}")
        return f"created on {first.label}, seen on {', '.join(r.label for r in others)}, deleted on {last.label}"

    def check_reconnect(self, user: str) -> str:
        session_id = self.open_session(self.replicas[0], user)
        hops = [self.replicas[0].label]
        for replica in self.replicas[1:]:
            require(self.resume_session(replica, user, session_id),
                    f"{replica.label} did not resume session {session_id} (last on {hops[-1]})")
            hops.append(replica.label)
        intruder = f"intruder-{uuid.uuid4().hex[:8]}@example.com"
        frame = self.connection_session(self.replicas[0], intruder, resume=session_id)
        require(frame.get("resumed") is False and frame.get("session_id") != session_id,
                f"another user resumed the session: {frame}")
        return f"session {session_id[:8]} resumed {' -> '.join(hops)}; other user refused"

    def run(self, user: str) -> bool:
        for replica in self.replicas:
            self.check(f"health[{replica.label}]", lambda r=replica: self.check_health(r))
        for replica in self.replicas:
            self.check(f"auth[{replica.label}]", lambda r=replica: self.check_auth(r, user))
        if len(self.replicas) < 2:
            print("FAIL  cross-replica checks need at least two --replica URLs", flush=True)
            return False
        self.check("durable-state", lambda: self.check_durable(user))
        self.check("session-reconnect", lambda: self.check_reconnect(user))
        failed = [name for name, ok, _ in self.results if not ok]
        print(f"\n{len(self.results) - len(failed)} passed, {len(failed)} failed", flush=True)
        return not failed


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a multi-replica Atlas deployment, replica by replica.")
    parser.add_argument("command", nargs="?", default="all", choices=("all", "open-session", "hold-session", "resume-session"))
    parser.add_argument("--replica", action="append", default=[], metavar="[LABEL=]URL",
                        help="Base URL of one API replica (repeat; at least two for 'all')")
    parser.add_argument("--user", default=None, help="User to act as (default: a fresh validation user)")
    parser.add_argument("--session-id", help="Session to resume (resume-session)")
    parser.add_argument("--wait", type=float, default=20.0, help="Seconds to keep retrying a resume")
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    secret = os.environ.get("ATLAS_PROXY_SECRET")
    if not secret:
        print("ATLAS_PROXY_SECRET must hold the deployment's PROXY_SECRET", file=sys.stderr)
        return 2
    replicas = []
    for index, spec in enumerate(args.replica, start=1):
        label, sep, url = spec.partition("=")
        replicas.append(Replica(label if sep else f"replica-{index}", url if sep else spec))
    if not replicas:
        print("at least one --replica is required", file=sys.stderr)
        return 2
    user = args.user or f"k8s-validate-{uuid.uuid4().hex[:10]}@example.com"
    validator = Validator(replicas, secret, timeout=args.timeout)
    if args.command == "open-session":
        print(validator.open_session(replicas[0], user))
        return 0
    if args.command == "hold-session":
        validator.hold_session(replicas[0], user)
        return 0
    if args.command == "resume-session":
        if not args.session_id:
            print("--session-id is required", file=sys.stderr)
            return 2
        ok = validator.resume_session(replicas[0], user, args.session_id, wait=args.wait)
        print(("resumed " if ok else "NOT resumed ") + args.session_id)
        return 0 if ok else 1
    return 0 if validator.run(user) else 1


if __name__ == "__main__":
    sys.exit(main())
