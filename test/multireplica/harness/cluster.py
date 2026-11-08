"""A set of replicas plus the client helpers tests drive them with."""

from __future__ import annotations

import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .infra import SharedInfra
from .replica import ADMIN_USER, MockLLM, Replica, free_port, replica_env, write_config
from .side_effect_keys import log_name, read_records

TERMINAL = ("completed", "failed", "cancelled")
WORKSPACE = "Multireplica harness"
RUN_CAP = 5  # MAX_CONCURRENT_RUNS_PER_USER on every replica (replica_env)
LEASE_TTL = 6.0  # RUN_LEASE_SECONDS on every replica (replica_env)


class GapNotClosed(AssertionError):
    """A #1128 acceptance criterion that the code does not meet yet.

    Gap tests are ``xfail(strict=True, raises=GapNotClosed)``: only this
    exception counts as the expected failure. A harness error or a broken
    precondition still fails the test, so a gap test cannot pass by accident,
    and once the gap is closed the strict XPASS forces removing the marker.
    """


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise GapNotClosed(message)


class Cluster:
    def __init__(self, workdir: Path, infra: SharedInfra, mock_llm: MockLLM, size: int = 2,
                 overrides: Optional[Dict[str, str]] = None):
        self.workdir = workdir
        self.infra = infra
        self.mock_llm = mock_llm
        self.config_dir = workdir / "config"
        self.side_effect_dir = workdir / "side_effects"
        self.side_effect_dir.mkdir(parents=True, exist_ok=True)
        write_config(self.config_dir, mock_llm.url)
        self.api_key = secrets.token_urlsafe(48)
        shared_secrets = {
            "AGENT_RUNS_API_KEY": self.api_key,
            "MCP_TOKEN_ENCRYPTION_KEY": secrets.token_urlsafe(32),
            "CAPABILITY_TOKEN_SECRET": secrets.token_urlsafe(32),
        }
        self.replicas: List[Replica] = []
        for index in range(size):
            name = "ABCDEFGH"[index]
            port = free_port()
            rdir = workdir / f"replica-{name}"
            rdir.mkdir(parents=True, exist_ok=True)
            env = replica_env(
                name=name, port=port, workdir=rdir, config_dir=self.config_dir,
                side_effect_dir=self.side_effect_dir, redis_url=infra.redis_url,
                redis_key_prefix=infra.redis_key_prefix, postgres_url=infra.postgres_url,
                secrets=shared_secrets, overrides=overrides,
            )
            self.replicas.append(Replica(name=name, port=port, workdir=rdir, env=env))
        self._workspace_users: set = set()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        # Start the first replica alone so schema creation is not raced, then
        # the rest.
        for replica in self.replicas:
            replica.start()

    def heal(self) -> None:
        """Bring every replica back to running after a test killed or paused one."""
        for replica in self.replicas:
            if replica.paused:
                replica.resume()
            if not replica.running:
                replica.start()

    def close(self) -> None:
        for replica in self.replicas:
            replica.stop()

    def __getitem__(self, name: str) -> Replica:
        return next(r for r in self.replicas if r.name == name)

    @property
    def a(self) -> Replica:
        return self.replicas[0]

    @property
    def b(self) -> Replica:
        return self.replicas[1]

    # -- HTTP helpers ----------------------------------------------------------

    @staticmethod
    def new_user() -> str:
        return f"mr-{uuid.uuid4().hex[:10]}@example.com"

    def _service_headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def call(self, replica: Replica, method: str, path: str, *, json_body: Any = None,
             user: Optional[str] = None, service: bool = False, timeout: float = 15.0) -> Tuple[int, Any]:
        headers = {}
        if user:
            headers["X-User-Email"] = user
        if service:
            headers.update(self._service_headers())
        response = httpx.request(method, replica.url + path, json=json_body, headers=headers, timeout=timeout)
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = response.text
        return response.status_code, body

    def ensure_workspace(self, user: str, replica: Optional[Replica] = None) -> None:
        """Create the harness workspace for ``user`` (stored in shared Postgres)."""
        if user in self._workspace_users:
            return
        status, body = self.call(replica or self.a, "POST", "/api/workspaces", user=user, json_body={
            "name": WORKSPACE,
            "description": "multi-replica harness",
            "config": {"selected_tools": ["atlas_sleep", "harness_side_effect"], "selected_data_sources": []},
        })
        assert status == 200, (status, body)
        self._workspace_users.add(user)

    def launch(self, replica: Replica, user: str, message: str, **extra: Any) -> Tuple[int, Any]:
        """POST /api/agent-runs on ``replica``; returns (status, body) without asserting."""
        self.ensure_workspace(user)
        payload = {"user": user, "workspace": WORKSPACE, "model": "scripted-mock", "message": message, **extra}
        return self.call(replica, "POST", "/api/agent-runs", json_body=payload, service=True)

    def launch_ok(self, replica: Replica, user: str, message: str, **extra: Any) -> Dict[str, Any]:
        status, body = self.launch(replica, user, message, **extra)
        assert status in (200, 202), (status, body)
        return body

    def run_status(self, replica: Replica, run_id: str) -> Tuple[int, Dict[str, Any]]:
        """GET the run on ``replica``. The body is always a JSON object.

        A non-JSON body (a proxy error page, a plain-text 500) breaks the
        transport contract: it fails the test with a plain ``AssertionError``
        instead of an ``AttributeError`` later, and never counts as a gap.
        """
        status, body = self.call(replica, "GET", f"/api/agent-runs/{run_id}", service=True)
        assert isinstance(body, dict), f"{replica.name} answered {status} with a non-JSON body: {body!r:.300}"
        return status, body

    def cancel(self, replica: Replica, run_id: str) -> Tuple[int, Any]:
        return self.call(replica, "POST", f"/api/agent-runs/{run_id}/cancel", service=True)

    def wait_terminal(self, replica: Replica, run_id: str, timeout: float = 60.0) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        body: Any = None
        while time.monotonic() < deadline:
            status, body = self.run_status(replica, run_id)
            assert status == 200, (status, body)
            if body["status"] in TERMINAL:
                return body
            time.sleep(0.3)
        raise AssertionError(f"run {run_id} not terminal on {replica.name} after {timeout}s: {body}")

    def wait_status(self, replica: Replica, run_id: str, wanted: str, timeout: float = 30.0) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        body: Any = None
        while time.monotonic() < deadline:
            status, body = self.run_status(replica, run_id)
            if status == 200 and body.get("status") == wanted:
                return body
            time.sleep(0.2)
        raise AssertionError(f"run {run_id} never reached {wanted!r} on {replica.name}: {body}")

    def health(self, replica: Replica) -> Dict[str, Any]:
        status, body = self.call(replica, "GET", "/api/health", timeout=10)
        assert status == 200 and isinstance(body, dict), (status, body)
        return body

    def coordination(self, replica: Replica, *, include_terminal: bool = False) -> Dict[str, Any]:
        """``GET /admin/coordination`` on ``replica`` (issue #1129)."""
        # refresh: tests poll for state changes and must not read a cached probe.
        path = "/admin/coordination?refresh=true" + ("&include_terminal=true" if include_terminal else "")
        status, body = self.call(replica, "GET", path, user=ADMIN_USER, timeout=10)
        assert status == 200 and isinstance(body, dict), (status, body)
        return body

    def conversation_ids(self, replica: Replica, user: str) -> List[str]:
        status, body = self.call(replica, "GET", "/api/conversations", user=user)
        assert status == 200, (status, body)
        return [c["id"] for c in body["conversations"]]

    # -- shared coordination state (read-only) -----------------------------------

    def redis_json(self, *parts: str) -> Optional[Dict[str, Any]]:
        """A JSON document the replicas keep in the shared Redis, or None."""
        import redis

        client = redis.Redis.from_url(self.infra.redis_url)
        try:
            raw = client.get(":".join((self.infra.redis_key_prefix, *parts)))
        finally:
            client.close()
        return json.loads(raw) if raw else None

    def run_record(self, run_id: str) -> Optional[Dict[str, Any]]:
        """The shared run record (``<prefix>:run:<id>``, a hash; see
        docs/developer/run-coordination.md), decoded: ``generation`` as an
        int, ``interrupted`` as a bool, an empty ``owner`` as None."""
        import redis

        client = redis.Redis.from_url(self.infra.redis_url)
        try:
            raw = client.hgetall(":".join((self.infra.redis_key_prefix, "run", run_id)))
        finally:
            client.close()
        if not raw:
            return None
        record = {k.decode(): v.decode() for k, v in raw.items()}
        record["generation"] = int(record.get("generation") or 0)
        record["interrupted"] = record.get("interrupted") == "1"
        record["owner"] = record.get("owner") or None
        return record

    def execution_doc(self, user: str, conversation_id: str) -> Optional[Dict[str, Any]]:
        """The conversation's execution projection (issue #1122)."""
        return self.redis_json("exec", user, conversation_id)

    # -- side-effect accounting -------------------------------------------------

    def side_effects(self, key: str) -> List[Dict[str, Any]]:
        """Complete records the side-effect tool wrote under ``key``."""
        return read_records(self.side_effect_dir / log_name(key))

    def wait_side_effects(self, key: str, count: int, timeout: float = 30.0) -> List[Dict[str, Any]]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            records = self.side_effects(key)
            if len(records) >= count:
                return records
            time.sleep(0.2)
        raise AssertionError(f"side effect {key!r}: wanted {count}, saw {self.side_effects(key)}")

    def cancel_everywhere(self, run_id: str) -> None:
        """Best-effort cleanup: cancel ``run_id`` on whichever replica owns it."""
        for replica in self.replicas:
            if replica.running and not replica.paused:
                try:
                    self.cancel(replica, run_id)
                except httpx.HTTPError:
                    pass  # best effort: the replica may be going down; heal() follows


def directive(label: str, *, sleep: float = 0, steps: int = 1, tool: str = "sleep", words: int = 3,
              stream_ms: int = 0) -> str:
    """A message the scripted mock turns into ``steps`` tool calls then an answer."""
    return f"label={label} tool={tool} sleep={sleep} steps={steps} words={words} stream_ms={stream_ms}"
