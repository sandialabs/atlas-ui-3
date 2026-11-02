# Multi-replica harness

Last updated: 2026-11-01

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

It is **not** part of `test/atlas_tests.sh` or CI. AGENTS.md ("Horizontal
Scaling") lists the code paths whose PRs must run it.

## What is in a run

| Piece | Where | Notes |
|-------|-------|-------|
| Redis + Postgres | `harness/infra.py` | podman, one pair per session |
| Scripted model | `harness/mock_llm.py` | OpenAI-compatible; directives in the message drive tool calls |
| Side-effect tool | `harness/side_effect_mcp.py` | stdio MCP server; every execution is appended to a shared file so tests can count them across replicas |
| Replicas | `harness/replica.py` | `uvicorn atlas.main:app` per replica, own port, process group, log and `.env`; environment built from scratch |
| Client helpers | `harness/cluster.py` | agent-runs REST API (`launch`, `run_status`, `cancel`, `wait_terminal`), conversations, side-effect counts |

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

- `test_harness_baseline.py`: must pass on main. Shared durable state, the
  side-effect counter, crash without replay, pause/resume.
- `test_run_coordination_gaps.py`: the #1128 acceptance criteria, each marked
  `xfail(strict=True, raises=GapNotClosed)`. Only the final criterion raises
  `GapNotClosed` (via `expect(...)`); preconditions use plain `assert`, so a
  harness fault is a real failure, never a silent xfail. When a PR meets a
  criterion the test XPASSes, strict mode fails the run, and the PR removes the
  marker.
- `test_known_bugs.py`: bugs the harness found that are outside #1128, in the
  same strict-xfail style.

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
