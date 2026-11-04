# Multi-Replica Observability and Failure Exercises

Last updated: 2026-11-04

How to see what each Atlas API replica is doing with agent runs and shared
dependencies, how to break a multi-replica deployment on purpose, and what
you should see when you do (issue #1129). For the deployment model itself see
[Session Store](session-store.md) and [Chat History](chat-history.md); for
run semantics see [Parallel Conversation Runs](parallel-conversation-runs.md).

## What is per replica

A run executes on the replica that admitted it (its **owner**). The run
registry is process-local today; cross-replica run discovery, routing and
lease-based failover are #1128. Everything below therefore describes **the
replica that answers**: behind a load balancer, query each pod directly, and
let your metrics backend sum or compare the pods.

Each replica names itself with the first of `ATLAS_REPLICA_ID`, `POD_NAME`,
`HOSTNAME` (the pod name on Kubernetes/OpenShift), or the host name, plus a
random `instance` id that changes on every process start.

## Run states an operator can distinguish

| State | Where it shows | Meaning |
|-------|----------------|---------|
| `queued` | `runs.counts.queued`, `atlas_runs{status="queued"}` | Admitted, task not started yet (normally milliseconds) |
| `running` | `runs.counts.running` | Executing on this replica |
| `waiting_for_input` | `runs.counts.waiting_for_input` | Paused on a tool approval or elicitation; still counts against the user's cap |
| completed | `atlas_runs_finished_total{outcome="completed"}` | Finished normally |
| cancelled | `outcome="cancelled", reason="user_cancelled"` | A user (or a parent run) stopped it |
| **failed** | `outcome="failed"`, `reason="error"` or `"wall_clock_limit"` | The run's own error, or `MAX_RUN_WALL_CLOCK_SECONDS` |
| **interrupted** | `outcome="interrupted"`, `reason="replica_shutdown"` | The replica running it shut down (SIGTERM: rollout, scale-in, node drain) |

`detached` (`runs.counts.detached`) marks live runs whose browser socket has
gone; they keep executing.

On the public run APIs an interrupted run has `status: "failed"` and
`end_reason: "replica_shutdown"` (`GET /api/agent-runs/{id}` on the owner, the
WebSocket `run_status` frame, and the conversation's `execution` projection
that **every** replica serves from Redis). Completed steps stay in history and
no tool is re-run: there is no automatic replay of side effects.

`end_reason` values `owner_lost` and `fenced` are reserved for #1128 (another
replica declaring a crashed or frozen owner lost); they also map to
`interrupted`.

## Endpoints

### `GET /api/health` (unauthenticated)

`status` is `healthy` or `degraded`; HTTP 200 either way, so a readiness probe
does not pull every replica during a shared outage. Degraded when:

- `session_store.status` is `unavailable` (Redis ping failed, 1 s bound), or
- `checks.chat_history` is `failed` (database failed at startup), or
- `checks.chat_history_db` is `unavailable` -- a live `SELECT 1` (1.5 s bound,
  single-flight, cached 2 s). Before #1129 a database outage after startup was
  invisible here.

Only reachability is reported; error detail stays in the logs.

### `GET /admin/coordination` (admin)

JSON for this replica:

```json
{
  "replica": {"replica_id": "atlas-7c9d-x2", "instance_id": "1f0e...", "pid": 7, "version": "0.11.0", "uptime_s": 812.4},
  "status": "ok",
  "runs": {
    "counts": {"queued": 0, "running": 2, "waiting_for_input": 1, "completed": 14,
               "failed": 1, "cancelled": 3, "detached": 1, "interrupted": 0},
    "max_concurrent_per_user": 5,
    "items": [{"run_id": "...", "status": "running", "detached": true, "age_s": 95.2,
               "outcome": null, "end_reason": null, "waiting_on": null, "depth": 0, "ended_at": null}]
  },
  "dependencies": {
    "redis": {"status": "ok", "latency_ms": 0.4, "state": {"status": "ok", "consecutive_failures": 0, "last_error": null, "...": "..."}},
    "postgres": {"status": "ok", "latency_ms": 1.2, "state": {"...": "..."}}
  },
  "counters": [{"name": "atlas_runs_finished_total", "labels": {"outcome": "completed", "reason": "completed"}, "value": 14}]
}
```

