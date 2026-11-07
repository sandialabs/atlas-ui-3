"""Latency, error and gauge bookkeeping for a load run.

Everything a run measures goes through one ``Metrics`` object:

- ``record(op, seconds, error=None)``: one timed operation (a WebSocket
  connect, a chat turn, an OIDC login...). Failed operations keep their
  latency too, but percentiles are computed over successes only, so a burst of
  fast failures cannot make a profile look quicker.
- ``count(name)``: an event with no latency (a resumed session, a relogin).
- ``gauge(name, value)`` / ``timeline``: point-in-time samples taken once per
  sampling interval (open connections, active runs, replica RSS...).

``summary()`` turns it into the plain-JSON report section; ``percentile`` is
linear interpolation between closest ranks (numpy's default), so p50 of
``[1, 2, 3, 4]`` is 2.5.
"""

from __future__ import annotations

import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


def percentile(values: Iterable[float], pct: float) -> Optional[float]:
    """The ``pct`` percentile (0-100) of ``values``; None when empty."""
    ordered = sorted(values)
    if not ordered:
        return None
    if not 0 <= pct <= 100:
        raise ValueError(f"percentile must be within 0-100, got {pct}")
    rank = (len(ordered) - 1) * pct / 100.0
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def classify_error(exc: BaseException) -> str:
    """A short, stable error class for grouping (never the message text)."""
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return f"http_{status}"
    return type(exc).__name__


@dataclass
class OpStats:
    ok: List[float] = field(default_factory=list)
    failed: List[float] = field(default_factory=list)
    errors: Counter = field(default_factory=Counter)

    def summary(self) -> Dict[str, Any]:
        def ms(value: Optional[float]) -> Optional[float]:
            return None if value is None else round(value * 1000.0, 1)

        total = len(self.ok) + len(self.failed)
        return {
            "count": total,
            "ok": len(self.ok),
            "errors": len(self.failed),
            "error_rate": round(len(self.failed) / total, 4) if total else 0.0,
            "p50_ms": ms(percentile(self.ok, 50)),
            "p95_ms": ms(percentile(self.ok, 95)),
            "p99_ms": ms(percentile(self.ok, 99)),
            "max_ms": ms(max(self.ok) if self.ok else None),
            "mean_ms": ms(sum(self.ok) / len(self.ok) if self.ok else None),
            "error_classes": dict(self.errors),
        }


class Metrics:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.ops: Dict[str, OpStats] = defaultdict(OpStats)
        self.counters: Counter = Counter()
        self.gauges: Dict[str, float] = {}
        self.timeline: List[Dict[str, Any]] = []
        self.phases: List[Dict[str, Any]] = []
        self.error_samples: List[Dict[str, str]] = []

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def record(self, op: str, seconds: float, error: Optional[str] = None, detail: str = "") -> None:
        stats = self.ops[op]
        if error is None:
            stats.ok.append(seconds)
            return
        stats.failed.append(seconds)
        stats.errors[error] += 1
        # Keep a few examples per run for the report; the counts above are
        # the measurement, these are only for diagnosis.
        if len(self.error_samples) < 25:
            self.error_samples.append({"op": op, "error": error, "detail": detail[:300]})

    def count(self, name: str, amount: int = 1) -> None:
        self.counters[name] += amount

    def gauge(self, name: str, value: float) -> None:
        self.gauges[name] = value

    def add_sample(self, sample: Dict[str, Any]) -> None:
        sample = {"t": round(self.elapsed(), 2), **sample}
        self.timeline.append(sample)

    def phase(self, name: str, kind: str, seconds: float, **info: Any) -> None:
        self.phases.append({"name": name, "kind": kind, "seconds": round(seconds, 2), **info})

    def peaks(self) -> Dict[str, Any]:
        """Max of every numeric timeline field (nested dicts flattened with dots)."""
        peaks: Dict[str, float] = {}
        for sample in self.timeline:
            for key, value in _flatten(sample).items():
                if key == "t" or not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                if key not in peaks or value > peaks[key]:
                    peaks[key] = value
        return dict(sorted(peaks.items()))

    def summary(self) -> Dict[str, Any]:
        total_ok = sum(len(s.ok) for s in self.ops.values())
        total_failed = sum(len(s.failed) for s in self.ops.values())
        return {
            "duration_s": round(self.elapsed(), 2),
            "operations": {op: stats.summary() for op, stats in sorted(self.ops.items())},
            "totals": {"ok": total_ok, "errors": total_failed},
            "counters": dict(sorted(self.counters.items())),
            "phases": self.phases,
            "peaks": self.peaks(),
            "error_samples": self.error_samples,
        }


def _flatten(value: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    for key, item in value.items():
        name = f"{prefix}{key}"
        if isinstance(item, dict):
            flat.update(_flatten(item, name + "."))
        else:
            flat[name] = item
    return flat
