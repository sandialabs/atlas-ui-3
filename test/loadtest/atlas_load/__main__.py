"""CLI: ``python -m atlas_load --profile smoke`` (run from ``test/loadtest``).

Prefer ``bash test/load_tests.sh <profile> [options]``, which activates the
venv, pins ``PYTHONPATH`` to this checkout and sets the working directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import resource
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict

from .metrics import Metrics
from .report import build_report, render_text, write_outputs

PROFILE_DIR = Path(__file__).resolve().parents[1] / "profiles"
SCALED_KEYS = ("connections", "users", "runs")


def load_profile(name_or_path: str) -> Dict[str, Any]:
    path = Path(name_or_path)
    if not path.suffix:
        path = PROFILE_DIR / f"{name_or_path}.json"
    profile = json.loads(path.read_text())
    profile.setdefault("name", path.stem)
    return profile


def apply_scale(profile: Dict[str, Any], scale: float) -> Dict[str, Any]:
    """Multiply connection, user and run counts (never below 1), and the
    SLO's ``min_count`` floors with them, since those count the same work."""
    if scale == 1.0:
        return profile
    profile = json.loads(json.dumps(profile))
    if "users" in profile:
        profile["users"] = max(1, round(profile["users"] * scale))
    minimums = profile.get("slo", {}).get("min_count", {})
    for op, floor in minimums.items():
        minimums[op] = max(1, int(floor * scale))
    phases = list(profile["phases"])
    while phases:
        phase = phases.pop()
        phases.extend(phase.get("phases", []))  # nested parallel phases
        for key in SCALED_KEYS:
            if key in phase:
                phase[key] = max(1, round(phase[key] * scale))
    return profile


def raise_fd_limit() -> int:
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if soft < hard:
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="atlas_load", description=__doc__)
    parser.add_argument("--profile", default="smoke", help="profile name in test/loadtest/profiles or a JSON path")
    parser.add_argument("--scale", type=float, default=1.0, help="multiply connections/users/runs")
    parser.add_argument("--seed", type=int, help="override the profile seed")
    parser.add_argument("--replicas", type=int, help="override the profile's replica count (local runs)")
    parser.add_argument("--out", type=Path, help="directory for report.json, timeline.csv, summary.txt")
    parser.add_argument("--target", action="append", default=[],
                        help="URL of a running Atlas replica or load balancer (repeatable); skips local startup")
    parser.add_argument("--service-key", help="AGENT_RUNS_API_KEY of the target, for api_runs phases")
    parser.add_argument("--proxy-secret", help="X-Proxy-Secret to send with header auth (--target)")
    parser.add_argument("--keep", action="store_true", help="keep the local work dir (replica logs)")
    parser.add_argument("--no-fail", action="store_true", help="exit 0 even when an SLO check fails")
    parser.add_argument("--list", action="store_true", help="list the bundled profiles and exit")
    return parser.parse_args(argv)


async def _run(profile, env, metrics, proxy_secret) -> None:
    from .resources import ResourceSampler
    from .runner import Runner

    runner = Runner(profile, env, metrics, proxy_secret=proxy_secret)
    if env.local:
        extra = {}
        if env.llm and env.llm.proc:
            extra["mock_llm"] = env.llm.proc.pid
        runner.sampler = ResourceSampler(env.replicas, env.infra.redis_url, env.infra.postgres_url, extra)
    try:
        await runner.run()
    finally:
        if runner.sampler is not None:
            runner.sampler.close()


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.list:
        for path in sorted(PROFILE_DIR.glob("*.json")):
            print(f"{path.stem:<20}{json.loads(path.read_text()).get('description', '')}")
        return 0
    profile = apply_scale(load_profile(args.profile), args.scale)
    if args.seed is not None:
        profile["seed"] = args.seed
    if args.replicas is not None:
        profile["replicas"] = args.replicas
    fd_limit = raise_fd_limit()
    out_dir = args.out or Path(tempfile.gettempdir()) / f"atlas-load-{profile['name']}-{int(time.time())}"

    from .environment import LocalEnvironment, RemoteTarget

    workdir = Path(tempfile.mkdtemp(prefix="atlas-load-"))
    if args.target:
        env = RemoteTarget(profile, args.target, args.service_key)
    else:
        env = LocalEnvironment(profile, workdir)
    print(f"[loadtest] profile {profile['name']} (fd limit {fd_limit}); work dir {workdir}", flush=True)
    try:
        started = time.monotonic()
        env.start()
        print(f"[loadtest] environment up in {time.monotonic() - started:.1f}s: {env.urls}", flush=True)
        metrics = Metrics()  # measure the profile, not the startup
        asyncio.run(_run(profile, env, metrics, args.proxy_secret))
        report = build_report(profile, env, metrics, args.scale)
    finally:
        env.close()
        if not args.keep:
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)
    write_outputs(report, out_dir)
    sys.stdout.write(render_text(report))
    print(f"[loadtest] report: {out_dir / 'report.json'}")
    return 0 if report["passed"] or args.no_fail else 1


if __name__ == "__main__":
    sys.exit(main())