`?include_terminal=true` also lists the terminal runs still retained (30
minutes). The database probe result is reused for up to 2 s, so frequent
polling costs at most one `SELECT 1` per 2 s; `?refresh=true` forces a new one. Runs are listed by id only: no user, conversation or prompt.

### `GET /admin/coordination/metrics` (admin)

The same numbers in Prometheus text format, every series labelled
`replica="<id>"`:

| Metric | Type | Labels |
|--------|------|--------|
| `atlas_runs` | gauge | `status` |
| `atlas_runs_detached` | gauge | |
| `atlas_runs_interrupted_retained` | gauge | |
| `atlas_run_oldest_active_age_seconds` | gauge | |
| `atlas_runs_admitted_total` | counter | `kind` (`top_level`, `launched`) |
| `atlas_runs_rejected_total` | counter | `reason` (`user_concurrency_limit`, `conversation_busy`) |
| `atlas_runs_finished_total` | counter | `outcome`, `reason` |
| `atlas_run_transitions_total` | counter | `from_status`, `to_status` |
| `atlas_run_duration_seconds` | summary (`_sum`, `_count`) | `outcome` |
| `atlas_dependency_up` | gauge | `dependency` (`redis`, `postgres`) |
| `atlas_dependency_errors_total` | counter | `dependency`, `op` |
| `atlas_projection_failures_total` | counter | `op` |
| `atlas_conversation_persist_failures_total` | counter | `error` |
| `atlas_coordination_events_total` | counter | `event` |
| `atlas_replica_info`, `atlas_replica_start_time_seconds` | gauge | `instance`, `version` |

The endpoint sits behind admin authorization like the rest of `/admin`. To
scrape it, send the admin identity your proxy injects (the scraper must reach
the pod through a path that sets it), or collect the `[COORD]` logs instead.
Counters reset when the process restarts (normal Prometheus semantics; use
`rate()`/`increase()`).

Suggested alerts: `atlas_dependency_up == 0` for 1 minute;
`increase(atlas_runs_finished_total{outcome="interrupted"}[15m]) > 0` outside
a planned rollout; `increase(atlas_conversation_persist_failures_total[5m]) > 0`.

## `[COORD]` log lines

Every coordination event is one log line, `[COORD] event=<name> replica=<id>
instance=<id> key=value ...`; in `app.jsonl` the same fields are also JSON
keys (`extra_coord_event`, `extra_coord_replica`, ...). Fields are ids,
statuses, counts and exception class names, never user identity or content.

| Event | Level | When |
|-------|-------|------|
| `replica_started` | INFO | Startup finished; carries `session_store`, `max_runs_per_user` |
| `run_admitted` | INFO | A run was admitted (`run_id`, `kind`, `depth`) |
| `run_rejected` | WARNING | Admission refused (`reason`) |
| `run_finished` | INFO / WARNING if failed | Terminal (`status`, `outcome`, `reason`, `duration_s`, `detached`) |
| `run_interrupted` | WARNING | Terminal because the replica went away (`reason=replica_shutdown`) |
| `replica_shutdown` | INFO / WARNING if runs were interrupted | Shutdown began (`interrupted_runs`) |
| `dependency_down` | ERROR | First failure of an outage (`dependency`, `op`, `error`) |
| `dependency_still_down` | ERROR | At most every 30 s while it stays down (`failures`, `down_for_s`) |
| `dependency_recovered` | WARNING | First success after an outage (`down_for_s`, `failures`) |
| `summary` | INFO | Every 30 s sweep when run counts or dependency states changed, and at least every 5 minutes |

The console handler shows WARNING and above, so `kubectl logs` carries
interruptions, failures and dependency transitions; INFO events are in
`APP_LOG_DIR/app.jsonl`.

Reserved for #1128 so coordination PRs log with stable names: `lease_acquired`,
`lease_renewed`, `lease_lost`, `orphan_detected`, `run_fenced`,
`command_routed`, `command_delivered`, `command_timed_out`. Emit them with
`coordination_metrics().record_event(CoordinationEvent.LEASE_LOST, run_id=..., ...)`
from `atlas/core/coordination_metrics.py`.

## Expected behavior under failure

