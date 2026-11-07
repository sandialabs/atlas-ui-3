# Run coordination across replicas

Last updated: 2026-11-04

Issue #1128. How API replicas agree on which runs exist, and the Redis
contract that later lease, routing and failover work builds on.

## Who owns what

| State | Lives in | Why |
|-------|----------|-----|
| The live task, steering channel, MCP clients, replay buffers | The owning replica's `RunRegistry` (`atlas/application/chat/runs/registry.py`) | Python objects; they cannot move between processes |
| Admission (one run per conversation, `MAX_CONCURRENT_RUNS_PER_USER` per user), run ownership, leases, fencing generations | Redis, through the `RunCoordinator` | Every replica must see the same answer at the same moment |
| Transcripts, conversation metadata, anything that must survive a Redis restart | PostgreSQL (chat history) | Source of truth |

Redis coordinates live work and is not the source of truth. If Redis is
flushed, history is untouched. Runs already executing lose their claims:
their next renewal reports them lost, and the lease guard fences them (they
stop at their next tool call or projection write). The cluster-wide cap
counts only runs admitted after the flush.

## Admission

Every tracked run, from any transport (WebSocket turn, `atlas_launch`, the
agent-runs REST API), is admitted by `admit_run`
(`atlas/application/chat/runs/admission.py`):

1. Apply `MAX_CONCURRENT_RUNS_PER_USER` from settings to the registry. Every
   admission does this, and so does startup, so a replica that has only served
   REST traffic enforces the configured cap. (Before #1128 only the WebSocket
   handler applied it; PR #1146's harness found the gap.)
2. Local fast refusal: the registry's own busy-conversation and cap checks.
3. Cluster-wide claim: `RunCoordinator.claim(...)`. With Redis this is one Lua
   script. It refuses when another run holds the conversation or the user is at
   the cap, and otherwise takes both atomically. Refusals raise the same
   `ConversationBusyError` / `ConcurrencyLimitError` as the registry, so
   transports map them as before (WebSocket `conversation_busy` /
   `run_limit_exceeded`; REST 409 / 429).
4. Local insert, `RunRegistry.start(run_id=...)`, run synchronously together
   with the caller's precheck (the `atlas_launch` depth and fan-out limits).

If step 4 refuses, or the caller is cancelled while step 3 is in flight, the
claim is released before the error propagates. A failed admission holds no
slot. When a run reaches a terminal state, `RunRegistry.set_status` schedules
the release. On graceful shutdown, `shut_down_live_runs` works in a fixed order, so that a
claim is never given back while its run could still be executing on this
replica:

1. It closes admission. Later `admit_run` calls fail with
   `RunCoordinationUnavailableError` and the client retries on another
   replica.
2. It cancels the live runs' tasks and awaits them, for up to 10 s, inside
   one lease. The heartbeat keeps renewing meanwhile.
3. It releases their claims. A run that never got a task is recorded as
   `failed`.

A task that does not stop in time keeps its claim, and the lease frees it
after the process exits. The lifespan then drains the releases before
closing the Redis pool.

Every admission refusal refuses the turn. On the WebSocket path an unexpected
`RunRegistryError` subclass becomes an `error` frame (`run_not_admitted`)
rather than an untracked turn, which would bypass the cap.

If the claim's reply is lost (a timeout after Redis applied it), the
coordinator releases whatever this replica holds under that run id before
reporting the failure.

If Redis cannot be reached, admission **fails closed** with
`RunCoordinationUnavailableError` (REST 503 `unavailable`, WebSocket
`run_coordination_unavailable`). Admitting without the shared check would let a
user exceed the cap for exactly as long as the outage lasts. There is no
setting to fall back to local admission while Redis is configured. A
single-replica deployment that does not want the dependency runs without
Redis (below).

Runs already executing when Redis goes away keep going. If the outage outlasts
the lease (30 s), their claims lapse: each is reported lost once (see
"Renewal reports loss") and, until those runs finish, the cluster-wide cap and
conversation lock no longer count them. Nothing re-claims them automatically.

The WebSocket handler also guards the window between a granted admission and
the run's task being attached (seeding the run's session files, sending
`run_started`). Any exit from it, a cancelled socket included, marks the run
`failed`, which releases the claim. Without the guard a record with no task
would be renewed indefinitely and hold the conversation and a slot on every
replica.

Without Redis (`SESSION_REPOSITORY_TYPE=memory`) the `InProcessRunCoordinator`
admits everything and never suspends, so the registry's checks are the whole
story, as they were before #1128.

