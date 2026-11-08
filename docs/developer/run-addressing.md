# Run addressing table

Issue #1128. Which run does a control frame reach, on which replica, and what
does the user see? One table, written before the resolver (`runs/resolve.py`) that replaced the
per-handler fallback chains in `atlas/main.py`. It describes today's behaviour;
rows marked `GAP` are where that behaviour disagrees with the expected outcome.

The tests are `atlas/tests/test_run_addressing_table.py`. A row tested there
cites that file in its last column and its id is in the test name (`S09` is
`test_S09_...`). A `GAP` row is a strict `xfail`: it asserts the expected
outcome, so closing the gap makes the marker fail until it is removed. A row
that names another test, or `n/a`, is not tested by the new file; where a row
needs two real processes it names the `test/multireplica/` test, or says none
exists yet.

## Axes

- **Entry point.** `ws stop` is `stop_streaming` and `agent_control` with
  `action: stop`: the two branches both call `stop_addressed_run`.
  `REST cancel` is `POST /agent-runs/{run_id}/cancel`. `ws chat` is a chat
  frame that steers a running turn. `ws approval` is `tool_approval_response`.
- **Address.** What the frame names: `run_id`, `conversation_id`, both, neither.
  When both are given the run id is tried first, and the conversation is the
  fallback for a run id nobody knows.
