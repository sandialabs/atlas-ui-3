"""Per-replica coordination metrics and structured logs (issue #1129).

Every Atlas API replica keeps its own counters for run lifecycle and shared
dependencies (Redis, PostgreSQL), and writes one structured log line per
coordination event. Operators read them three ways:

* ``GET /admin/coordination``          -- JSON snapshot for this replica
* ``GET /admin/coordination/metrics``  -- the same numbers as Prometheus text
* log lines starting with ``[COORD]``  -- grep/ship them; every line carries
  ``replica=<id>`` so lines from different pods can be told apart

The counters are deliberately process-local: they describe *this* replica, and
a metrics backend sums them across pods. Nothing here is coordination state,
so the horizontal-scaling rule about in-process state does not apply -- losing
the counters on restart is the normal Prometheus counter-reset semantics.

What is recorded never includes user identity, prompts or tool arguments:
only run ids, statuses, reasons, dependency names and exception class names.

Run outcomes
------------
``RunStatus`` has three terminal states (completed / failed / cancelled). An
operator also needs to tell a run that *failed* (its own error, a limit) from
one that was *interrupted* (the replica running it went away). The registry
records an ``end_reason`` with each terminal transition; ``outcome_for`` maps
it to the ``outcome`` label used here. An interrupted run is still reported as
``failed`` on the public run API, matching #1128 ("interrupted runs can be
marked failed, retaining completed work").

Events reserved for #1128
-------------------------
``CoordinationEvent`` also names the events distributed run coordination will
emit (leases, orphan detection, fencing, cross-replica command routing), so
those PRs record them through ``record_event`` with stable names instead of
inventing their own log formats.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("atlas.coordination")

# A dependency that keeps failing is logged again at most this often, so an
# outage is visible without one log line per failed command.
DEPENDENCY_REPEAT_LOG_SECONDS = 30.0

# The periodic summary line is written when the numbers change, and at least
# this often while nothing changes, so a quiet replica still proves it is alive.
SUMMARY_LOG_INTERVAL_SECONDS = 300.0

KNOWN_DEPENDENCIES = ("redis", "postgres")


class CoordinationEvent(str, Enum):
    """Stable event names for ``[COORD]`` log lines and the events counter."""

    # Emitted today.
    RUN_ADMITTED = "run_admitted"
    RUN_REJECTED = "run_rejected"
    RUN_STATE_CHANGED = "run_state_changed"
    RUN_FINISHED = "run_finished"
    RUN_INTERRUPTED = "run_interrupted"
    REPLICA_STARTED = "replica_started"
    REPLICA_SHUTDOWN = "replica_shutdown"
    DEPENDENCY_DOWN = "dependency_down"
    DEPENDENCY_STILL_DOWN = "dependency_still_down"
    DEPENDENCY_RECOVERED = "dependency_recovered"
    SUMMARY = "summary"
    # Reserved for distributed run coordination (#1128).
    LEASE_ACQUIRED = "lease_acquired"
    LEASE_RENEWED = "lease_renewed"
    LEASE_LOST = "lease_lost"
    ORPHAN_DETECTED = "orphan_detected"
    RUN_FENCED = "run_fenced"
    COMMAND_ROUTED = "command_routed"
    COMMAND_DELIVERED = "command_delivered"
    COMMAND_TIMED_OUT = "command_timed_out"


class EndReason(str, Enum):
    """Why a run reached a terminal state (``RunRecord.end_reason``)."""

    COMPLETED = "completed"
    USER_CANCELLED = "user_cancelled"
    ERROR = "error"
    WALL_CLOCK_LIMIT = "wall_clock_limit"
    # The replica that owned the run shut down (SIGTERM, rollout, scale-in).
    REPLICA_SHUTDOWN = "replica_shutdown"
    # Another replica took the run over after its owner's lease lapsed.
    OWNER_LOST = "owner_lost"
    # This replica lost the run's claim (or a mirror was refused) and stopped it.
    FENCED = "fenced"


INTERRUPTED_REASONS = frozenset(
    {EndReason.REPLICA_SHUTDOWN.value, EndReason.OWNER_LOST.value, EndReason.FENCED.value}
)


def outcome_for(status: str, end_reason: Optional[str]) -> str:
    """The ``outcome`` label: completed | cancelled | failed | interrupted."""
    if end_reason in INTERRUPTED_REASONS:
        return "interrupted"
    return status


def default_end_reason(status: str) -> str:
    return {
        "completed": EndReason.COMPLETED.value,
        "cancelled": EndReason.USER_CANCELLED.value,
    }.get(status, EndReason.ERROR.value)


def _resolve_replica_id() -> str:
    for name in ("ATLAS_REPLICA_ID", "POD_NAME", "HOSTNAME"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    try:
        return socket.gethostname() or "unknown"
    except OSError:  # pragma: no cover - gethostname essentially never fails
        return "unknown"


def run_owner_id() -> Optional[str]:
    """The #1128 run-owner id of this process (``<hostname>-<pid>-<random>``), if available.

    Provided by ``default_replica_id`` in ``atlas.application.chat.runs.coordination``
    once distributed run coordination is installed; None before that. Imported
    lazily so this module keeps no hard dependency on the application layer.
    """
    try:
        from atlas.application.chat.runs.coordination import default_replica_id  # type: ignore[attr-defined]
    except Exception:
        return None
    try:
        return str(default_replica_id())
    except Exception:  # pragma: no cover - defensive
        return None


def _fmt(value: Any) -> str:
    """One ``key=value`` token: no spaces, newlines or quotes to split on."""
    text = "" if value is None else str(value)
    text = text.replace("\n", " ").replace("\r", " ")
    if not text or any(c in text for c in ' ="'):
        text = '"' + text.replace('"', "'") + '"'
    return text


def _sample_value(value: float) -> str:
    """Full precision: ``:g`` keeps 6 digits, which corrupts timestamps and large counters."""
    number = float(value)
    if number.is_integer() and abs(number) < 2**53:
        return str(int(number))
    return repr(number)


def _label_value(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class _DependencyState:
    __slots__ = ("status", "since", "failures", "last_error", "last_op", "last_logged", "last_checked")

    def __init__(self) -> None:
        self.status = "unknown"
        self.since = time.time()
        self.failures = 0  # consecutive failures in the current outage
        self.last_error: Optional[str] = None
        self.last_op: Optional[str] = None
        self.last_logged = 0.0
        self.last_checked: Optional[float] = None


class CoordinationMetrics:
    """Counters, dependency states and event logging for one replica."""

    def __init__(self, replica_id: Optional[str] = None) -> None:
        self.replica_id = replica_id or _resolve_replica_id()
        # Distinguishes two processes that share a pod name (a restart).
        self.instance_id = uuid.uuid4().hex[:12]
        self.started_at = time.time()
        self._lock = threading.Lock()
        self._counters: Dict[Tuple[str, Tuple[Tuple[str, str], ...]], float] = {}
        self._dependencies: Dict[str, _DependencyState] = {}
        self._last_summary: Optional[Dict[str, Any]] = None
        self._last_summary_at = 0.0

    # -- counters ------------------------------------------------------------

    def incr(self, name: str, amount: float = 1, **labels: Any) -> None:
        key = (name, tuple(sorted((k, str(v)) for k, v in labels.items())))
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + amount

    def counter(self, name: str, **labels: Any) -> float:
        """Sum of ``name`` over every label set matching ``labels``."""
        wanted = {k: str(v) for k, v in labels.items()}
        with self._lock:
            return sum(
                value for (metric, key), value in self._counters.items()
                if metric == name and all(dict(key).get(k) == v for k, v in wanted.items())
            )

    def counters(self) -> List[Dict[str, Any]]:
        with self._lock:
            items = list(self._counters.items())
        return [
            {"name": name, "labels": dict(labels), "value": value}
            for (name, labels), value in sorted(items)
        ]

    # -- events ----------------------------------------------------------------

    def record_event(self, event: "CoordinationEvent | str", level: int = logging.INFO, **fields: Any) -> None:
        """Count the event and write one ``[COORD]`` line.

        ``fields`` must be identifiers, statuses, counts or exception class
        names -- never user identity or content.
        """
        name = event.value if isinstance(event, CoordinationEvent) else str(event)
        self.incr("atlas_coordination_events_total", event=name)
        tokens = " ".join(f"{k}={_fmt(v)}" for k, v in fields.items())
        extra = {f"coord_{k}": v for k, v in fields.items()}
        extra.update({"coord_event": name, "coord_replica": self.replica_id, "coord_instance": self.instance_id})
        logger.log(
            level,
            "[COORD] event=%s replica=%s instance=%s%s",
            name,
            _fmt(self.replica_id),
            self.instance_id,
            f" {tokens}" if tokens else "",
            extra=extra,
        )

    # -- run lifecycle ------------------------------------------------------------

    def run_admitted(self, run_id: str, *, kind: str, depth: int = 0) -> None:
        self.incr("atlas_runs_admitted_total", kind=kind)
        self.record_event(CoordinationEvent.RUN_ADMITTED, run_id=run_id, kind=kind, depth=depth)

    def run_rejected(self, reason: str) -> None:
        self.incr("atlas_runs_rejected_total", reason=reason)
        self.record_event(CoordinationEvent.RUN_REJECTED, level=logging.WARNING, reason=reason)

    def run_transition(
        self,
        run_id: str,
        old_status: str,
        new_status: str,
        *,
        end_reason: Optional[str] = None,
        duration_s: Optional[float] = None,
        detached: bool = False,
    ) -> None:
        self.incr("atlas_run_transitions_total", from_status=old_status, to_status=new_status)
        if end_reason is None:
            self.record_event(
                CoordinationEvent.RUN_STATE_CHANGED, level=logging.DEBUG,
                run_id=run_id, from_status=old_status, to_status=new_status,
            )
            return
        outcome = outcome_for(new_status, end_reason)
        self.incr("atlas_runs_finished_total", outcome=outcome, reason=end_reason)
        if duration_s is not None:
            self.incr("atlas_run_duration_seconds_sum", duration_s, outcome=outcome)
            self.incr("atlas_run_duration_seconds_count", 1, outcome=outcome)
        event = CoordinationEvent.RUN_INTERRUPTED if outcome == "interrupted" else CoordinationEvent.RUN_FINISHED
        level = logging.WARNING if outcome in ("interrupted", "failed") else logging.INFO
        self.record_event(
            event, level=level, run_id=run_id, status=new_status, outcome=outcome,
            reason=end_reason, from_status=old_status,
            duration_s=None if duration_s is None else round(duration_s, 3),
            detached=detached,
        )

    # -- dependencies ---------------------------------------------------------------

    def _dep(self, name: str) -> _DependencyState:
        state = self._dependencies.get(name)
        if state is None:
            state = self._dependencies[name] = _DependencyState()
        return state

    def dependency_failed(self, name: str, exc: "BaseException | str", *, op: str = "command") -> None:
        """Record a failed call to a shared dependency.

        The first failure of an outage logs ``dependency_down`` at ERROR; later
        ones are counted and summarized every ``DEPENDENCY_REPEAT_LOG_SECONDS``.
        """
        error = exc if isinstance(exc, str) else type(exc).__name__
        now = time.time()
        with self._lock:
            state = self._dep(name)
            was = state.status
            state.failures += 1
            state.last_error = error
            state.last_op = op
            state.last_checked = now
            if was != "unavailable":
                state.status = "unavailable"
                state.since = now
                state.last_logged = now
                log_event, failures, since = CoordinationEvent.DEPENDENCY_DOWN, state.failures, now
            elif now - state.last_logged >= DEPENDENCY_REPEAT_LOG_SECONDS:
                state.last_logged = now
                log_event, failures, since = CoordinationEvent.DEPENDENCY_STILL_DOWN, state.failures, state.since
            else:
                log_event = None
                failures, since = state.failures, state.since
        self.incr("atlas_dependency_errors_total", dependency=name, op=op)
        if log_event is not None:
            self.record_event(
                log_event, level=logging.ERROR, dependency=name, op=op, error=error,
                failures=failures, down_for_s=round(now - since, 1),
            )

    def dependency_ok(self, name: str) -> None:
        now = time.time()
        with self._lock:
            state = self._dep(name)
            was, since, failures = state.status, state.since, state.failures
            state.last_checked = now
            if was == "ok":
                return
            state.status = "ok"
            state.since = now
            state.failures = 0
        if was == "unavailable":
            self.record_event(
                CoordinationEvent.DEPENDENCY_RECOVERED, level=logging.WARNING,
                dependency=name, down_for_s=round(now - since, 1), failures=failures,
            )

    def dependencies(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {
                name: {
                    "status": s.status,
                    "since": s.since,
                    "consecutive_failures": s.failures,
                    "last_error": s.last_error,
                    "last_op": s.last_op,
                    "last_checked": s.last_checked,
                }
                for name, s in sorted(self._dependencies.items())
            }

    # -- snapshots ----------------------------------------------------------------------

    def identity(self) -> Dict[str, Any]:
        from atlas.version import VERSION

        return {
            "replica_id": self.replica_id,
            "instance_id": self.instance_id,
            "owner_id": run_owner_id(),
            "pid": os.getpid(),
            "version": VERSION,
            "started_at": self.started_at,
            "uptime_s": round(time.time() - self.started_at, 1),
        }

    def maybe_log_summary(self, run_counts: Dict[str, int], *, force: bool = False) -> bool:
        """Write a ``summary`` line when the counts or dependency states change."""
        deps = {name: d["status"] for name, d in self.dependencies().items()}
        summary = {**{f"runs_{k}": v for k, v in sorted(run_counts.items())}, **{f"dep_{k}": v for k, v in deps.items()}}
        now = time.time()
        if not force and summary == self._last_summary and now - self._last_summary_at < SUMMARY_LOG_INTERVAL_SECONDS:
            return False
        self._last_summary = summary
        self._last_summary_at = now
        self.record_event(CoordinationEvent.SUMMARY, **summary)
        return True

    def render_prometheus(self, gauges: Iterable[Tuple[str, Dict[str, Any], float]] = ()) -> str:
        """Prometheus text exposition (version 0.0.4) of counters plus ``gauges``."""
        base = {"replica": self.replica_id}
        lines: List[str] = []
        seen_types: set = set()

        def emit(name: str, kind: str, labels: Dict[str, Any], value: float) -> None:
            family = name
            for suffix in ("_sum", "_count"):
                if name.endswith(suffix) and kind == "counter":
                    family = name[: -len(suffix)]
                    kind = "summary"
            if family not in seen_types:
                seen_types.add(family)
                lines.append(f"# TYPE {family} {kind}")
            merged = {**base, **labels}
            rendered = ",".join(f'{k}="{_label_value(v)}"' for k, v in sorted(merged.items()))
            lines.append(f"{name}{{{rendered}}} {_sample_value(value)}")

        # "instance_id", not "instance": Prometheus sets its own ``instance``
        # target label and would rename ours to ``exported_instance``.
        info = {"instance_id": self.instance_id, "version": self.identity()["version"]}
        owner = run_owner_id()
        if owner:
            # Lets operators join this pod's metrics to the #1128 owner id in
            # run records and leases (one value per process: low cardinality).
            info["owner_id"] = owner
        emit("atlas_replica_info", "gauge", info, 1)
        emit("atlas_replica_start_time_seconds", "gauge", {}, self.started_at)
        for name, labels, value in gauges:
            emit(name, "gauge", labels, value)
        for name, state in self.dependencies().items():
            emit("atlas_dependency_up", "gauge", {"dependency": name}, 1 if state["status"] == "ok" else 0)
        for item in self.counters():
            emit(item["name"], "counter", item["labels"], item["value"])
        return "\n".join(lines) + "\n"


_metrics: Optional[CoordinationMetrics] = None
_metrics_lock = threading.Lock()


def coordination_metrics() -> CoordinationMetrics:
    """The process-wide instance."""
    global _metrics
    if _metrics is None:
        with _metrics_lock:
            if _metrics is None:
                _metrics = CoordinationMetrics()
    return _metrics


def reset_coordination_metrics() -> None:
    """Drop the singleton (tests only)."""
    global _metrics
    _metrics = None
