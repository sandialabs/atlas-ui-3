# Cross-Replica Run Control

Last updated: 2026-11-06

A run executes on the replica that admitted it. Its asyncio task, steering
queue and approval futures live in that process and never move. With several
replicas behind a load balancer, the user's next request (a stop, a steer, an
approval) often reaches a different replica. That replica sends the request
to the run's owner as a **command** over Redis Streams and answers the client
with the owner's reply (issue #1128).

Code: `atlas/application/chat/runs/control_bus.py` (transport),
`run_control.py` (owner-side handlers and sender-side `route_*` helpers),
`run_directory.py` (reading the shared run record to find a run's owner),
`remote_run_frames.py` (the WebSocket frames a routed command produces).

Single-instance deployments (`SESSION_REPOSITORY_TYPE=memory`) have no bus:
every lookup returns "not routed" and the local paths behave as before.

## Redis contract

Run control adds no directory of its own. It routes by the run record that
admission writes and renews ([run coordination](run-coordination.md)):
`<p>:run:<run_id>` (hash; `owner`, `generation`, `status`, ...) names the
owning replica, and `<p>:run-conv:<conversation_id>` names the run holding a
conversation. A record whose status is terminal, or whose lease was released,
is not routed to, except by a stop (below). `run_directory.py` reads the record through
`RunCoordinator.read()`; the keys come from `RunCoordinationKeys`, and the
inbox is named by the coordinator's replica id.

The command keys, all built by `RunCoordinationKeys`:

| Key | Holds | Lifetime |
|-----|-------|----------|
| `<p>:ctl:<replica_id>` | the owner's inbox stream, one envelope per entry | trimmed to ~1000 entries |
| `<p>:ctl-reply:<scope>` | reply stream for one request id | 120 s |
| `<p>:ctl-done:<scope>` | dedup record: `pending`, then the reply once it took effect | pending 30 s, done 1 h |

One inbox per replica, not one stream per run: an owner reads a single
stream however many runs it holds. `<scope>` hashes the actor and the
request id, so one caller's request id can never read or replay another's.

An envelope carries `request_id`, a per-send `attempt`, `command`, `run_id`,
`generation` (as the sender read it from the record), `actor`
(`{"kind": "user", "email"}` or `{"kind": "service"}`), `args` and a
`deadline`. A reply carries `ok`, `code`, `message`, `payload`, the `attempt`
it answers, and `duplicate: true` when it is the remembered answer to an
earlier delivery.

## Guarantees

- **Idempotent.** The owner claims `ctl-done:<scope>` with `SET NX` before
  executing. A second delivery of the same request id (a client retry, a
  re-sent envelope) gets the first reply back and executes nothing. A
  refusal changed nothing, so it is not remembered and a retry runs again
  (for example once a full steering queue has drained). Once a command has
  acted, writing its reply to the dedup record is retried for as long as
  the pending marker holds the request (`DONE_WRITE_ATTEMPTS`). The dedup
  covers routed commands only: a steer a replica queues for a run it holds
  itself does not consult `ctl-done`, so a client that retries one
  `request_id` against two replicas can queue it twice. The browser client
  sends no `request_id`.
- **Correlated.** A sender reads only the reply carrying its own attempt id,
  so a retry never picks up an answer an earlier attempt left in the stream.
- **Fenced.** Before anything is recorded or executed, the owner reads the
  run record. Unless it still names this replica at the envelope's
  `generation`, the command is refused with `stale_generation` and nothing
  runs (the claim lapsed and the run was claimed again, or the record is
  gone). The sender then looks the run up once more and resends with the
  same request id if the run really moved.
  A run whose claim this replica's own heartbeat found lost
  (`admission.add_claim_lost_listener`) is refused at once, without waiting
  for the shared record to say so.
- **Bounded.** A sender waits `5 s` (`DEFAULT_TIMEOUT_SECONDS`), then reports
  `command_timeout`: the outcome is unknown and a retry with the same request
  id is safe. At the deadline the sender reads the reply stream once more
  without blocking, so an answer written as its last blocking read returned
  is still delivered (a steer the owner queued is not reported unanswered
  and then queued again by the retry). An owner that reads a command after its deadline answers
  `command_expired` without executing it, so a caller's timeout is never
  followed by a late stop or steer. A steer re-checks the deadline after its
  classification check and just before it is queued. The deadline is on the
  shared Redis clock (`TIME`), stamped just before the `XADD` and checked by
  the owner against `TIME`; the sender's own wait runs on its monotonic
  clock. Wall-clock skew between replicas therefore neither expires a valid
  command nor lets one run after its sender gave up.
- **Authorized on the owner.** The directory only routes. The owner re-checks
  the actor against its own registry: a user may act only on their own run,
  the agent-runs service only on API-started runs, approvals only for the
  run's user. A user naming a conversation another user's run holds on any
  replica is refused like the local guard refuses it.
- **Unknown is not "nobody".** A directory that cannot be read (a Redis error
  or timeout) raises `RunDirectoryUnavailable` instead of answering "no
  owner". A chat message naming a conversation is then refused with
  `run_control_unavailable` and never admitted, so an outage cannot start a
  second turn beside a run another replica is executing, or one under another
  user's run. A stop or API cancel answers `owner_unavailable` (WebSocket
  `run_control_unavailable`, HTTP 503). A stop naming no run id and no
  conversation stops the plain turn running on the socket's own session, if any.
