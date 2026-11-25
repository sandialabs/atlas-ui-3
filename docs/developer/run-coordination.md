# Run coordination across replicas

Last updated: 2026-11-04

Issue #1128. How API replicas agree on which runs exist, and the Redis
contract that later lease, routing and failover work builds on.

## Who owns what

| State | Lives in | Why |
|-------|----------|-----|
| The live task, steering channel, MCP clients, replay buffers | The owning replica's `RunRegistry` (`atlas/application/chat/runs/registry.py`) | Python objects; they cannot move between processes |
| Admission (one run per conversation, `MAX_CONCURRENT_RUNS_PER_USER` per user), run ownership, leases, fencing generations | Redis, through the `RunCoordinator` | Every replica must see the same answer at the same moment |
| Agent Runs API idempotency keys (`(user, key)` to request fingerprint and run handle) | Redis, through the `IdempotencyStore` (`atlas/application/chat/runs/idempotency.py`) | A client retry through a load balancer lands on any replica. Keyed requests fail closed when Redis is unreachable; the store is process-local without Redis |
| Transcripts, conversation metadata, anything that must survive a Redis restart | PostgreSQL (chat history) | Source of truth |

Redis coordinates live work and is not the source of truth. If Redis is
flushed, history is untouched. Runs already executing lose their claims:
their next renewal reports them lost, and the lease guard fences them (they
stop at their next tool call or projection write). The cluster-wide cap
counts only runs admitted after the flush.

## Admission

