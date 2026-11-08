"""Shared Redis and PostgreSQL for the replicas, started with podman.

Set ``ATLAS_TEST_REDIS_URL`` and/or ``ATLAS_TEST_POSTGRES_URL`` to use servers
you already run; anything not given is started as a disposable podman
container on a random local port and removed afterwards. With neither the URLs
nor podman available the suite is skipped; any other infrastructure failure
fails it (see ``conftest.shared_infra``).

A provided Redis is isolated with a random key prefix and a provided Postgres
with a fresh database that is dropped afterwards, so the suite never touches
existing data.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from urllib.parse import urlsplit, urlunsplit

from .manifest import record_container

REDIS_IMAGE = os.environ.get("ATLAS_TEST_REDIS_IMAGE", "docker.io/library/redis:7-alpine")
POSTGRES_IMAGE = os.environ.get("ATLAS_TEST_POSTGRES_IMAGE", "docker.io/library/postgres:16-alpine")


class InfraUnavailable(RuntimeError):
    """A provided URL or podman could not supply a working server."""


@dataclass
class SharedInfra:
    redis_url: str
    postgres_url: str
    redis_key_prefix: str
    # Container names when the harness started the server itself (None for a
    # supplied URL). The failure exercises freeze these to simulate an outage.
    redis_container: Optional[str] = None
    postgres_container: Optional[str] = None
    _cleanups: List[Callable[[], None]] = field(default_factory=list)

    def close(self) -> None:
        while self._cleanups:
            try:
                self._cleanups.pop()()
            except Exception as exc:  # cleanup must not mask a test failure
                print(f"[multireplica] cleanup failed: {exc}")


def _podman() -> str:
    path = shutil.which("podman")
    if not path:
        raise InfraUnavailable("podman is not installed and no ATLAS_TEST_*_URL was given")
    return path


def _run_container(name: str, image: str, container_port: int, env: dict,
                   extra: Optional[List[str]] = None, args: Optional[List[str]] = None) -> int:
    podman = _podman()
    cmd = [podman, "run", "-d", "--rm", "--name", name, "-p", f"127.0.0.1::{container_port}"]
    for key, value in env.items():
        cmd += ["-e", f"{key}={value}"]
    cmd += extra or []
    cmd.append(image)
    cmd += args or []
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise InfraUnavailable(f"podman run {image} failed: {result.stderr.strip()}")
    record_container(name)
    output = ""
    try:
        output = subprocess.run(
            [podman, "port", name, f"{container_port}/tcp"], capture_output=True, text=True, timeout=30
        ).stdout.strip()
        # "127.0.0.1:41234" (one line per binding)
        return int(output.splitlines()[0].rsplit(":", 1)[1])
    except (subprocess.SubprocessError, IndexError, ValueError) as exc:
        # The caller only registers cleanup once it has a port; remove the
        # container here so a failed lookup cannot leave it running.
        _remove_container(name)()
        raise InfraUnavailable(f"podman port {name} failed: {exc}; output: {output!r}") from exc


def _remove_container(name: str) -> Callable[[], None]:
    def _rm() -> None:
        subprocess.run([_podman(), "rm", "-f", "-t", "2", name], capture_output=True, timeout=60)

    return _rm


def _wait(check: Callable[[], bool], what: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    last: Optional[BaseException] = None
    while time.monotonic() < deadline:
        try:
            if check():
                return
        except Exception as exc:
            last = exc
        time.sleep(0.5)
    raise InfraUnavailable(f"{what} did not become ready in {timeout:.0f}s: {last}")


def _redis_ready(url: str) -> bool:
    import redis

    client = redis.Redis.from_url(url, socket_timeout=2)
    try:
        return bool(client.ping())
    finally:
        client.close()


def _pg_ready(url: str) -> bool:
    import psycopg

    with psycopg.connect(url, connect_timeout=2) as conn:
        conn.execute("SELECT 1")
    return True


def _with_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path="/" + database))


def start_shared_infra() -> SharedInfra:
    """Start (or adopt) Redis and Postgres for one test session."""
    tag = uuid.uuid4().hex[:8]
    infra = SharedInfra(redis_url="", postgres_url="", redis_key_prefix=f"atlas-mr-{tag}")
    try:
        redis_url = os.environ.get("ATLAS_TEST_REDIS_URL")
        if not redis_url:
            name = f"atlas-mr-redis-{tag}"
            port = _run_container(name, REDIS_IMAGE, 6379, {})
            infra._cleanups.append(_remove_container(name))
            infra.redis_container = name
            redis_url = f"redis://127.0.0.1:{port}/0"
        _wait(lambda: _redis_ready(redis_url), "Redis")
        infra.redis_url = redis_url

        admin_url = os.environ.get("ATLAS_TEST_POSTGRES_URL")
        if not admin_url:
            name = f"atlas-mr-pg-{tag}"
            # Data and socket dirs on tmpfs: rootless podman can hand the
            # image's anonymous volumes to the wrong owner ("data directory has
            # wrong ownership" / "could not create any Unix-domain sockets"),
            # and a throwaway database does not need a disk anyway.
            port = _run_container(
                name, POSTGRES_IMAGE, 5432,
                {"POSTGRES_USER": "atlas", "POSTGRES_PASSWORD": "atlas", "POSTGRES_DB": "postgres",
                 "PGDATA": "/pgtmp/data"},
                extra=["--tmpfs", "/pgtmp:rw,size=512m,mode=1777",
                       "--tmpfs", "/var/run/postgresql:rw,mode=1777"],
                args=["-c", "max_connections=200", "-c", "fsync=off"],
            )
            infra._cleanups.append(_remove_container(name))
            infra.postgres_container = name
            admin_url = f"postgresql://atlas:atlas@127.0.0.1:{port}/postgres"
        # Postgres accepts a connection briefly during init and then restarts;
        # require two successes a moment apart.
        _wait(lambda: _pg_ready(admin_url) and (time.sleep(1.0) or _pg_ready(admin_url)), "PostgreSQL", 90)

        import psycopg

        database = f"atlas_mr_{tag}"
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(f'CREATE DATABASE "{database}"')

        def _drop() -> None:
            with psycopg.connect(admin_url, autocommit=True) as conn:
                conn.execute(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')

        infra._cleanups.append(_drop)
        infra.postgres_url = _with_database(admin_url, database)

        def _flush_prefix() -> None:
            import redis

            client = redis.Redis.from_url(redis_url)
            try:
                keys = list(client.scan_iter(match=f"{infra.redis_key_prefix}:*", count=500))
                if keys:
                    client.delete(*keys)
            finally:
                client.close()

        infra._cleanups.append(_flush_prefix)
        return infra
    except BaseException:
        infra.close()
        raise


def freeze_container(name: str) -> None:
    """``podman pause``: every process in the container stops answering.

    A frozen server keeps its TCP port open and never replies, which is the
    harder outage to detect (a black hole, not a refused connection).
    """
    result = subprocess.run([_podman(), "pause", name], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise InfraUnavailable(f"podman pause {name} failed: {result.stderr.strip()}")


def thaw_container(name: str) -> None:
    result = subprocess.run([_podman(), "unpause", name], capture_output=True, text=True, timeout=60)
    if result.returncode != 0 and "not paused" not in result.stderr:
        raise InfraUnavailable(f"podman unpause {name} failed: {result.stderr.strip()}")