- **Kind.** `tracked` is a run in the registry. Every chat turn is one (issue
  #1128): a turn with history on, saved to the server, in agent mode with tools
  has its own session and outlives the socket; any other turn is a `plain turn`,
  a run on the connection's own session (`connection_bound`) that ends with the
  socket. `finished parent` is a terminal run whose sub-conversation runs are
  still live (stopping it cascades, `registry.py:766-815`). `API follow-up` is a
  finished API run whose conversation now holds a live follow-up with a new run
  id. `off-record turn` is an incognito or not-saved turn: a run
  (`off_record`) that claims its conversation cluster-wide and shares nothing
  else, so it only differs for steering. Retired row ids (the `untracked turn`
  rows) are not reused.
- **Location.** `local` is this replica's registry. `remote live` is another
  replica, run live. `remote terminal` is another replica, run finished, its
  directory record still kept (the retention window, and while a descendant is live). `unreadable` is the directory (Redis)
  failing. Record expiry is its own row where it changes the answer.

Expected outcomes are what the user observes. "No frame" means the client gets
nothing back; "status" is a `run_status` frame; "unavailable" is an error frame
with `error_type: run_control_unavailable`. It keeps the client's running state only
when it carries a `run_id` (a stop naming the run, or a steer routed to a known
run). A conversation-only stop or a turn refused before any run was found has no
run id, so the client ends the turn it started (rows `S15`, `S18`, `C04`).

## Collapsed cells

- `REST cancel` takes only a run id in the path: `conversation only`, `neither`
  and `both` do not exist (row `R12`). The conversation is looked up from the record.
- `REST cancel` is API-runs only: a run the UI started, a plain turn and an
  off-record turn all answer `run_not_found` (`external_runs.py:460-476`).
- `ws chat` reads `conversation_id` only (the `chat` branch of `websocket_endpoint`); a `run_id` on a chat
  frame is ignored. `run id only` equals `neither`, `both` equals `conversation
  only`. With no conversation a chat frame is matched to the plain turn this
  connection is already running, if any, and otherwise starts a turn.
- A plain turn is in the directory like any run, so it has the same `remote`
  rows. A stop that finds no run is a no-op, never an error frame: it
  finished, was reaped, or the id is stale, and the client has already left its
  running state.
- An approval is keyed by `invocation_id`, which names exactly one request. A
  bare `tool_call_id` is ambiguous when two runs hold the same provider id
  (`call_0`): `run_id` or `conversation_id` then disambiguates
  (`approval_manager.py:355-388`, `approval_resolution.py:100-133`), rows `P07`
  and `P08`. With an `invocation_id` every address resolves the same request,
  so the other approval rows are per location. The directory only wakes the owner (`nudge_approval`); the
  write-once outcome settles the approval, so an unreadable directory or a
  missing nudge delays the tool by one poll and never loses the answer.
- A finished parent has no `remote live` row (live would make it `tracked`).

## ws stop (stop_streaming, agent_control stop)

Code path: `stop_addressed_run` (`runs/commands.py`): `resolve` locates the run
(local registry, then the directory), the run is cancelled here or stopped on its
owner with `route_stop`. A frame that addresses nothing means the plain turn on
this connection's session. The tests call it in `ws_stop`.

| entry point | address given | run kind | location | expected outcome | today's code path | test |
|---|---|---|---|---|---|---|
| `S01` ws stop | run_id only | tracked | local | run cancelled, no frame | `resolve` Local, `registry.cancel` | `atlas/tests/test_run_addressing_table.py` |
| `S02` ws stop | run_id only | tracked | remote live | owner cancels, status frame | `resolve` Remote, `route_stop`, owner `_stop` | `atlas/tests/test_run_addressing_table.py` |
| `S03` ws stop | run_id only | tracked | remote terminal | status frame, run stays as it ended | `route_stop` with `include_terminal` | `atlas/tests/test_run_addressing_table.py` |
| `S04` ws stop | run_id only | tracked | unreadable | unavailable, run untouched | `route_stop` -> `owner_unavailable` -> `stop_frame` | `atlas/tests/test_run_addressing_table.py` |
| `S07` ws stop | run_id only | finished parent | local | children stopped | `registry.cancel` cascade above the terminal check | `atlas/tests/test_run_addressing_table.py` |
| `S08` ws stop | run_id only | finished parent | remote terminal | children stopped on owner, status frame | `route_stop` include_terminal, owner cascade | `atlas/tests/test_run_addressing_table.py` |
| `S09` ws stop | run_id only | finished parent | remote terminal, past the old 300 s window | children stopped on owner | the record outlives its live children, so `resolve` still finds it | `atlas/tests/test_run_addressing_table.py` |
| `S10` ws stop | run_id only | finished parent | unreadable | unavailable, children untouched | as `S04` | `atlas/tests/test_run_addressing_table.py` |
| `S11` ws stop | run_id only | API follow-up | local | the named run and its children only; the follow-up keeps running | `get_for_user` hit on the finished run | `atlas/tests/test_run_addressing_table.py` |
| `S12` ws stop | conversation only | tracked | local | run cancelled | `active_for_conversation` | `atlas/tests/test_run_addressing_table.py` |
| `S13` ws stop | conversation only | tracked | remote live | owner cancels, status frame | `resolve` Remote via `run_for_conversation`, `route_stop` | `atlas/tests/test_run_addressing_table.py` |
| `S14` ws stop | conversation only | tracked | remote terminal | nothing to stop, no frame | conversation key released with the run | `atlas/tests/test_run_addressing_table.py` |
| `S15` ws stop | conversation only | tracked | unreadable | unavailable | `route_stop` -> `owner_unavailable` | `atlas/tests/test_run_addressing_table.py` |
| `S19` ws stop | conversation only | finished parent | local | children stopped | GAP: `active_for_conversation` skips terminal records (`registry.py:274-288`, `resolve` step 3) and `children_of_conversation` is never used | `atlas/tests/test_run_addressing_table.py` |
| `S20` ws stop | conversation only | finished parent | remote terminal | children stopped on owner | GAP: `run_for_conversation` returns live runs only (`run_directory.py:81-90`), so `resolve` finds nothing | `atlas/tests/test_run_addressing_table.py` |
| `S21` ws stop | conversation only | API follow-up | local | follow-up stopped | `active_for_conversation` finds it | `atlas/tests/test_run_addressing_table.py` |
| `S22` ws stop | conversation only | API follow-up | remote live | follow-up stopped on owner | `resolve` Remote by conversation, `route_stop` | `atlas/tests/test_run_addressing_table.py` |
| `S23` ws stop | both | tracked | local | the named run only, never the other conversation's | run id wins in `resolve` | `atlas/tests/test_run_addressing_table.py` |
| `S24` ws stop | both | tracked | remote live | owner cancels, status frame | `resolve` Remote, `route_stop` | `atlas/tests/test_run_addressing_table.py` |
| `S25` ws stop | both | tracked | remote terminal | status frame | as `S03` | `atlas/tests/test_run_addressing_table.py` |
| `S26` ws stop | both | tracked | unreadable | unavailable naming the run | as `S04` | `atlas/tests/test_run_addressing_table.py` |
| `S27` ws stop | both, run id stale | tracked | local | the conversation's run is stopped | `resolve` step 3 falls back to the local conversation run | `atlas/tests/test_run_addressing_table.py` |
| `S28` ws stop | both, run id stale | tracked | remote live | the conversation's run is stopped on its owner | `resolve` misses the run id, finds the conversation, `route_stop` | `atlas/tests/test_run_addressing_table.py` |
| `S30` ws stop | both | finished parent | remote terminal | children stopped on owner | as `S08` | `atlas/tests/test_run_addressing_table.py` |
| `S31` ws stop | both | API follow-up | local | named run and the conversation's live follow-up stopped (REST cancel does) | GAP: the run id wins and the conversation is ignored once it resolves (`resolve` returns one run) | `atlas/tests/test_run_addressing_table.py` |
| `S32` ws stop | both | API follow-up | remote live | as `S31`, on the owner | GAP: `resolve` returns the named run only, so `stop_addressed_run` stops that run only | `atlas/tests/test_run_addressing_table.py` |
| `S33` ws stop | neither | any | any (never read) | only the connection's plain turn is stopped; other runs untouched | `stop_addressed_run` addresses the connection's `connection_bound` run | `atlas/tests/test_run_addressing_table.py` |
| `S34` ws stop | run_id | tracked | remote live, two real replicas | owner stops, other users refused | via `route_stop` | `test_websocket_stop_via_non_owner_and_other_users_are_refused` |

## REST cancel

Code path: `cancel_run` (`agent_run_routes.py`) calls `cancel_api_run_addressed`
(`runs/commands.py`): `resolve` finds the run, then the local `cancel_api_run`
or `route_api_cancel` on the owner, then `route_api_follow_up_cancel` for a
finished named run.

| entry point | address given | run kind | location | expected outcome | today's code path | test |
|---|---|---|---|---|---|---|
| `R01` REST cancel | run_id only | API run (tracked) | remote live | owner cancels, 200 status | `route_api_cancel`, service-actor stop | `atlas/tests/test_run_addressing_table.py` |
| `R02` REST cancel | run_id only | API run | remote terminal | 200, `cancelled: false` | `include_terminal` stop, no follow-up | `atlas/tests/test_run_addressing_table.py` |
| `R03` REST cancel | run_id only | API run | unreadable | 503 retryable, run untouched | `route_api_cancel` -> `owner_unavailable` | `atlas/tests/test_run_addressing_table.py` |
| `R04` REST cancel | run_id only | UI-started, plain, off-record | remote live | 404 `run_not_found`, run untouched | owner `cancel_api_run_here` refuses a non-API id | `atlas/tests/test_run_addressing_table.py` |
| `R05` REST cancel | run_id only | any | unknown everywhere | 404 | `resolve` Nothing, `cancel_api_run` refusal | `atlas/tests/test_run_addressing_table.py` |
| `R06` REST cancel | run_id only | any | unreadable, id unknown | 503, never 404 | `route_api_cancel` -> `owner_unavailable` | `atlas/tests/test_run_addressing_table.py` |
| `R07` REST cancel | run_id only | finished API run | remote terminal, past the old 300 s window | run and its children stopped on owner | same as `S09` | `atlas/tests/test_run_addressing_table.py` |
| `R08` REST cancel | run_id only | finished parent (API-started) | remote terminal | children stopped | owner cascade; the cited test drives the user-actor stop, the API-sourced REST case has no test yet | n/a: untested; see `test_stopping_a_finished_parent_stops_its_live_child_on_the_owner` for the cascade |
| `R09` REST cancel | run_id only | API follow-up | named run local, follow-up remote | follow-up stopped on its owner | `route_api_follow_up_cancel` | `test_api_cancel_of_a_finished_run_held_here_stops_its_follow_up_elsewhere` |
| `R10` REST cancel | run_id only | API follow-up | named run remote terminal, follow-up here | follow-up stopped | `route_api_cancel`, then `route_api_follow_up_cancel` | `test_api_cancel_of_a_finished_run_stops_its_follow_up_elsewhere` |
| `R11` REST cancel | run_id only | API follow-up | unreadable on the follow-up lookup | 503, not `cancelled: false` | `route_api_follow_up_cancel` returns the failure | n/a: untested; `test_api_cancel_reports_a_follow_up_it_could_not_stop` covers the owner timing out (504) |
| `R12` REST cancel | conversation only, both, neither | any | any | n/a: the path takes a run id only | n/a | n/a |

## ws chat (steer)

Code path: local run (`main.py`), then `steer_addressed_run`
(`runs/commands.py`: `resolve`, `route_steer`), then a new turn.

| entry point | address given | run kind | location | expected outcome | today's code path | test |
|---|---|---|---|---|---|---|
| `C01` ws chat | conversation (or both) | tracked | remote live, loop draining | message queued on the owner, `steering_queued` | `route_steer`, owner `_steer` | `atlas/tests/test_run_addressing_table.py` |
| `C02` ws chat | conversation | tracked | remote live, loop not draining | refused `conversation_busy`, client keeps running state | owner `_steer` | `atlas/tests/test_run_addressing_table.py` |
| `C03` ws chat | conversation | tracked | remote terminal | starts a new turn | `run_for_conversation` is live only | `atlas/tests/test_run_addressing_table.py` |
| `C04` ws chat | conversation | tracked | unreadable | refused `run_control_unavailable`, no turn started | `RunDirectoryUnavailable` -> `directory_unavailable_frame` | `atlas/tests/test_run_addressing_table.py` |
| `C05` ws chat | conversation | finished parent | remote terminal | starts a new turn (children keep running) | conversation key released | `atlas/tests/test_run_addressing_table.py` |
| `C06` ws chat | conversation | API follow-up | remote live | steered like any tracked run | `route_steer` | `atlas/tests/test_run_addressing_table.py` |
| `C07` ws chat | conversation | off-record turn | remote live | refused `off_record`, nothing written | off-record guard in `RedisRunControlBus.send` | `atlas/tests/test_run_addressing_table.py` |
| `C08` ws chat | conversation | tracked | local, loop draining | message queued, `steering_queued` | the local tracked-run branch of `chat` | `atlas/tests/test_run_addressing_table.py` |
| `C09` ws chat | conversation | tracked | local, loop not draining | refused `conversation_busy`; unlike the routed refusal (`steer_frame`) the local frame carries no `steering: true` | the local not-draining branch of `chat` | `atlas/tests/test_run_addressing_table.py` |
| `C10` ws chat | conversation | off-record turn | local | refused `off_record`, as the remote path does, unless the run is itself off-record | the local run branch of `chat` compares `turn_is_off_record` with `RunRecord.off_record` | `atlas/tests/test_run_addressing_table.py` |
| `C13` ws chat | run_id only, neither | any | any | starts a new turn, except that a frame naming no conversation continues the plain turn this connection runs (steered into it, or refused `conversation_busy`) | the `chat` branch looks up `active_bound_to` when the frame has no conversation | n/a: not exercised here |
| `C14` ws chat | conversation | tracked | remote live, two real replicas | steer reaches the loop once | via `route_steer` | `test_steer_via_non_owner_reaches_the_loop_once` |
| `C15` ws chat | conversation | tracked | retry lands on the owner after a routed attempt, same `request_id` | queued once | owner `_steer` and the local branch of `chat` both call `RunRegistry.steer` | `atlas/tests/test_run_addressing_table.py` |
| `C16` ws chat | conversation | tracked | retry is routed after a local attempt on the owner, same `request_id` | queued once | the same registry call, reached through `route_steer` | `atlas/tests/test_run_addressing_table.py` |
| `C17` ws chat | conversation | tracked | remote live, two real replicas, the retry switches replicas | queued once, in either order | the same registry call | `test_steer_retried_on_another_replica_lands_once` |

## ws tool_approval_response

Code path: `decide_tool_approval` (`approval_resolution.py:136-250`) settles the
write-once outcome, `_resume_waiting_run` (`main.py`) resumes a local run,
and `nudge_approval` resolves the run and calls `route_approval` to wake a remote owner.

| entry point | address given | run kind | location | expected outcome | today's code path | test |
|---|---|---|---|---|---|---|
| `P01` ws approval | run_id (with any hint) | tracked | remote live | the owner's tool resumes once | outcome write, `route_approval`, owner `_approval` | `atlas/tests/test_run_addressing_table.py` |
| `P02` ws approval | no run id | tracked | remote live | resolves the same request; no wake-up, the executor polls | `approval_nudge` with no run id returns None | `atlas/tests/test_run_addressing_table.py` |
| `P03` ws approval | run_id | tracked | remote terminal | answer recorded, no nudge | `resolve` without `include_terminal` | `atlas/tests/test_run_addressing_table.py` |
| `P04` ws approval | run_id | tracked | unreadable | answer recorded, tool resumes on its next poll | `route_approval` returns None | `atlas/tests/test_run_addressing_table.py` |
| `P05` ws approval | any | tracked, plain, off-record | local | the local future resolves | `find_open_request`, `accepted` | n/a: not exercised by the table tests |
| `P06` ws approval | run_id | tracked | remote live, two real replicas | tool runs once | via `route_approval` | `test_approval_via_non_owner_runs_the_tool_once` |
| `P07` ws approval | tool_call_id only, shared by two runs | tracked | local | refused `unknown_tool_approval`; neither request settles | `find_open_request` finds two matches | `atlas/tests/test_run_addressing_table.py` |
| `P08` ws approval | tool_call_id plus run_id | tracked | local | the hinted run's request is selected | `_bare_matches` filters on `run_id` | `atlas/tests/test_run_addressing_table.py` |
| `P09` ws approval | run_id | tracked | two runs reuse one invocation id | each run's nudge has its own request id and dedup record | `route_approval` scopes the id to `(run_id, invocation_id)` | `atlas/tests/test_run_addressing_table.py` |

## Counts

Rows `S01-S34`, `R01-R12`, `C01-C17` and `P01-P09`, less the eight retired
`untracked turn` ids (`S05`, `S06`, `S16`, `S17`, `S18`, `S29`, `C11`, `C12`):
64 rows. `GAP` rows (4): `S19`, `S20`, `S31`, `S32`. All are
strict `xfail` tests.
