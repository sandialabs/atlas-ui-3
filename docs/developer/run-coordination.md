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
flushed, history is untouched. Runs already executing keep going, but their
claims are gone (their next renewal reports them lost), so until they finish
the cluster-wide cap counts only runs admitted after the flush.

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
the release.

If Redis cannot be reached, admission **fails closed** with
`RunCoordinationUnavailableError` (REST 503 `unavailable`, WebSocket
`run_coordination_unavailable`). Admitting without the shared check would let a
user exceed the cap for exactly as long as the outage lasts.

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
| `<p>:run-conv:<user>:<conversation_id>` | string | `run_id` of the run holding the conversation | `PX` = lease, renewed |
| `<p>:run-user:<user>` | sorted set | The user's claimed `run_id`s, scored by lease expiry (epoch ms) | 2 x lease, renewed |
| `<p>:run-fence` | string | Fencing counter, only ever `INCR`ed | Persistent |
| `<p>:run-cmd:<run_id>` | stream | **Reserved** for command routing (stop, steer, approve) | (not written yet) |
| `<p>:replica:<replica_id>` | string/hash | **Reserved** for replica liveness | (not written yet) |

Run record fields:

| Field | Meaning |
|-------|---------|
| `run_id` | Stable run id (also the registry's id and the REST API's id) |
| `conversation_id` | The conversation the run mutates |
| `user_email` | Normalized owner |
| `owner` | Replica id holding the run: `<hostname>-<pid>-<random>`, unique per process (on Kubernetes the hostname is the pod name) |
| `generation` | Fencing token from `<p>:run-fence`. Later claims always carry a larger value |
| `status` | `queued` at claim, then the registry status carried by each lease renewal (`running`, `waiting_for_input`), and the terminal status at release |
| `lease_expires_at` | Epoch ms from the Redis clock; `0` once released |
| `created_at`, `updated_at` | Epoch ms from the Redis clock |

Rules every script follows:

- **Time is Redis time** (`TIME`), so replicas with skewed clocks agree on
  expiry.
- **Expired slots free themselves.** Before counting, the claim prunes members
  of `run-user` whose score is in the past, and `run-conv` expires on its own.
  A replica that dies holding claims stops blocking its users within one lease
  (`RUN_LEASE_SECONDS`, 30 s).
- **Only the holder releases.** Release requires the record's `owner` and
  `generation` to match. A stale owner cannot free a slot that a newer claim of
  the same run holds.
- **Release is final.** Renewal refuses a record in a terminal status, so a
  heartbeat that races the release cannot take the slot back.
- **Renewal reports loss.** The owner renews every live claim every
  `RUN_LEASE_RENEW_INTERVAL_SECONDS` (10 s, `run_lease_heartbeat` in the app
  lifespan). It gets `False` for a claim it no longer holds, because the claim
  expired and another run took the conversation, or because it was released
  elsewhere. Today it only logs a warning. Acting on it (stopping the run, or
  refusing its writes by generation) is the fencing work below.

Scripts touch several keys, so the contract assumes one logical Redis (a
primary, optionally with replicas or Sentinel), which is what `REDIS_URL`
supports today. Redis Cluster would need hash tags and is out of scope.

## Command IDs (reserved)

Cross-replica commands will be appended to `<p>:run-cmd:<run_id>` with at
least: `command_id` (a UUID chosen by the sender, so a retried command is
handled once), `type` (`stop`, `steer`, `approval`, ...), `issued_by` (replica
id), `issued_at`, and the `generation` the sender saw. An owner whose own
generation is newer ignores the command. Nothing produces or consumes the
stream yet.

## Still open in #1128

Tracked as strict xfails in `test/multireplica/test_run_coordination_gaps.py`:
inspecting a run from a non-owner, routing stop/steer/approval to the owner,
reporting owner loss as `failed`, and fencing a paused owner. Each should be
built on the record and keys above rather than new ones.

## Tests

- `atlas/tests/test_redis_run_coordinator.py`: the Lua scripts under
  fakeredis (`fakeredis[lua]`), including races between coordinators with
  different replica ids.
- `atlas/tests/test_run_admission.py`: `admit_run`, including cleanup after
  every kind of failed admission.
- `test/multireplica/test_cluster_admission.py` and
  `test_run_coordination_gaps.py`: two real replicas with shared Redis and
  PostgreSQL (`bash test/multireplica_tests.sh`).
