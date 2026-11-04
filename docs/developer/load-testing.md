# Load and Scale Profiles

Last updated: 2026-11-04

`bash test/load_tests.sh <profile>` runs a reproducible load profile against
real Atlas replicas and reports latency percentiles, errors, active runs,
connection counts and resource usage. No real LLM is involved. It is the load
half of issue #1129; the correctness half for distributed run coordination is
the multi-replica harness (`test/multireplica`, PR #1146), and the load runner
starts its replicas with that harness, the same way.

It is **not** part of `test/atlas_tests.sh` or CI. Run it when you change
something that affects capacity (connection handling, the session store, run
admission, the agent loop's Redis/Postgres traffic) or when validating a
deployment.

## What a local run starts

| Piece | Source | Notes |
|-------|--------|-------|
| Redis + PostgreSQL | `test/multireplica/harness/infra.py` | throwaway podman containers, or `ATLAS_TEST_REDIS_URL` / `ATLAS_TEST_POSTGRES_URL` |
| N Atlas replicas | `test/multireplica/harness/replica.py` | `uvicorn atlas.main:app`, own port and process group, environment built from scratch, `SESSION_REPOSITORY_TYPE=redis`, chat history in Postgres |
| Mock LLM | `test/loadtest/atlas_load/load_mock_llm.py` | the harness's scripted model, plus request counts and (OIDC profiles) JWT verification of the delegated token |
| Mock IdP (OIDC profiles) | `test/loadtest/atlas_load/mock_oidc.py` | Authorization Code + PKCE with auto-login, refresh, RFC 8693 token exchange, real RSA/JWKS; adapted from [atlas-load-test-helper](https://github.com/garland3/atlas-load-test-helper) |
| Virtual users | `test/loadtest/atlas_load/clients.py` | asyncio WebSocket + REST clients, one process |

Replicas run with `RATE_LIMIT_RPM` raised, because every virtual user comes
from 127.0.0.1 and would otherwise trip the per-IP limiter. Profiles measure
Atlas, not that limiter.

## Running

```bash
bash test/load_tests.sh --list                       # bundled profiles
bash test/load_tests.sh smoke                        # ~1 minute incl. startup
bash test/load_tests.sh reconnect-storm --out /tmp/storm
bash test/load_tests.sh ws-capacity --scale 4        # 4000 sockets
bash test/load_tests.sh agent-runs --replicas 3
bash test/load_tests.sh my-profile.json --seed 7 --keep
```

| Option | Effect |
|--------|--------|
| `--scale F` | multiply `connections`, `users` and `runs` (durations unchanged) |
| `--seed N` | override the profile seed |
| `--replicas N` | override the replica count |
| `--out DIR` | write `report.json`, `timeline.csv`, `summary.txt` there (default: a temp dir, printed at the end) |
| `--keep` | keep the work dir with replica and mock logs |
| `--no-fail` | exit 0 even if an SLO check fails |
| `--target URL` | drive a running deployment instead (repeatable; see below) |

The exit status is 0 when every SLO check in the profile passed. The runner
raises its own open-file limit to the hard limit; for thousands of sockets
make sure `ulimit -Hn` allows roughly two descriptors per connection.

Tests of the tooling itself:

```bash
python -m pytest test/loadtest/test_load_tooling.py   # unit, no infrastructure, ~1s
ATLAS_LOADTEST_REQUIRED=1 python -m pytest test/loadtest/test_load_smoke.py   # runs `smoke`
```

(from the repo root with `PYTHONPATH=$(pwd)`; `test/pr-validation/` has a
script that runs both.)

## Bundled profiles

| Profile | What it exercises |
|---------|-------------------|
| `smoke` | every load path once at small size: 20 sockets over 2 replicas, chat and agent turns, 6 REST agent runs, a client-side reconnect storm to the other replica |
| `oidc-delegated` | header auth **off**; every user logs in through `/auth/oidc/login` against the mock IdP; chat on a model with `api_key_source: "delegated"`, so each LLM call carries an RFC 8693-exchanged token that the mock LLM verifies; a reconnect to the other replica |
| `reconnect-storm` | 300 sockets that have chatted; replica A is SIGKILLed and its sockets all reconnect to B at once with session resume; A is restarted; then half of all sockets drop and reconnect to the other replica with 2s jitter |
| `ws-capacity` | 1000 sockets (250 users, 4 tabs each) ramped over 30s, held idle, 10% chatting; scale up to find the limit |
| `agent-runs` | 120 WebSocket agent turns (3 tool steps of 2s, approvals answered) concurrently with 40 REST agent-runs API runs |

### Profile format

A profile is a JSON file (`test/loadtest/profiles/<name>.json`, or any path):

```json
{
  "description": "what it measures",
  "seed": 1129,
  "replicas": 2,
  "auth": "header",
  "users": 10,
  "sample_interval": 1.0,
  "max_runs_per_user": 5,
  "env": {"ANY_ATLAS_SETTING": "value"},
  "phases": [ ... ],
  "slo": { ... }
}
```

Socket `i` belongs to user `i % users` and starts on replica `i % replicas`,
and every random choice uses `random.Random(seed)`, so a profile, seed and
scale always produce the same plan. Timings are what vary between runs.

Phases run in order over one shared pool of sockets:

| Kind | Keys | Does |
|------|------|------|
| `connect` | `connections`, `users`, `ramp_seconds`, `concurrency` | open sockets (and log in first for `auth: "oidc"`) |
| `chat` | `turns`, `fraction`, `agent`, `model`, `directive`, `concurrency`, `timeout`, `new_conversation` | each chosen socket runs `turns` turns in sequence, continuing its conversation like the browser does |
| `api_runs` | `runs`, `users`, `concurrency`, `directive`, `timeout`, `model` | launch runs through `POST /api/agent-runs` (round robin over replicas) and poll each to a terminal state |
| `hold` | `seconds` | wait; counts sockets that died while idle |
| `reconnect_storm` | `trigger` (`client_drop` / `replica_kill`), `replica`, `fraction`, `to` (`other` / `same`), `jitter_ms`, `concurrency`, `heal` | drop sockets and reconnect them with `resume_session`; `replica_kill` SIGKILLs a local replica and restarts it afterwards |
| `parallel` | `phases` | run nested `connect`/`chat`/`api_runs`/`hold` phases at the same time |
| `disconnect` | | close every socket |

`directive` drives the scripted model (see `test/multireplica/harness/mock_llm.py`):
`{"tool": "sleep", "sleep": 2, "steps": 3, "words": 40, "stream_ms": 10}`
makes each turn call `atlas_sleep` three times for 2s, then stream 40 words at
10 ms each. `{}` gets an immediate echo; `"tool": "none"` streams an answer
with no tool call. Virtual users answer `tool_approval_request` frames with an
approval, as a user clicking Approve would (counted as `tool_approvals_sent`).

`slo` decides the exit status:

```json
"slo": {
  "max_error_rate": {"*": 0, "ws_reconnect": 0.01},
  "p95_ms": {"ws_connect": 2000},
  "min_count": {"chat_turn": 100},
  "counters_max": {"oidc_relogin": 0}
}
```

`"*"` applies to every operation without its own limit. `p95_ms` and
`min_count` fail for an operation that never ran, so a profile cannot pass by
doing nothing.

## Against a running deployment

```bash
bash test/load_tests.sh ws-capacity --target https://atlas.staging.example \
    --service-key "$AGENT_RUNS_API_KEY" --proxy-secret "$PROXY_SECRET"
```

`--target` may be one load balancer or each replica (repeat the flag; sockets
are spread round robin). Header auth sends `X-User-Email` (plus `X-Proxy-Secret`
when given), so the target must trust the client the way it trusts its proxy;
use an isolated staging deployment. The bundled mock IdP only redirects back to `/auth/oidc/callback` on loopback hosts; set `OIDC_REDIRECT_HOSTS` (comma-separated) when you run it for another host. `auth: "oidc"` profiles work against a
deployment whose IdP auto-logs-in `loaduser-NNNN` names from `login_hint`
(the mock IdP here, or the helper repo's image). `api_runs` phases need the
deployment's `AGENT_RUNS_API_KEY`; `replica_kill` is local only. Server-side
resource sampling is not available remotely: the report says so and keeps the
client-side gauges.

## Reading the report

`summary.txt` (also printed) from a `smoke` run on a developer workstation
(abridged):

```
operation             count     ok   err    p50 ms    p95 ms    p99 ms    max ms
api_run_launch            6      6     0      34.8      58.1      61.6      62.5
api_run_total             6      6     0    1554.7    1581.5    1585.5    1586.5
chat_first_token         30     30     0     209.6    2223.4    2230.6    2231.6
chat_turn                30     30     0     231.9    2306.7    2310.4    2310.6
ws_connect               20     20     0       1.9      10.2      16.6      18.2
ws_reconnect             20     20     0      17.9      20.6      21.2      21.4

Phases:
  agent-turns                 2.31s  {'connections': 10, 'turns_each': 1, 'agent': True, ..., 'counters': {'tool_approvals_sent': 20}}
  storm                       0.02s  {'trigger': 'client_drop', 'affected': 20, 'reconnected': 20, 'recovery_s': 0.02,
                                      'counters': {'session_not_resumed_no_turn': 5, 'session_not_resumed': 1, 'session_resumed': 14}}

Peaks (max over samples):
  client.active_runs                          10
  client.open_connections                     20
  server.postgres.connections                 4
  server.redis.connected_clients              23
  server.replicas.A.cpu_pct                   22.3
  server.replicas.A.rss_mb                    310.8
  server.replicas.A.server_conns              16
  server_active_api_runs.A                    3

RESULT: PASS
```

The `chat_turn` p50 is a plain echo turn; the p95 is an agent turn, which
includes two 1s `atlas_sleep` calls. `server_conns` counts every TCP
connection the replica holds (sockets plus the REST polling), so it can exceed
the client's socket count.

**Operations** (latency percentiles are over successful attempts; failures
are counted in `err` with their class, e.g. `http_429`, `TimeoutError`,
`TurnError`):

| Operation | Measures |
|-----------|----------|
| `ws_connect` / `ws_reconnect` | WebSocket upgrade until the `connection_session` frame (includes an OIDC relogin when one was needed) |
| `oidc_login` | `/auth/oidc/login` -> IdP `/authorize` -> `/auth/oidc/callback` |
| `chat_first_token` | chat frame sent until the first `token_stream` frame |
| `chat_turn` | chat frame sent until `conversation_saved` (an `error` frame is a `TurnError`) |
| `api_run_launch` | `POST /api/agent-runs` until 202 |
| `api_run_total` | launch until the run is terminal; anything but `completed` is an error (`run_failed`, `run_timeout`) |
| `replica_restart` | after a `replica_kill`, restarting the replica until healthy |

Agent turns include their tool time: with `sleep=1 steps=2` a turn can't be
faster than 2s. Compare runs of the same profile, not turns of different
profiles.

**Counters**

| Counter | Meaning |
|---------|---------|
| `session_resumed` | a reconnect named its previous session and the server took it over |
| `session_not_resumed` | the server started a fresh session: usually the old socket's session was still marked active (its teardown had not run), or its replica died before teardown |
| `session_not_resumed_no_turn` | the socket never chatted, so it had no session to resume (Atlas creates it at the first turn) |
| `oidc_relogin` | a replica refused the user's session cookie and the user logged in again there |
| `tool_approvals_sent` | approval requests answered |
| `approval_unknown` | the server answered an approval with `unknown_tool_approval` (it does not end the turn, so the client carries on) |
| `connections_closed_by_server` | sockets that closed without the client closing or dropping them, in any phase (a `replica_kill` storm's own victims are excluded); every bundled profile requires 0 |
| `connections_lost_while_idle` | sockets the server closed during a `hold` |

Each phase line in the summary carries its own counter deltas, so you can see
which storm produced which outcome.

**Gauges** are sampled every `sample_interval` and kept in `timeline.csv`;
`Peaks` is the maximum of each:

| Gauge | Source |
|-------|--------|
| `client.open_connections`, `client.in_flight_turns`, `client.in_flight_api_runs`, `client.active_runs` | the load client |
| `server_active_api_runs.<replica>` | `GET /api/agent-runs` on each replica: API runs that replica's registry holds as not terminal |
| `server.replicas.<r>.server_conns` | ESTABLISHED TCP connections on the replica's port, from `/proc/net/tcp*` (sockets the server holds) |
| `server.replicas.<r>.cpu_pct`, `rss_mb`, `fds`, `threads` | the replica's process group, from `/proc` (CPU is % of one core) |
| `server.redis.connected_clients`, `used_memory_mb` | Redis `INFO` (includes the sampler's own connection) |
| `server.postgres.connections`, `state_*` | `pg_stat_activity` for the run's database, excluding the sampler |
| `server.mock_llm.*` | the mock LLM process, to confirm it is not the bottleneck |

`mock_stats` reports what the mocks saw: LLM completions and how many carried a
valid or invalid delegated token; IdP authorizations, code redemptions and
token exchanges.

`report.json` holds all of the above plus the resolved profile, the git
revision, host CPU count and the full timeline (`schema: atlas-load-report/1`).

### What the bundled profiles measured

One run each on a developer workstation (2 replicas, local podman Redis and
Postgres, load client on the same host), 2026-11-04. These are reference
points to compare later runs against, not capacity guarantees: rerun on the
hardware you care about.

| Profile | Result |
|---------|--------|
| `smoke` | 0 errors; `ws_connect` p95 10 ms, `ws_reconnect` p95 21 ms |
| `ws-capacity` | 1000 sockets, 0 errors, none lost while idle; `ws_connect` p95 2.9 ms; per replica 501 server connections, 583 fds, RSS 311 MB idle -> 397 MB (about 0.17 MB per socket); Redis clients and Postgres connections unchanged by socket count (41 and 2) |
| `reconnect-storm` | 300 reconnects, 0 errors, `ws_reconnect` p95 160 ms; killed replica restarted in 4.2 s; 900 chat turns, 0 errors |
| `agent-runs` | 120 agent turns + 40 API runs in parallel, 0 errors, 100 active runs at peak; one replica at about 100% CPU, so turns of 6 s tool time took 20 s at p50 and 41 s at p95: one process per replica is CPU-bound well before Redis or Postgres are busy |
| `oidc-delegated` | 40 logins (p95 296 ms), 40 token exchanges, 140 model calls all with a verified delegated JWT, 0 errors |

### Behaviour the bundled profiles show

- **OIDC sessions are per process.** In `oidc-delegated`, every socket that
  moves to the other replica is refused and logs in again (`oidc_relogin`
  equals the number of moved sockets), and the delegated-token cache is per
  process too: the IdP sees one token exchange per user per replica. This is
  the documented behaviour (see [OIDC authentication](../admin/oidc-authentication.md),
  "Sessions are per-process"); the profile makes its cost measurable.
- **A crashed replica's sessions are not resumable.** In `reconnect-storm`,
  sockets from the SIGKILLed replica reconnect to the survivor without errors
  but get fresh sessions (`session_not_resumed` in the `kill-A` phase),
  because the dead replica never marked its sessions inactive. Sockets that
  drop with jitter while their replica is alive mostly resume.
- **An instant reconnect can beat the old socket's teardown.** With no jitter,
  some reconnects arrive before the server noticed the old socket closing and
  get a fresh session; `jitter_ms` spreads a storm the way real clients back
  off.
- **Approval answers under load.** In `agent-runs`, a few approval responses
  (`approval_unknown`, 6 of 360) got the server's `unknown_tool_approval`
  frame although the turns then completed; worth watching when changing the
  approval path.
- **Clients should continue their conversation.** Before the virtual users
  sent `conversation_id` (the browser always does), the first turn after a
  cross-replica resume in `oidc-delegated` was refused at save time: the
  repository would not let a two-message write replace the six stored
  messages. The turn streamed but was not persisted, and the client got an
  `error` frame, not silent loss.
