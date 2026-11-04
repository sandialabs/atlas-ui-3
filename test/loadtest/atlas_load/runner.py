"""Executes a load profile's phases against an environment and samples it.

A profile is a list of phases run in order over one shared pool of
connections (see ``profiles/README`` in the docs for the schema). Connection
``i`` belongs to user ``i % users`` and starts on replica ``i % replicas``,
and every random choice comes from ``random.Random(seed)``, so the same
profile, seed and scale produce the same plan every time.

While the phases run, a sampler records once per ``sample_interval``:
client-side open sockets and in-flight turns/API runs, server-side API runs
still active per replica, and (local runs) each replica's CPU, RSS, fds,
threads and server-side TCP connections plus Redis/Postgres connections.
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any, Dict, List, Optional

import httpx

from .clients import Connection, VirtualUser
from .metrics import Metrics, classify_error

TERMINAL = ("completed", "failed", "cancelled")


def directive(spec: Dict[str, Any], label: str) -> str:
    """The scripted-mock instruction for one turn (see harness mock_llm.py)."""
    if not spec:
        return f"load test turn {label}"
    keys = {"tool": "sleep", "sleep": 1, "steps": 1, "words": 20, "stream_ms": 10, **spec}
    return f"label={label} " + " ".join(f"{k}={v}" for k, v in keys.items())


class Runner:
    def __init__(self, profile: Dict[str, Any], env, metrics: Metrics, proxy_secret: Optional[str] = None):
        self.profile = profile
        self.env = env
        self.metrics = metrics
        self.rng = random.Random(profile.get("seed", 1129))
        self.auth = profile.get("auth", "header")
        self.users: Dict[int, VirtualUser] = {}
        self.connections: List[Connection] = []
        self.in_flight = {"turns": 0, "api_runs": 0}
        self.proxy_secret = proxy_secret
        self.sampler = None
        self._api_users: set = set()
        self._service = httpx.AsyncClient(timeout=30.0)

    # -- helpers -----------------------------------------------------------------

    def user(self, index: int) -> VirtualUser:
        if index not in self.users:
            self.users[index] = VirtualUser(index, self.auth, self.metrics, self.proxy_secret)
        return self.users[index]

    def replica_url(self, index: int) -> str:
        urls = self.env.urls
        return urls[index % len(urls)]

    async def _bounded(self, items, worker, concurrency: int, ramp_seconds: float = 0.0) -> None:
        """Run ``worker(item)`` for every item, at most ``concurrency`` at once,
        starts spread evenly over ``ramp_seconds``; failures are already
        recorded by the worker and never stop the phase."""
        gate = asyncio.Semaphore(max(1, concurrency))
        items = list(items)
        step = ramp_seconds / len(items) if items and ramp_seconds else 0.0

        async def one(position, item):
            if step:
                await asyncio.sleep(position * step)
            async with gate:
                try:
                    await worker(item)
                except Exception:
                    pass  # recorded in Metrics by the worker

        await asyncio.gather(*(one(i, item) for i, item in enumerate(items)))

    def _pick(self, pool: List[Any], fraction: float) -> List[Any]:
        count = max(0, min(len(pool), round(len(pool) * fraction)))
        return self.rng.sample(pool, count) if count < len(pool) else list(pool)

    # -- phases ------------------------------------------------------------------

    async def phase_connect(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        users = max(1, int(spec.get("users", self.profile.get("users", spec["connections"]))))
        start = len(self.connections)
        new = [Connection(self.user((start + i) % users), self.replica_url(start + i), self.metrics)
               for i in range(int(spec["connections"]))]
        self.connections.extend(new)
        await self._bounded(new, lambda c: c.connect(), int(spec.get("concurrency", 50)),
                            float(spec.get("ramp_seconds", 0)))
        return {"opened": sum(1 for c in new if c.open), "requested": len(new)}

    async def phase_chat(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        targets = self._pick([c for c in self.connections if c.open], float(spec.get("fraction", 1.0)))
        turns = int(spec.get("turns", 1))
        model = spec.get("model", "delegated-mock" if self.auth == "oidc" else "scripted-mock")
        agent = bool(spec.get("agent", False))
        timeout = float(spec.get("timeout", 120))

        async def converse(conn: Connection) -> None:
            for turn in range(turns):
                if not conn.open:
                    self.metrics.record("chat_turn", 0.0, "ConnectionLost")
                    return
                self.in_flight["turns"] += 1
                try:
                    await conn.chat_turn(directive(spec.get("directive", {}), f"t{turn}"), model, agent, timeout,
                                         new_conversation=bool(spec.get("new_conversation", False)))
                finally:
                    self.in_flight["turns"] -= 1

        await self._bounded(targets, converse, int(spec.get("concurrency", len(targets) or 1)),
                            float(spec.get("ramp_seconds", 0)))
        return {"connections": len(targets), "turns_each": turns, "agent": agent, "model": model}

    async def _api_call(self, base: str, method: str, path: str, body: Any = None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.env.service_key}"}
        return await self._service.request(method, base + path, json=body, headers=headers)

    async def _ensure_workspace(self, user: VirtualUser, base: str) -> None:
        if user.email in self._api_users:
            return
        await user.ensure_login(base)
        body = {"name": "Load test", "description": "atlas load profile",
                "config": {"selected_tools": ["atlas_sleep"], "selected_data_sources": []}}
        response = await user.http.post(base + "/api/workspaces", headers=user.headers(), json=body)
        if response.status_code == 401 and user.auth == "oidc":
            self.metrics.count("oidc_relogin")
            await user.ensure_login(base, force=True)
            response = await user.http.post(base + "/api/workspaces", headers=user.headers(), json=body)
        if response.status_code != 200:
            raise httpx.HTTPStatusError("workspace", request=response.request, response=response)
        self._api_users.add(user.email)

    async def phase_api_runs(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        if not self.env.service_key:
            raise RuntimeError("api_runs needs the agent-runs service key (--service-key for --target)")
        runs = int(spec["runs"])
        users = max(1, int(spec.get("users", runs)))
        timeout = float(spec.get("timeout", 120))
        outcomes: Dict[str, int] = {}

        async def one(i: int) -> None:
            base = self.replica_url(i)
            user = self.user(10000 + i % users)  # API users never share a socket user
            started = time.monotonic()
            try:
                await self._ensure_workspace(user, base)
                response = await self._api_call(base, "POST", "/api/agent-runs", {
                    "user": user.email, "workspace": "Load test", "model": spec.get("model", "scripted-mock"),
                    "message": directive(spec.get("directive", {}), f"api{i}")})
                if response.status_code not in (200, 202):
                    raise httpx.HTTPStatusError("launch", request=response.request, response=response)
            except Exception as exc:
                self.metrics.record("api_run_launch", time.monotonic() - started, classify_error(exc), repr(exc))
                return
            self.metrics.record("api_run_launch", time.monotonic() - started)
            run_id = response.json()["run_id"]
            self.in_flight["api_runs"] += 1
            try:
                status = await self._poll_run(base, run_id, started + timeout)
            finally:
                self.in_flight["api_runs"] -= 1
            outcomes[status] = outcomes.get(status, 0) + 1
            error = None if status == "completed" else f"run_{status}"
            self.metrics.record("api_run_total", time.monotonic() - started, error)

        await self._bounded(range(runs), one, int(spec.get("concurrency", runs)), float(spec.get("ramp_seconds", 0)))
        return {"runs": runs, "outcomes": outcomes}

    async def _poll_run(self, base: str, run_id: str, deadline: float) -> str:
        while time.monotonic() < deadline:
            try:
                response = await self._api_call(base, "GET", f"/api/agent-runs/{run_id}")
                if response.status_code == 200 and response.json().get("status") in TERMINAL:
                    return response.json()["status"]
            except httpx.HTTPError:
                pass  # replica busy or restarting; keep polling until the deadline
            await asyncio.sleep(0.5)
        return "timeout"

    async def phase_hold(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        before = sum(1 for c in self.connections if c.open)
        await asyncio.sleep(float(spec.get("seconds", 5)))
        after = sum(1 for c in self.connections if c.open)
        if before > after:
            self.metrics.count("connections_lost_while_idle", before - after)
        return {"open_before": before, "open_after": after}

    async def phase_reconnect_storm(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Drop many sockets at once and reconnect them, with session resume.

        ``trigger``: ``client_drop`` aborts a ``fraction`` of the sockets from
        the client side; ``replica_kill`` SIGKILLs replica ``replica`` (local
        runs only) so every socket on it dies. ``to: other`` reconnects each
        socket to the next replica (no stickiness), ``same`` to the one it was
        on (after a kill, survivors are always used).
        """
        trigger = spec.get("trigger", "client_drop")
        urls = self.env.urls
        killed_url = None
        t0 = time.monotonic()
        if trigger == "replica_kill":
            if not self.env.local:
                raise RuntimeError("replica_kill needs a local run")
            victim = self.env.cluster[spec.get("replica", "A")]
            killed_url = victim.url
            affected = [c for c in self.connections if c.base_url == killed_url and c.ws is not None]
            await asyncio.to_thread(victim.kill)
            await asyncio.gather(*(asyncio.wait_for(c.closed_by_server.wait(), 30) for c in affected),
                                 return_exceptions=True)
            for conn in affected:
                await conn.drop()
        else:
            affected = self._pick([c for c in self.connections if c.open], float(spec.get("fraction", 1.0)))
            for conn in affected:
                await conn.drop()
        survivors = [u for u in urls if u != killed_url]
        for conn in affected:
            if spec.get("to", "other") == "other" or conn.base_url == killed_url:
                pos = urls.index(conn.base_url)
                nxt = next(urls[(pos + k) % len(urls)] for k in range(1, len(urls) + 1)
                           if urls[(pos + k) % len(urls)] in survivors)
                conn.base_url = nxt
        jitter = float(spec.get("jitter_ms", 0)) / 1000.0

        async def reconnect(conn: Connection) -> None:
            if jitter:
                await asyncio.sleep(self.rng.uniform(0, jitter))
            await conn.connect(op="ws_reconnect")

        await self._bounded(affected, reconnect, int(spec.get("concurrency", len(affected) or 1)))
        recovered = time.monotonic() - t0
        result = {"trigger": trigger, "affected": len(affected),
                  "reconnected": sum(1 for c in affected if c.open), "recovery_s": round(recovered, 2)}
        if killed_url and spec.get("heal", True):
            started = time.monotonic()
            await asyncio.to_thread(self.env.cluster.heal)
            self.metrics.record("replica_restart", time.monotonic() - started)
        return result

    async def phase_parallel(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Run the nested ``phases`` at the same time (e.g. WS turns + API runs)."""
        async def child(sub: Dict[str, Any]) -> Dict[str, Any]:
            if sub["kind"] in ("parallel", "reconnect_storm", "disconnect"):
                raise ValueError(f"{sub['kind']} cannot run inside a parallel phase")
            started = time.monotonic()
            info = await getattr(self, f"phase_{sub['kind']}")(sub)
            self.metrics.phase(sub.get("name", sub["kind"]), sub["kind"], time.monotonic() - started, **info)
            return info

        results = await asyncio.gather(*(child(sub) for sub in spec["phases"]))
        return {"children": len(results)}

    async def phase_disconnect(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        await asyncio.gather(*(c.close() for c in self.connections))
        closed = len(self.connections)
        self.connections = []
        return {"closed": closed}

    # -- sampling --------------------------------------------------------------------

    async def _server_active_runs(self) -> Dict[str, int]:
        if not self.env.service_key:
            return {}
        out = {}
        for index, base in enumerate(self.env.urls):
            name = self.env.replicas[index].name if self.env.replicas else str(index)
            try:
                response = await self._api_call(base, "GET", "/api/agent-runs")
                runs = response.json().get("runs", []) if response.status_code == 200 else []
                out[name] = sum(1 for r in runs if r.get("status") not in TERMINAL)
            except (httpx.HTTPError, ValueError):
                out[name] = -1  # replica unreachable at this instant
        return out

    async def sample_once(self) -> None:
        sample: Dict[str, Any] = {
            "client": {
                "open_connections": sum(1 for c in self.connections if c.open),
                "in_flight_turns": self.in_flight["turns"],
                "in_flight_api_runs": self.in_flight["api_runs"],
                "active_runs": self.in_flight["turns"] + self.in_flight["api_runs"],
            },
            "server_active_api_runs": await self._server_active_runs(),
        }
        if self.sampler is not None:
            sample["server"] = await asyncio.to_thread(self.sampler.sample)
        self.metrics.add_sample(sample)

    async def sample_loop(self, interval: float) -> None:
        while True:
            try:
                await self.sample_once()
            except Exception as exc:  # sampling must never stop a run
                self.metrics.count(f"sampler_error_{type(exc).__name__}")
            await asyncio.sleep(interval)

    # -- driver ---------------------------------------------------------------------

    async def run(self) -> None:
        interval = float(self.profile.get("sample_interval", 1.0))
        sampler_task = asyncio.create_task(self.sample_loop(interval))
        try:
            for number, spec in enumerate(self.profile["phases"]):
                kind = spec["kind"]
                handler = getattr(self, f"phase_{kind}", None)
                if handler is None:
                    raise ValueError(f"unknown phase kind {kind!r}")
                name = spec.get("name", f"{number}-{kind}")
                started = time.monotonic()
                before = dict(self.metrics.counters)
                info = await handler(spec)
                delta = {k: v - before.get(k, 0) for k, v in self.metrics.counters.items() if v != before.get(k, 0)}
                if delta:
                    info["counters"] = delta
                self.metrics.phase(name, kind, time.monotonic() - started, **info)
            await self.sample_once()
        finally:
            sampler_task.cancel()
            await asyncio.gather(sampler_task, return_exceptions=True)
            await asyncio.gather(*(c.close() for c in self.connections), return_exceptions=True)
            await asyncio.gather(*(u.close() for u in self.users.values()), return_exceptions=True)
            await self._service.aclose()