## The Redis contract

`<p>` is `REDIS_KEY_PREFIX`. Variable parts are escaped (`%` to `%25`, `:` to
`%3A`, see `key_part`), so a `:` in an email or a client-supplied conversation
id cannot make two keys collide. Emails are normalized (lower-cased) first.
Builders live in `RunCoordinationKeys`
(`atlas/infrastructure/redis/run_coordinator.py`). Later work must use them
rather than formatting keys by hand.

| Key | Type | Contents | Lifetime |
|-----|------|----------|----------|
| `<p>:run:<run_id>` | hash | The run record (fields below) | Lease + 300 s while live; 300 s after release |
| `<p>:run-conv:<conversation_id>` | string | `run_id` of the run holding the conversation, for every user (conversation ids are global in storage) | `PX` = lease, renewed |
| `<p>:run-user:<user>` | sorted set | The user's claimed `run_id`s, scored by lease expiry (epoch ms) | 2 x lease, renewed |
| `<p>:run-fence` | string | Fencing counter, only ever `INCR`ed (by claims and by takeovers), floored at Redis time (microseconds) | Persistent |
| `<p>:runs-live` | sorted set | Every claimed, unreleased `run_id`, scored by lease expiry (epoch ms); read by the orphan sweep | Removed on release; after a takeover, once the run's projection is closed |
| `<p>:run-cmd:<run_id>` | stream | **Reserved** for command routing (stop, steer, approve) | (not written yet) |
| `<p>:replica:<replica_id>` | string/hash | **Reserved** for replica liveness | (not written yet) |

Run record fields:

| Field | Meaning |
|-------|---------|
| `run_id` | Stable run id (also the registry's id and the REST API's id) |
| `conversation_id` | The conversation the run mutates |
| `user_email` | Normalized owner |
| `owner` | Replica id holding the run: `<hostname>-<pid>-<random>`, minted once per process, so a coordinator rebuilt in the same process still owns its claims (on Kubernetes the hostname is the pod name) |
| `generation` | Fencing token from `<p>:run-fence`. Later claims always carry a larger value: the counter is `INCR`ed and floored at the Redis clock in microseconds, so a flushed or restarted Redis (counter back at 0) still issues values above every earlier one, a burst just before the reset included. The counter could only run ahead of that clock with more than one claim per microsecond, which a claim script cannot sustain. The guarantee breaks only if the Redis clock steps backwards past the last generation |
| `status` | `queued` at claim, then the registry status carried by each lease renewal (`running`, `waiting_for_input`), and the terminal status at release |
| `lease_expires_at` | Epoch ms from the Redis clock; `0` once released |
| `created_at`, `updated_at` | Epoch ms from the Redis clock |
| `snapshot` | JSON of the run's public status (title, `source`, model, tools, timestamps, the completed `result` capped at 20,001 characters), written by the owner under its owner/generation, so any replica can answer `GET /api/agent-runs/{id}` |
| `source` | The run's launch source (`api` for agent-runs API runs, empty otherwise), written with the snapshot; an API launch awaits the first write before it returns 202 |
| `error` | The run's error, if any |
| `interrupted` | `1` once an orphan sweep took the run over (see below) |
| `interrupted_by` | Replica id that took it over |

Rules every script follows:

- **Time is Redis time** (`TIME`), so replicas with skewed clocks agree on
  expiry.
- **Expired slots free themselves.** Before counting, the claim prunes members
  of `run-user` whose score is in the past, and `run-conv` expires on its own.
  A replica that dies holding claims stops blocking its users within one lease
  (`RUN_LEASE_SECONDS`, 30 s).
- **Only the holder releases.** Release requires the record's `owner` and
  `generation` to match and, while the run is live, an unexpired lease (as
  renewal does). A stale owner cannot free a slot that a newer claim of the
  same run holds, nor record an outcome after its lease passed and before a
  sweep took the run over.
- **Release is final.** Renewal refuses a record in a terminal status, so a
  heartbeat that races the release cannot take the slot back.
- **The final snapshot comes first.** With the lease guard running, a
  terminal release waits behind the release gate (`set_release_gate`) for the
  run's terminal `MIRROR`, which writes the terminal status and the final
  snapshot (the reply included) in one script. No replica therefore sees the
  run end without its result. If that write cannot be confirmed, the guard
  keeps the release and does it once a sweep's retried mirror lands. If the
  owner dies meanwhile, the lease lapses and the run reads `interrupted`,
  never `completed` without its reply.
