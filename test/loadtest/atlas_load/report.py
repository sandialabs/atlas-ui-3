"""Report assembly, SLO checks and the human-readable summary."""

from __future__ import annotations

import csv
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from .metrics import Metrics, _flatten


def _git_revision() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              timeout=5, cwd=Path(__file__).parent).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def check_slo(profile: Dict[str, Any], summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Evaluate the profile's ``slo`` block. Each entry is one pass/fail check.

    ``max_error_rate``: per operation, errors / attempts (``"*"`` for every
    operation without its own limit). ``p95_ms``: per
    operation limit. ``min_count``: per operation, at least this many
    successes (so an operation that silently never ran cannot pass).
    ``counters_max``: upper bounds on counters (e.g. ``oidc_relogin``).
    """
    slo = profile.get("slo", {})
    ops = summary["operations"]
    checks = []
    error_limits = dict(slo.get("max_error_rate", {}))
    wildcard = error_limits.pop("*", None)
    if wildcard is not None:
        # ``"*"`` covers every operation the run performed that has no
        # explicit limit of its own.
        for op in ops:
            error_limits.setdefault(op, wildcard)
    for op, limit in sorted(error_limits.items()):
        actual = ops.get(op, {}).get("error_rate", 0.0)
        checks.append({"check": f"{op}.error_rate <= {limit}", "actual": actual, "pass": actual <= limit})
    for op, limit in slo.get("p95_ms", {}).items():
        actual = ops.get(op, {}).get("p95_ms")
        checks.append({"check": f"{op}.p95_ms <= {limit}", "actual": actual,
                       "pass": actual is not None and actual <= limit})
    for op, minimum in slo.get("min_count", {}).items():
        actual = ops.get(op, {}).get("ok", 0)
        checks.append({"check": f"{op}.ok >= {minimum}", "actual": actual, "pass": actual >= minimum})
    for name, limit in slo.get("counters_max", {}).items():
        actual = summary["counters"].get(name, 0)
        checks.append({"check": f"{name} <= {limit}", "actual": actual, "pass": actual <= limit})
    return checks


def build_report(profile: Dict[str, Any], env, metrics: Metrics, scale: float) -> Dict[str, Any]:
    summary = metrics.summary()
    checks = check_slo(profile, summary)
    return {
        "schema": "atlas-load-report/1",
        "profile": profile.get("name"),
        "description": profile.get("description", ""),
        "seed": profile.get("seed"),
        "scale": scale,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": _git_revision(),
        "host": {"cpus": os.cpu_count(), "python": platform.python_version(), "platform": platform.platform()},
        "environment": env.describe(),
        "resource_sampling": "local /proc + Redis INFO + pg_stat_activity" if env.local
        else "unavailable for --target (client-side gauges only)",
        **summary,
        "mock_stats": env.mock_stats(),
        "final_sample": metrics.timeline[-1] if metrics.timeline else {},
        "slo": checks,
        "passed": all(c["pass"] for c in checks),
        "resolved_profile": profile,
        "timeline": metrics.timeline,
    }


def write_outputs(report: Dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
    rows = [_flatten(sample) for sample in report["timeline"]]
    if rows:
        fields = sorted({k for row in rows for k in row}, key=lambda k: (k != "t", k))
        with open(out_dir / "timeline.csv", "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    (out_dir / "summary.txt").write_text(render_text(report))


def _fmt(value: Any) -> str:
    return "-" if value is None else str(value)


def render_text(report: Dict[str, Any]) -> str:
    lines = [
        f"Atlas load profile: {report['profile']}  (seed {report['seed']}, scale {report['scale']}, "
        f"git {report['git']})",
        f"Environment: {json.dumps(report['environment'].get('replicas') or report['environment'].get('targets'))}"
        f"  auth={report['environment'].get('auth')}  duration {report['duration_s']}s",
        "",
        f"{'operation':<20}{'count':>7}{'ok':>7}{'err':>6}{'p50 ms':>10}{'p95 ms':>10}{'p99 ms':>10}{'max ms':>10}",
    ]
    for op, s in report["operations"].items():
        lines.append(f"{op:<20}{s['count']:>7}{s['ok']:>7}{s['errors']:>6}{_fmt(s['p50_ms']):>10}"
                     f"{_fmt(s['p95_ms']):>10}{_fmt(s['p99_ms']):>10}{_fmt(s['max_ms']):>10}")
        if s["error_classes"]:
            lines.append(f"{'':<20}errors: {s['error_classes']}")
    lines += ["", "Phases:"]
    for phase in report["phases"]:
        extra = {k: v for k, v in phase.items() if k not in ("name", "kind", "seconds")}
        lines.append(f"  {phase['name']:<24}{phase['seconds']:>8}s  {extra}")
    if report["counters"]:
        lines += ["", f"Counters: {report['counters']}"]
    peaks = report["peaks"]
    wanted = [k for k in peaks if k.startswith(("client.", "server_active_api_runs."))
              or k.endswith(("server_conns", "rss_mb", "cpu_pct", "fds", "threads", "connected_clients",
                             "used_memory_mb", "connections"))]
    lines += ["", "Peaks (max over samples):"]
    lines += [f"  {k:<44}{peaks[k]}" for k in wanted]
    if report["mock_stats"]:
        lines += ["", f"Mocks: {report['mock_stats']}"]
    if report["error_samples"]:
        lines += ["", "Error samples:"] + [f"  {e['op']}: {e['error']} {e['detail']}" for e in report["error_samples"][:10]]
    if report["slo"]:
        lines += ["", "SLO checks:"]
        lines += [f"  [{'PASS' if c['pass'] else 'FAIL'}] {c['check']} (actual {c['actual']})" for c in report["slo"]]
    lines += ["", f"RESULT: {'PASS' if report['passed'] else 'FAIL'}"]
    return "\n".join(lines) + "\n"