| Failure | What each replica reports | What users see |
|---------|---------------------------|----------------|
| **Graceful pod stop** (delete, rollout, scale-in: SIGTERM) | The stopping pod logs `run_interrupted reason=replica_shutdown` per live run, then `replica_shutdown interrupted_runs=N`. It waits up to 5 s for the runs to unwind and for their terminal state to reach Redis. | The run is reported `failed` with `end_reason=replica_shutdown` on the run APIs and the shared projection; completed steps are kept and nothing is re-run. A browser reconnects to another replica. The UI does not yet render a specific "interrupted" notice. |
| **Crash** (SIGKILL, OOM, node loss) | Nothing from the dead pod. Its runs are **not** reported interrupted by anyone yet: that is #1128 (leases / orphan detection). | The conversation keeps what was saved; its live projection can keep saying `running` until it expires or the next run on that conversation replaces it. |
| **Redis outage** | `/api/health` `degraded`, `session_store.status=unavailable`; `dependency_down dependency=redis` once, `dependency_still_down` every 30 s, `dependency_recovered` after. `atlas_dependency_up{dependency="redis"} 0`. | Requests needing the shared session fail with `SESSION_STORE_UNAVAILABLE`; there is no silent per-replica fallback. Projection writes that fail are counted (`atlas_projection_failures_total`), not raised. |
| **PostgreSQL outage** | `checks.chat_history_db=unavailable`, `degraded`; `dependency_down dependency=postgres`; failed saves counted in `atlas_conversation_persist_failures_total`. | Conversation history cannot load or save until it returns. Saves attempted during the outage fail and are counted; check affected conversations after recovery. |
| **Controlled rollout** | Each old pod: `replica_shutdown` (+ `run_interrupted` per live run); each new pod: `replica_started`. | As graceful stop, for runs still live on a replaced pod. Size `maxUnavailable`/`maxSurge` and keep `terminationGracePeriodSeconds` >= 15. |

The detection time for an outage is one probe: the next `/api/health`, the
next `/admin/coordination` call, the 30 s sweep, or the first failed command,
whichever comes first.

## Failure-injection scripts

`scripts/failure-injection/` holds reusable exercises for a staging
Kubernetes/OpenShift deployment. Each snapshots `/api/health` (and
`/admin/coordination` when `ATLAS_ADMIN_EMAIL` is set) on every Atlas pod,
streams their `[COORD]` lines, injects the failure, restores, and prints a
summary plus the evidence directory. They never pass or fail on their own:
compare the evidence with the table above. All accept `--dry-run` (print the
commands), require an explicit `--namespace`, and ask for confirmation unless
`--yes`.

```bash
export ATLAS_SELECTOR=app=atlas ATLAS_ADMIN_EMAIL=ops-admin@example.org   # KUBECTL=oc on OpenShift

# Pod deletion: graceful, then crash-like
scripts/failure-injection/delete-pod.sh -n atlas-staging
scripts/failure-injection/delete-pod.sh -n atlas-staging --force

# Redis / PostgreSQL outage for 60 s (scales the workload to 0 and restores it)
scripts/failure-injection/dependency-outage.sh -n atlas-staging --dependency redis --target statefulset/redis
scripts/failure-injection/dependency-outage.sh -n atlas-staging --dependency postgres --target statefulset/postgres

# Controlled rollout
scripts/failure-injection/rollout.sh -n atlas-staging --deployment deploy/atlas

# Local podman containers instead of Kubernetes: freeze/thaw, poll replicas by URL
scripts/failure-injection/dependency-outage.sh --podman my-redis --dependency redis \
    --url http://127.0.0.1:8000 --url http://127.0.0.1:8001
```

Start a few long agent runs before a pod or rollout exercise (for example with
the [Agent Runs REST API](agent-runs-api.md)) so there is something to
interrupt. A Redis or PostgreSQL managed by an operator may undo the scale to
0; use the operator's own pause mechanism instead and observe with the same
scripts. Never point these at production.

## Automated checks

`bash test/multireplica_tests.sh -k failure_exercises` runs the same
exercises against two real replicas with podman Redis/PostgreSQL (see
`test/multireplica/README.md`): per-replica `/admin/coordination`, graceful
stop reported as interrupted on the other replica without replaying a side
effect, and Redis and PostgreSQL outages (container frozen) visible in health,
coordination status and logs, then recovered.