- **Renewal reports loss.** A lapsed lease is never re-taken (its slot may
  already count for another run). The owner renews every live claim every
  third of `RUN_LEASE_SECONDS` (10 s by default), on a fixed schedule so a
  slow pass does not delay the next one. The heartbeat starts with the first
  claim on an event loop, so the API server and a headless `AtlasClient` both
  renew. Renewals run concurrently (16 at a time). Renewal returns `False`
  for a claim the replica no longer holds: the lease lapsed, another owner or
  generation holds the run, or it was released elsewhere. The first `False`
  moves the claim from `RunRecord.claim` to `RunRecord.lost_claim`, logs one
  warning and notifies the claim-lost listeners once; the claim is not
  renewed or reported again. The lease guard's listener then fences the run
  (next section).

Scripts touch several keys, so the contract assumes one logical Redis (a
primary, optionally with replicas or Sentinel), which is what `REDIS_URL`
supports today. Redis Cluster would need hash tags and is out of scope.

## Extension points for fencing

- `admission.add_claim_lost_listener(callback)`: `callback(record)` runs on
  the owning replica, once per claim, when lease renewal refuses a live run's
  claim (it lapsed, another owner or generation holds the run, or it was
  released elsewhere). `record.lost_claim` is the refused `RunClaim`, with
  the generation this replica held. It returns an unsubscribe callable. Callbacks must not block, and an
  exception is logged without stopping other listeners. Stopping the run or
  refusing its writes belongs to the fencing work.
- `RunCoordinator.read(run_id)`: the shared run record (fields above, as
  strings), readable from any replica. `None` without Redis.

## Leases, orphan detection and fencing

`atlas/application/chat/runs/leases.py` (`RunLeaseGuard`, one per process,
started with the shared infrastructure) acts on the claims above through the
extension points. Its two scripts (`INTERRUPT`, `MIRROR`) live in
`atlas/infrastructure/redis/run_leases.py` and use `RunCoordinationKeys`. It
adds no record of its own: everything it writes goes into `run:<run_id>`,
under the claim's owner and generation. Its changes to #1161's scripts are the
`runs-live` index (claim adds, renew rescores, release removes) and release's
lease check above. The guard is installed wherever a Redis coordinator holds
claims: by the API server's shared-infrastructure startup and by every
`AtlasClient.chat` call (re-armed when `chat_sync` runs a new event loop).

| Step | Who | What |
|------|-----|------|
| Mirror | owner | On every registry change, the `MIRROR` script writes the `snapshot`, `source` and a non-terminal `status` if `owner` and `generation` are still the writer's and, for a live run, its lease has not passed. Once the record is terminal, a snapshot of a live state is dropped (an older write landing late). There is one writer per run on the owner, so the newest state is the last written, and a run counts as mirrored only once a terminal snapshot was written. A refusal fences the run. A write that fails (Redis unreachable) is retried by the next sweep, a terminal one included. `launch_api_run` and `follow_up_api_run` await the first write before returning. |
| Orphan sweep | every replica | Every half lease (jittered), read `runs-live` members whose score has passed and run the `INTERRUPT` script on each. It acts only on a non-terminal record whose `lease_expires_at` is past by the Redis clock (which the owner can no longer renew). It then `INCR`s `run-fence`, sets `generation` to that value and `owner` to empty, marks the run `failed` with `interrupted=1`, `error` and `interrupted_by`, frees `run-conv` / `run-user` / `runs-live`, and closes the run in the execution projection. The run stays in `runs-live` until that close is confirmed by reading the projection back, so a failed close is retried by a later sweep on any replica (`INTERRUPT` then returns `-1`, and the sweeper closes the projection idempotently and drops the member). Each attempt reschedules the member a third of a lease later, behind the runs due now, so closes that keep failing cannot starve later orphans, and refreshes the record's retention, so the obligation cannot expire before the close is confirmed. A candidate whose takeover fails is logged and retried next sweep without stopping the others; leases of runs gone from the local registry are dropped. |
| Fence | old owner | A lost claim (the `add_claim_lost_listener` callback, fired when the heartbeat's renewal is refused) or a refused mirror stops the run locally (`RunRegistry.mark_interrupted`): the task is cancelled (unless it is the run fencing itself, which is already unwinding), the local record reads failed/interrupted, its claim is dropped, and nothing more is published. If no replica has swept the run yet, the owner runs the takeover itself. |

The `runs-live` score is only a hint for the sweep. The takeover re-checks the
lease inside the script, so clock skew between replicas affects efficiency,
never safety.

**Checks in the run's own code.** A frozen owner's event loop notices
nothing until it runs again, and then a pending tool call or projection write
may run before its heartbeat does. `ensure_current_run_may_act()` (in
`execute_single_tool`, before the tool start notification and after it; in
`MCPToolManager.execute_tool` after authorization; right before each request
to the MCP server, after the client or session setup awaited, the retry after
a 401 included; and in the launcher right before a child run is admitted) and
`may_publish_for_run()` (in `publish_execution_event`) trust the claim only
for 80% of `RUN_LEASE_SECONDS` after the claim was last proved by a renewal:
the heartbeat's (`admission.add_claims_renewed_listener`, credited from the
moment the renewal was sent) or the check's own. Past that window the check
renews. The window is measured on `CLOCK_BOOTTIME`, which keeps counting
while the process is stopped.
- An explicit refusal for a run that is still live fences it at once (a
  refusal because the run just ended and released is not a fence).