- **Pool-friendly.** The bus shares the process's Redis pool (20 connections
  by default) with sessions, admission and lease renewal. At most
  `MAX_CONCURRENT_SENDS` (6) senders hold a connection in a blocking reply
  read at once; a sender past the bound waits for a slot within its own
  timeout and, if none frees, reports `command_timeout` without sending.
  Commands this replica executes are bounded by `MAX_CONCURRENT_DISPATCH`
  (8): the inbox reader takes a slot before it creates a task, so while all
  are busy further commands stay in Redis instead of piling up in memory, and
  waiting counts against the command's deadline.
- **Off the record stays local.** A steer from an incognito or not-saved
  turn (`turn_is_off_record`) is refused with `off_record` before anything is
  written: the inbox is shared Redis, and such content never goes to shared
  storage. The client gets a steering error, keeps the run's running state,
  and can wait for the run or stop it.
- **Not retained.** The owner `XDEL`s each inbox entry as soon as it has read
  it, so a command's arguments do not linger in Redis after delivery. Commands sent to
  a dead or stalled owner go with the inbox key's 120 s TTL
  (`INBOX_TTL_SECONDS`, refreshed per send); none of them could still
  execute by then. Reply and dedup keys hold the owner's answer, never the
  arguments.
- **Size-capped.** A serialized command over 256 KiB (`MAX_COMMAND_BYTES`)
  is refused with `command_too_large` before anything is written, so a large
  steering message cannot fill the shared Redis. The client gets a steering
  error and keeps the run's running state.
- **No replay.** A command only acts on a run that is still live on its
  owner. Nothing re-executes tool work; an owner that died takes its runs
  with it, and its claim lapses within one lease (30 s).

## Commands

| Command | Reaches the owner from | Owner action |
|---------|------------------------|--------------|
| `stop` | WS `stop_streaming` / `agent_control stop` naming a run or conversation this replica does not hold; `POST /api/agent-runs/{id}/cancel` | `registry.cancel` (user), `cancel_api_run` (service). Reply carries the run's status. |
| `steer` | WS `chat` for a conversation another replica is executing | Same checks as the local steer: loop draining, classification, queue bound. Optional `request_id` on the chat frame deduplicates client retries. |
| `approval` | WS `tool_approval_response` this replica recorded (`decide_tool_approval` returned `recorded`) | Wakes the waiting tool with the shared outcome. |

A stop, from the WebSocket or the agent-runs API, resolves like the local
stop: the named run first, even when it has finished, then the conversation's
live run. The named run is looked up on this replica and then in the shared
directory before any conversation fallback, so a stop naming a finished
parent on another replica reaches it even when this replica holds the
conversation's follow-up. A failure frame for a stop that named only the conversation
carries the run id the stop resolved to, so the client keeps its running
state and Stop control while the run executes on its owner. A finished run's retained record (kept 300 s after release) still
names its last owner, and the stop goes there: stopping a finished parent
stops the sub-conversations it launched, which run on the same replica
(#925). An API cancel of a finished run then also stops the conversation's
in-progress API run (a follow-up), on whichever replica executes it, also
when the finished run is held by the replica the cancel reached. Only an
API-started run cascades to a follow-up. A last owner or follow-up owner that
does not answer is reported (`command_timeout`, HTTP 504), never taken for
"nothing to stop": a retry is safe.

Known limit: the parent's shared record is retained for a fixed 300 s after
release (`RUN_RECORD_RETENTION_SECONDS`), while the owner's registry keeps a
parent with live descendants for as long as they run. A stop or API cancel of
such a parent through another replica more than 300 s after it finished
finds no record (WS: nothing happens; REST: 404); the same stop on the owner
still cascades. Keeping the record alive while descendants run is a
coordinator change left to later #1128 work.

Approvals keep the write-once outcome of [execution state](execution-state.md)
as the only settle point: the replica that receives the answer writes it with
`SET NX`, and a second or contradicting answer, on any replica, gets
`tool_approval_settled` with the first decision. The `approval` command only
wakes the owner's waiting tool instead of leaving it to its next poll, and it
refuses (`no_outcome`) when no outcome is recorded, so the transport can never
decide an approval. Its request id is derived from the invocation, so every
replica that nudges the same approval sends the same command.

A routed stop answers the WebSocket with a `run_status` frame; a routed steer
with the same `steering_queued` / `conversation_busy` / `steering_queue_full`
/ classification frames as the local path. When the owner cannot be reached
the client gets `error_type: run_control_unavailable`, and a chat message for
that conversation is **not** started as a second turn beside the run. Steer
refusals carry `steering: true`, and the client keeps the running state of a
`run_control_unavailable` frame that names a run, since the run is still
executing on its owner and Stop must stay available.

## Testing

- Unit: `atlas/tests/test_run_control_bus.py` (transport) and
  `atlas/tests/test_run_control_service.py` (handlers, routing,
  authorization, finished parents and API follow-ups, an unreadable
  directory, a steer outlasting its deadline), two buses on one fakeredis;
  `atlas/tests/test_websocket_run_directory_outage.py` (no turn admitted while
  the directory cannot be read).
- Multi-process: `test/multireplica/test_run_control_routing.py` runs on
  replica A and controls it through replica B, including duplicate cancels,
  retried steers, approvals answered three times, another user's commands and
  a frozen owner (`bash test/multireplica_tests.sh`).

## Not covered here

Inspecting a run from a non-owner (`GET /api/agent-runs/{id}`), reporting
owner loss and fencing a lost owner's own writes are the leases' work
([run coordination](run-coordination.md)); a run they interrupted has an
empty `owner` and a terminal status, so it is never routed to. Elicitation
responses are still answered only on the replica
that holds the elicitation.