Every tracked run, from any transport (WebSocket turn, `atlas_launch_subagent`, the
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
   with the caller's precheck (the `atlas_launch_subagent` depth and fan-out limits).

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
rather than a turn that bypasses the cap.

If the claim's reply is lost (a timeout after Redis applied it), the
coordinator releases whatever this replica holds under that run id before
reporting the failure. For API turns, CLAIM exposes the routing source but stages
the continuation spec without updating the latest pointer or retained indexes.
The local record holds the claim before publication can suspend. Publication
checks that the original claim is still held. A refused publication removes
the local record and attempts to undo the shared claim. Its original publication
identity permits metadata cleanup after lease expiry or orphan takeover, without
releasing a different run's lock or overwriting a newer continuation pointer.
Publication also records a provisional marker in the shared run hash. The
marker is cleared by an explicit successful activation renewal at the API task
execution gate, or a normal terminal release after acknowledged admission.
Routine heartbeat renewals never activate a provisional run. The gate retries
unknown renewal replies twice, but never starts work without confirmed ownership. Before that point, the prior continuation is retained for at least
the provisional pointer's lifetime. A shutdown or cancel during publication
releases the admission as refused, not as an accepted terminal turn.

The owner's orphan sweep retries known refusals promptly. If the owner
disappears before cleanup succeeds, any replica's existing orphan sweep rolls
back the expired provisional publication atomically, restores a still-retained
predecessor, and removes the refused run from the retained indexes. A tombstone
keeps the projection-close obligation until that close is confirmed. Runs that
reached the execution boundary instead retain the normal owner-lost outcome.
The marker tracks shared admission/activation, not receipt of the HTTP response
by the client; it cannot provide exactly-once HTTP delivery.

If Redis cannot be reached, admission **fails closed** with
`RunCoordinationUnavailableError` (REST 503 `unavailable`, WebSocket
`run_coordination_unavailable`). Admitting without the shared check would let a
user exceed the cap for exactly as long as the outage lasts. There is no
setting to fall back to local admission while Redis is configured. A
single-replica deployment that does not want the dependency runs without
Redis (below).

Runs already executing when Redis goes away keep going only while their lease
holds. An unreachable Redis is an unknown answer, not a refusal: once a whole
lease (`RUN_LEASE_SECONDS`, default 30 s) has passed since the last proven renewal, the
run is fenced at its next tool call or projection write, and when Redis
returns, its renewal finds the conversation lease expired and reports the claim
lost. Either way the run is cancelled and its partial turn is **not saved**
(see "Fence" below): an outage longer than the lease loses the unsaved turn of
every run in flight, on every replica. Nothing re-claims a lapsed run. This is
deliberate: with Redis unreachable nothing can show that no other replica took
the run over, and a save replaces the stored message set.

The same happens without an outage when the shared record is gone or expired:
a flush, a failover that loses recent writes, or an evicting `maxmemory-policy`
(Redis must run `noeviction`). The run's claim is refused at its next renewal.

The user is told: the owner's `run_status` frame carries `status: failed`,
`end_reason: fenced` (sent from local state, so it does not need Redis), and
the frontend shows a notice that the response was interrupted, was not saved
and can be sent again; the history list marks the conversation Interrupted.
The server log has `Run ... lost its claim` and `Not saving the turn of a run
that lost its claim`, which says whether a takeover was observed here. A
fenced run's work is never saved: it is a retry for the user.

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
| `<p>:run:<run_id>` | hash | The run record (fields below) | Lease + retention while live; retention after release (see [Time constants](#time-constants)), or lease + retention while a descendant is live |
| `<p>:run-kids:<run_id>` | set | The run ids `run_id` launched; how the scripts find live descendants | As its run record |
| `<p>:run-conv:<conversation_id>` | string | `run_id` of the run holding the conversation, for every user (conversation ids are global in storage) | `PX` = lease, renewed |
| `<p>:run-user:<user>` | sorted set | The user's claimed `run_id`s, scored by lease expiry (epoch ms) | 2 x lease, renewed |
| `<p>:run-fence` | string | Fencing counter, only ever `INCR`ed (by claims and by takeovers), floored at Redis time (microseconds) | Persistent |
| `<p>:run-api-latest:<conversation_id>` | string | Latest accepted API admission; compare predecessor inside CLAIM, publish after the local insert | Until the last retained API run in the conversation expires |
| `<p>:run-api-latest:<conversation_id>:retained` | sorted set | Conversation API run IDs scored by expiry; determines the latest pointer and spec lifetime | Expires with its last member |
| `<p>:runs-api`, `<p>:runs-api-user:<user>` | sorted set | Global and per-user retained API run IDs, scored by record expiry | Prune expired scores on writes and listing; expire the index with its last member |
| `<p>:runs-live` | sorted set | Every claimed, unreleased `run_id`, scored by lease expiry (epoch ms); read by the orphan sweep | Removed on release; after a takeover, once the run's projection is closed |
| `<p>:ctl:<replica_id>` | stream | Run-control inbox of one replica: envelopes for stop, steer, approval ([run control](run-control.md)) | Entries deleted on read; trimmed to ~1000; inbox TTL, refreshed per send |
| `<p>:ctl-reply:<scope>` | stream | Replies to one request (`scope` hashes actor and request id) | reply TTL |
| `<p>:ctl-done:<scope>` | string | Dedup record: `pending`, then the reply once the command took effect | pending TTL, then done TTL |
| `<p>:idem:<user>:<key>` | hash | Agent Runs API idempotency reservation (#1220): `token`, `fingerprint`, `created_at`, `handle` (JSON, absent while the first request is admitting), `addressed_run_id`, `conversation_id`. Written by `RedisIdempotencyStore` (`atlas/infrastructure/redis/idempotency_store.py`) with one Lua script per step (reserve, complete, release); without Redis `InProcessIdempotencyStore` keeps the same contract in a dict | 2 minutes while pending, extended every 40 s by the admitting request (a crash between reserve and launch frees the key); 24 hours once the handle is recorded |
| `<p>:idem-addr:<run_id>:<key>`, `<p>:idem-conv:<conversation_id>:<key>` | string | The user whose follow-up of `run_id` (in `conversation_id`) used `key`, so a retry finds its record after the run is reaped, on another replica, or naming a sibling run of the conversation (resolved through the run's shared record) | As its `idem` hash |
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
| `source` | The run's launch source (`api` for agent-runs API runs, empty otherwise), written at claim. The snapshot write repeats it |
| `parent_run_id` | The run that launched this one (empty for a top-level run), written at claim; the scripts walk it to keep a finished parent readable while a descendant is live |
| `follow_up_of` | The run an API follow-up continues (empty otherwise), written at claim |
| `parent_conversation_id`, `title`, `launch_origin`, `depth` | The run's place in the conversation tree (issue #1222), written at claim: the conversation that launched it (empty for a top-level run), the prompt that started it (first 200 characters; never written for an off-record run), the published launch origin (`agent_runs_api` or empty) and its launch depth. A replica that lists a run it does not own copies them onto the listed run, so an unsaved child still nests under its parent. Records claimed before this field existed fall back to the same values in `snapshot` |
| `error` | The run's error, if any |
| `interrupted` | `1` once an orphan sweep took the run over (see below) |
| `interrupted_by` | Replica id that took it over |

### Time constants

Two constants, `RUN_LEASE_SECONDS` (30 s) and `RUN_RETENTION_SECONDS` (30 min),
both in `atlas/application/chat/runs/coordination.py`. Everything else derives
from them, and nothing keeps its own number.

| What | Value | Where |
|------|-------|-------|
| Lease; renewed every third of it | `RUN_LEASE_SECONDS` | claim, renew |
| Run record after the run ended and no descendant is live | `RUN_RETENTION_SECONDS` | release, takeover |
| Run record while a descendant is live | lease + retention, refreshed by each descendant's renewal | release, renew |
| Registry terminal runs | `RUN_RETENTION_SECONDS` | `registry.py` |
| Execution projection of a finished run | `RUN_RETENTION_SECONDS` | `execution_state.py` |
| Control inbox and reply streams | 4 x lease | `control_bus.py` |
| Control `done` dedup record | `RUN_RETENTION_SECONDS` | `control_bus.py` |
| Control `pending` marker | lease | `control_bus.py` |

When a run ends, `RELEASE` and `INTERRUPT` set its record's expiry to the
retention window only if nothing below it in the launch tree is live; otherwise
they keep it for lease + retention, and each live descendant's `RENEW` pushes a
finished ancestor's expiry out again. A descendant ending starts the retention
window for every finished ancestor it was the last live descendant of (the walk
is capped at 64 levels, as in the registry). So a stop or cancel of a finished
parent through any replica finds its record for as long as its children run.

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
  execution projection. The same holds when no replica took the run over and
  it was fenced only because the lease lapsed during a Redis outage: the owner
  cannot tell the two apart, so the turn is not saved either.
- Live session snapshots (`session:<id>`) are not fenced.

A graceful shutdown stops its live runs and then releases their claims
(`shut_down_live_runs`, above). A run whose task does not stop in time keeps
its claim, and the others report it interrupted once its lease lapses, the
same as after a crash. Detection of a lost owner takes roughly
one to one and a half `RUN_LEASE_SECONDS`. A shorter lease detects failure
sooner, at the cost of more Redis traffic and a higher chance that a long
event-loop stall fences a healthy run.

## Run-control commands

A stop, steer or approval that reaches a replica which does not hold the run
is sent to the owner named by the run record's `owner`, on that replica's
inbox `<p>:ctl:<owner>` (one stream per replica rather than one per run, so
an owner consumes a single stream). The envelope, JSON in field `c`:

| Field | Meaning |
|-------|---------|
| `request_id` | Chosen by the caller (or derived, e.g. `approval:<invocation_id>`); a retry reuses it and the owner executes it once (`ctl-done` `SET NX`) |
| `attempt` | Fresh per send; the reply carries it, so a retry never reads an earlier attempt's reply |
| `command` | `stop`, `steer` or `approval` |
| `run_id` | The run |
| `generation` | The record's `generation` as the sender read it. The owner refuses with `stale_generation`, executing nothing, unless the record still names itself at that generation |
| `actor` | `{"kind": "user", "email"}` or `{"kind": "service"}`; the owner re-checks it against its own registry |
| `args` | Command arguments |
| `deadline` | Epoch seconds on the Redis server clock (`TIME`); an owner that reads the command later answers `command_expired` without executing it |

Details, reply codes and guarantees: [run control](run-control.md).

## Child run frames and tool identity

A tool that launches a run (`atlas_launch_subagent`, `atlas_follow_up_subagent`)
hands its update callback to the child, so the child's frames travel through
the parent's tool-call wrappers. Rule: a frame whose `run_id` differs from the
wrapper's own run belongs to a descendant. `observe_tool_callback` and
`stamp_callback_identity` forward it unchanged: no parent `tool_name`,
`tool_call_id`, `step` or `invocation_id`, and no execution-projection write.
`_ChildConnection` likewise calls `note_event` only for frames with no run id
or its own run id, so a grandchild's approval request does not mark the
middle child as waiting for input.

## Beyond #1128: what stays replica-local

Nothing in `test/multireplica/test_run_coordination_gaps.py` is still a gap:
admission, inspection from a non-owner, owner loss, fencing a paused owner and
routing stop/steer/approval to the owner are all covered. That file covers the
browser run path. These pieces are still replica-local and are not gaps in
that file:

- The `atlas_follow_up_subagent` tool reads the replica's own run registry.
  The Agent Runs API follow-up and list endpoints are not replica-local:
  with Redis they use the retained API run records and the per-conversation
  continuation pointer (see the CLAIM notes above). Status by run id
  (`get_api_run_anywhere`), cancel and the idempotency keys are cluster-wide,
  and so are the launch result and the listing of
  sub-conversations (`atlas_get_subagent_result` and
  `atlas_get_subagent_runs`, see
  [reading sub-conversations from any replica](#reading-sub-conversations-from-any-replica)).
- Opening a conversation whose run has not saved its first turn
  (`in_flight.py`) resolves the run through the local registry, so only the
  owning replica can show its transcript so far.
- MCP elicitation requests and their responses (`elicitation_manager`) live in
  the process that issued them.

## Tests

- `atlas/tests/test_redis_run_coordinator.py`: the Lua scripts under
  fakeredis (`fakeredis[lua]`), including races between coordinators with
  different replica ids.
- `atlas/tests/test_idempotency_store.py`: both idempotency stores (atomic reserve under concurrency, different body, release, pending TTL expiry, Redis down fails closed).
- `atlas/tests/test_run_admission.py`: `admit_run`, including cleanup after
  every kind of failed admission.
- `atlas/tests/test_run_leases.py`: the lease guard on the same scripts with
  two replicas: mirror, sweep, takeover generation, stale-owner refusal,
  frozen owner, Redis outage after the lease lapsed, projection and tool
  guards, non-owner reads.
- `test/multireplica/test_idempotency_across_replicas.py`, `test_cluster_admission.py`, `test_run_leases.py` and
  `test_run_coordination_gaps.py`: two real replicas with shared Redis and
  PostgreSQL (`bash test/multireplica_tests.sh`), including SIGKILL and
  SIGSTOP of the owner with the side-effect counter proving nothing was
  replayed.

## Reading sub-conversations from any replica

Issue #1222. `atlas_get_subagent_runs` and `atlas_get_subagent_result` look in
the local run registry first, then in shared state
(`atlas/application/chat/runs/shared_children.py`), so a parent turn that lands
on a replica other than the launching one, or after the local retention window
or a restart, still finds its children:

1. **Live children, any replica.** The user's live runs (`<p>:run-user:<user>`)
   whose `parent_conversation_id` is the caller's conversation. Status, waiting
   state and error come from the run record and its mirrored `snapshot`.
2. **Finished children, within retention.** The run record is read by run id;
   a completed run's reply is the `result` in its snapshot.
3. **Finished children, after retention.** The stored child conversations: the
   metadata the server writes on the child's first save holds
   `parent_conversation_id` and `launch_run_id` (the run id the launch
   returned). `ConversationRepository.list_child_conversations` finds them by
   parent and user, and the reply is the conversation's last assistant message.
   The launch path records how the launching run ended (`launch_status`,
   `launch_depth`) and how the newest run ended (`latest_status`) on the child's
   conversation, before the run's status is published. A lookup by the launch
   run id reports `launch_status`; a lookup by conversation id reports
   `latest_status`, so a failed follow-up is not shown as the launch's
   success. A child with no recorded outcome (stored before this, or its
   replica died first) is reported `failed` with an error saying so, never
   assumed successful; its last saved reply is still returned. A child stored
   before `launch_run_id` existed is addressed by its conversation id. The
   stored children are read in full (up to 5000 per parent conversation), not
   just the first page. When shared state cannot be read, the listing returns
   the children this replica holds flagged `partial` (or is refused when it
   holds none).

Authorization is the registry path's: a caller sees only children of its own
conversation that its own user owns, and an unknown run, another user's run and
a run of another conversation all get the same "not a sub-conversation" answer.
An unreadable store is reported as unavailable, never as "not a sub-conversation".

`atlas_follow_up_subagent` is unchanged and still needs the launching replica's
registry: continuing a child runs on the owning replica.