- A success that arrives a whole lease or more after it was sent proves
  nothing about now: it counts as an unknown answer.
- A run that has ended locally but whose terminal snapshot is not written
  yet is checked by writing it (refused if the run was taken over), also when
  it ended while a renewal was in flight, so a
  resumed owner cannot publish `completed` over a takeover's interrupted
  projection.
- An unknown answer (Redis unreachable, a timeout) fences it only once a whole
  lease has passed since the last proven renewal (the claim itself is not
  proof: its reply may have arrived late): before that no other replica can
  have taken the run over, so a single network blip does not cancel a healthy
  run.

A fenced tool call raises `CancelledError`, so the turn unwinds as a stopped
turn.

**What an interrupted run looks like.**
- `GET /api/agent-runs/{id}` on any replica: `status: failed`,
  `interrupted: true`, and an `error` saying the server running it stopped
  responding. A non-owner that cannot read the shared record, finds its
  snapshot unreadable, or finds no `source` written (live or interrupted)
  answers `503 unavailable` (retry), never `404`: a client that relaunches on
  404 would repeat the run's side effects.
- In the execution projection, completed tool rows keep their status and
  result; open rows become `interrupted`.
- **No tool is retried.** A side effect that was in flight may or may not
  have happened; its row says interrupted, never completed.
- The old owner, if it comes back, reports the same outcome from its own
  registry. It does not save its partial turn to chat history: the
  conversation may have a newer turn saved since the takeover, and a save
  replaces the stored message set. Its finished tool calls stay in the
  execution projection.
- Live session snapshots (`session:<id>`) are not fenced.

A graceful shutdown stops its live runs and then releases their claims
(`shut_down_live_runs`, above). A run whose task does not stop in time keeps
its claim, and the others report it interrupted once its lease lapses, the
same as after a crash. Detection of a lost owner takes roughly
one to one and a half `RUN_LEASE_SECONDS`. A shorter lease detects failure
sooner, at the cost of more Redis traffic and a higher chance that a long
event-loop stall fences a healthy run.

## Command IDs (reserved)

Cross-replica commands will be appended to `<p>:run-cmd:<run_id>` with at
least: `command_id` (a UUID chosen by the sender, so a retried command is
handled once), `type` (`stop`, `steer`, `approval`, ...), `issued_by` (replica
id), `issued_at`, and the `generation` the sender saw. An owner whose own
generation is newer ignores the command. Nothing produces or consumes the
stream yet.

## Still open in #1128

Tracked as a strict xfail in `test/multireplica/test_run_coordination_gaps.py`:
routing stop/steer/approval to the owner. It should be built on the record and
keys above rather than new ones. (Inspecting a run from a non-owner, reporting
owner loss as `failed`, and fencing a paused owner are closed by the leases.)

## Tests

- `atlas/tests/test_redis_run_coordinator.py`: the Lua scripts under
  fakeredis (`fakeredis[lua]`), including races between coordinators with
  different replica ids.
- `atlas/tests/test_run_admission.py`: `admit_run`, including cleanup after
  every kind of failed admission.
- `atlas/tests/test_run_leases.py`: the lease guard on the same scripts with
  two replicas: mirror, sweep, takeover generation, stale-owner refusal,
  frozen owner, Redis outage after the lease lapsed, projection and tool
  guards, non-owner reads.
- `test/multireplica/test_cluster_admission.py`, `test_run_leases.py` and
  `test_run_coordination_gaps.py`: two real replicas with shared Redis and
  PostgreSQL (`bash test/multireplica_tests.sh`), including SIGKILL and
  SIGSTOP of the owner with the side-effect counter proving nothing was
  replayed.
