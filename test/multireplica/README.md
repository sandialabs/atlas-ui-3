# Multi-replica harness

Last updated: 2026-11-04

Pytest suite that runs **two independent Atlas API processes** (replicas A and
B) against one shared Redis and one shared PostgreSQL, the way a horizontally
scaled deployment runs, and sends each request to a replica the test chooses.
No load balancer, no Kubernetes, no docker compose. Built for issue #1128
(distributed agent-run coordination).

## Running it

```bash
bash test/multireplica_tests.sh                 # whole suite, ~2 minutes
bash test/multireplica_tests.sh -k baseline     # extra args go to pytest
ATLAS_MULTIREPLICA_KEEP=1 bash test/multireplica_tests.sh   # keep replica logs
```

Requirements:

- **podman**. Redis (`redis:7-alpine`) and PostgreSQL (`postgres:16-alpine`)
  are started as throwaway containers on random local ports and removed at the
  end. Postgres keeps its data on tmpfs.
- Or point at servers you already run with `ATLAS_TEST_REDIS_URL` and
  `ATLAS_TEST_POSTGRES_URL` (an admin URL; the suite creates and drops its own
  database and uses a random Redis key prefix).

If neither podman nor the URLs are available the suite is skipped when you
run pytest directly; through `test/multireplica_tests.sh` it refuses to start.
Any other infrastructure problem (a broken podman, an image pull failure, an
unreachable URL, a readiness timeout) **fails** the run rather than skipping
it, so the gate can never pass with zero coverage.

`bash test/run_pr_validation.sh 1146` runs the suite and checks its outcome
from the parsed pytest counts (no failures, errors, skips or XPASSes, and
exactly `EXPECTED_GAPS` expected failures), that every process group and
container the run recorded in its manifest (`ATLAS_MULTIREPLICA_MANIFEST`) is
gone, and that `atlas/tests` does not collect the harness.

It is **not** part of `test/atlas_tests.sh` or CI. AGENTS.md ("Horizontal
Scaling") lists the code paths whose PRs must run it.

## What is in a run

| Piece | Where | Notes |
|-------|-------|-------|
| Redis + Postgres | `harness/infra.py` | podman, one pair per session; `freeze_container` / `thaw_container` simulate an outage |
| Scripted model | `harness/mock_llm.py` | OpenAI-compatible; directives in the message drive tool calls |
| Side-effect tool | `harness/side_effect_mcp.py`, `harness/side_effect_keys.py` | stdio MCP server; every execution is appended to a shared file so tests can count them across replicas. Writer and reader share one key-to-filename function; the reader skips torn or undecodable lines |
| Replicas | `harness/replica.py` | `uvicorn atlas.main:app` per replica, own port, process group, log and `.env`; environment built from scratch, `ENVIRONMENT=development` (required by `DEBUG_MODE`) and `PYTHON_DOTENV_DISABLED=1` so litellm's import-time `load_dotenv()` cannot pull in a `.env` found above the venv |
| Client helpers | `harness/cluster.py` | agent-runs REST API (`launch`, `run_status`, `cancel`, `wait_terminal`), conversations, side-effect counts, `health`, `coordination` (as `ADMIN_USERS` identity `mr-admin@example.com`) |
| Browser tab | `harness/ws_client.py` | chat WebSocket to one replica; `auto_approve` answers named tools the way the UI's auto-approve does |

Replicas start once per session. The `cluster` fixture restarts or resumes any
replica a test killed or paused, so tests can crash replicas freely
(`replica.kill()` = SIGKILL, `pause()`/`resume()` = SIGSTOP/SIGCONT,
`stop()` = SIGTERM). On a failure the tail of every replica's log is attached
to the report.

### Scripted model directives

`directive(label, tool="sleep"|"side_effect", sleep=N, steps=K)` builds a
message that makes the mock call the chosen tool K times (N seconds each), then
answer. A message without directives is answered `ECHO: <message>`.

## The tests

- `test_harness_baseline.py`: must pass on main. Durable state shared through
  Postgres, live sessions shared through Redis (fails if the replicas fall
  back to the in-memory session store), the side-effect counter, crash without
  replay, pause/resume.
- `test_run_coordination_gaps.py`: the #1128 acceptance criteria. The ones not
  met yet are marked `xfail(strict=True, raises=GapNotClosed)`; the per-user
  cap and atomic-admission criteria pass since the Redis run coordinator. Only the final criterion raises
  `GapNotClosed` (via `expect(...)`); preconditions use plain `assert`, so a
  harness fault is a real failure, never a silent xfail. When a PR meets a
  criterion the test XPASSes, strict mode fails the run, and the PR removes the
  marker.
- `test_cluster_admission.py`: cluster-wide admission (#1128). A conversation
  running on A is refused on B (over WebSocket) and freed when the run ends,
  and refused admissions leave no slot behind.
- `test_run_leases.py`: ownership leases and fencing (#1128) checked in the
  shared Redis itself: a run longer than its lease survives on heartbeats, a
  SIGKILLed owner's run is interrupted with its completed tool rows kept and
  the fencing generation bumped, and a SIGSTOPped owner that resumes after
  being declared lost writes nothing more. Replicas run with
  `RUN_LEASE_SECONDS=6` (`LEASE_TTL` in `harness/cluster.py`).
- `test_run_control_routing.py`: stop, steer and approval of a run on A
  sent through B (WebSocket and REST), with duplicate and retried commands,
  another user's commands, and a frozen owner. Tests that need a browser use
  `harness/ws_client.py`; `approved_write` is the approval-gated twin of the
  side-effect tool.
- `test_known_bugs.py`: bugs the harness found that are outside #1128, in the
  same strict-xfail style. A fixed bug keeps its test as a regression test
  (the REST-only cap bug, fixed with #1128).
- `test_failure_exercises.py`: issue #1129 observability under failure, must
  pass on main. Per-replica `/admin/coordination`, a graceful stop (SIGTERM)
  reported as interrupted (`end_reason=replica_shutdown`) on the other
  replica without replaying a side effect, and Redis / PostgreSQL outages
  (`podman pause` of the harness container) visible in `/api/health`,
  `/admin/coordination` and `[COORD]` log lines, then recovered. The outage
  tests skip when the suite uses supplied `ATLAS_TEST_*_URL` servers.
  Expected behavior: `docs/admin/multi-replica-observability.md`.

## Writing a test

```python
def test_something(cluster):
    user = cluster.new_user()                          # unique per test
    handle = cluster.launch_ok(cluster.a, user, directive("x", sleep=5))
    cluster.wait_status(cluster.a, handle["run_id"], "running")
    status, body = cluster.run_status(cluster.b, handle["run_id"])
    ...
    cluster.cancel_everywhere(handle["run_id"])        # leave no long runs behind
```

Use a fresh user per test: runs count against a per-user cap
(`RUN_CAP`, 5) and replicas are shared across the session.

## Running in CI later

Nothing here depends on the developer machine: GitHub's `ubuntu-latest`
runners ship podman, so a workflow job (manual or path-filtered) can run
`bash test/multireplica_tests.sh` as is.
