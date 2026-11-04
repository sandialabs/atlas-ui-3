"""Server-side resource and connection sampling.

Linux only, and only for processes this run started (it reads ``/proc``):

- per replica process group: CPU %, RSS, open file descriptors, threads and
  the number of ESTABLISHED TCP connections on the replica's listening port
  (that is, the WebSocket and HTTP clients the *server* holds, as opposed to
  what the load client believes it has open);
- shared Redis: ``connected_clients`` and ``used_memory`` from ``INFO``;
- shared PostgreSQL: backends connected to the run's database, by state.

Against a remote target (``--target``) none of this is visible, so the
sampler is not created and the report says so.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Optional, Tuple

_CLK_TCK = os.sysconf("SC_CLK_TCK")
_PAGE = os.sysconf("SC_PAGE_SIZE")


def _read_stat(pid: int) -> Optional[Tuple[int, int, int]]:
    """(process group, utime+stime ticks, threads) from /proc/<pid>/stat."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            raw = handle.read().decode(errors="replace")
    except OSError:
        return None
    # The command name is in parentheses and may contain spaces; fields after
    # the last ')' are positional (field 3 is state).
    fields = raw[raw.rfind(")") + 2:].split()
    pgrp = int(fields[2])
    ticks = int(fields[11]) + int(fields[12])
    threads = int(fields[17])
    return pgrp, ticks, threads


def _rss_bytes(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/statm", "rb") as handle:
            return int(handle.read().split()[1]) * _PAGE
    except (OSError, IndexError, ValueError):
        return 0


def _fd_count(pid: int) -> int:
    try:
        return len(os.listdir(f"/proc/{pid}/fd"))
    except OSError:
        return 0


def pids_by_group(pgids) -> Dict[int, List[int]]:
    """Members of each wanted process group, from one pass over /proc."""
    wanted = set(pgids)
    groups: Dict[int, List[int]] = {pgid: [] for pgid in wanted}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            stat = _read_stat(int(entry))
            if stat is not None and stat[0] in wanted:
                groups[stat[0]].append(int(entry))
    return groups


def established_on_port(port: int) -> int:
    """ESTABLISHED TCP sockets whose *local* port is ``port`` (server side)."""
    count = 0
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(table) as handle:
                next(handle, None)
                for line in handle:
                    parts = line.split()
                    if len(parts) > 3 and parts[3] == "01" and int(parts[1].rsplit(":", 1)[1], 16) == port:
                        count += 1
        except OSError:
            continue
    return count


class ProcessGroupSampler:
    """CPU % between consecutive samples, plus RSS/fds/threads, for one group."""

    def __init__(self, pgid: int):
        self.pgid = pgid
        self._last: Optional[Tuple[float, int]] = None

    def sample(self, pids: List[int]) -> Dict[str, Any]:
        ticks = threads = rss = fds = 0
        for pid in pids:
            stat = _read_stat(pid)
            if stat is None:
                continue
            ticks += stat[1]
            threads += stat[2]
            rss += _rss_bytes(pid)
            fds += _fd_count(pid)
        now = time.monotonic()
        cpu = None
        if self._last is not None and now > self._last[0]:
            cpu = round(100.0 * (ticks - self._last[1]) / _CLK_TCK / (now - self._last[0]), 1)
        self._last = (now, ticks)
        return {
            "processes": len(pids),
            "cpu_pct": cpu,
            "rss_mb": round(rss / 1048576, 1),
            "fds": fds,
            "threads": threads,
        }


class ResourceSampler:
    """Samples every replica plus the shared Redis/Postgres in one call."""

    def __init__(self, replicas, redis_url: Optional[str], postgres_url: Optional[str],
                 extra_groups: Optional[Dict[str, int]] = None):
        self.replicas = replicas
        self.redis_url = redis_url
        self.postgres_url = postgres_url
        self._groups: Dict[str, ProcessGroupSampler] = {}
        self._extra = extra_groups or {}
        self._redis = None

    def _group(self, name: str, pgid: int) -> ProcessGroupSampler:
        sampler = self._groups.get(name)
        if sampler is None or sampler.pgid != pgid:  # a restarted replica is a new group
            sampler = self._groups[name] = ProcessGroupSampler(pgid)
        return sampler

    def sample(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"replicas": {}}
        live = {r.name: r.proc.pid for r in self.replicas if r.proc is not None and r.proc.poll() is None}
        # One /proc pass per sample for every group, so sampling cost does not
        # grow with the replica count.
        members = pids_by_group(list(live.values()) + list(self._extra.values()))
        for replica in self.replicas:
            if replica.name not in live:
                out["replicas"][replica.name] = {"up": 0, "server_conns": 0}
                continue
            pgid = live[replica.name]
            stats = self._group(replica.name, pgid).sample(members[pgid])
            stats["up"] = 1
            stats["server_conns"] = established_on_port(replica.port)
            out["replicas"][replica.name] = stats
        for name, pgid in self._extra.items():
            out[name] = self._group(name, pgid).sample(members[pgid])
        out["redis"] = self._redis_info()
        out["postgres"] = self._pg_backends()
        return out

    def _redis_info(self) -> Dict[str, Any]:
        if not self.redis_url:
            return {}
        try:
            import redis

            if self._redis is None:
                self._redis = redis.Redis.from_url(self.redis_url, socket_timeout=2)
            info = self._redis.info()
            # The sampler's own connection is one of the clients; leave it in,
            # the report states the count includes it.
            return {"connected_clients": info.get("connected_clients"),
                    "used_memory_mb": round(info.get("used_memory", 0) / 1048576, 2)}
        except Exception as exc:  # sampling must never stop a run
            return {"error": type(exc).__name__}

    def _pg_backends(self) -> Dict[str, Any]:
        if not self.postgres_url:
            return {}
        try:
            import psycopg

            with psycopg.connect(self.postgres_url, connect_timeout=2) as conn:
                rows = conn.execute(
                    "SELECT coalesce(state, 'unknown'), count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid <> pg_backend_pid() GROUP BY 1"
                ).fetchall()
            by_state = {state: count for state, count in rows}
            return {"connections": sum(by_state.values()), **{f"state_{k.replace(' ', '_')}": v
                                                              for k, v in by_state.items()}}
        except Exception as exc:  # sampling must never stop a run
            return {"error": type(exc).__name__}

    def close(self) -> None:
        if self._redis is not None:
            self._redis.close()
