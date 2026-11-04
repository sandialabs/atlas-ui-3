# Cross-Replica Run Control

Last updated: 2026-11-04

A run executes on the replica that admitted it. Its asyncio task, steering
queue and approval futures live in that process and never move. With several
replicas behind a load balancer, the user's next request (a stop, a steer, an
approval) often reaches a different replica. That replica sends the request
to the run's owner as a **command** over Redis Streams and answers the client
with the owner's reply (issue #1128).

Code: `atlas/application/chat/runs/control_bus.py` (transport),
`run_control.py` (owner-side handlers and sender-side `route_*` helpers),
`run_directory.py` (publishing and withdrawing runs as they start and end),
`remote_run_frames.py` (the WebSocket frames a routed command produces).

Single-instance deployments (`SESSION_REPOSITORY_TYPE=memory`) have no bus:
every lookup returns "not routed" and the local paths behave as before.

## Redis contract

Run control adds no directory of its own. It routes by the run record that
admission writes and renews ([run coordination](run-coordination.md)):
`<p>:run:<run_id>` (hash; `owner`, `generation`, `status`, ...) names the
owning replica, and `<p>:run-conv:<conversation_id>` names the run holding a
conversation. A record whose status is terminal, or whose lease was released,
is not routed to. `run_directory.py` reads the record through
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
  (for example once a full steering queue has drained).
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
  id is safe. An owner that reads a command after its deadline answers
  `command_expired` without executing it, so a caller's timeout is never
  followed by a late stop or steer.
- **Authorized on the owner.** The directory only routes. The owner re-checks
  the actor against its own registry: a user may act only on their own run,
  the agent-runs service only on API-started runs, approvals only for the
  run's user. A user naming a conversation another user's run holds on any
  replica is refused like the local guard refuses it.
- **No replay.** A command only acts on a run that is still live on its
  owner. Nothing re-executes tool work; an owner that died takes its runs
  with it, and its claim lapses within one lease (30 s).

## Commands

| Command | Reaches the owner from | Owner action |
|---------|------------------------|--------------|
| `stop` | WS `stop_streaming` / `agent_control stop` naming a run or conversation this replica does not hold; `POST /api/agent-runs/{id}/cancel` | `registry.cancel` (user), `cancel_api_run` (service). Reply carries the run's status. |
| `steer` | WS `chat` for a conversation another replica is executing | Same checks as the local steer: loop draining, classification, queue bound. Optional `request_id` on the chat frame deduplicates client retries. |
| `approval` | WS `tool_approval_response` this replica recorded (`decide_tool_approval` returned `recorded`) | Wakes the waiting tool with the shared outcome. |

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
that conversation is **not** started as a second turn beside the run.

## Testing

- Unit: `atlas/tests/test_run_control_bus.py` (transport) and
  `atlas/tests/test_run_control_service.py` (handlers, routing,
  authorization), two buses on one fakeredis.
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
