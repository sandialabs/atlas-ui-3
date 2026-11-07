# Run addressing table

Issue #1128. Which run does a control frame reach, on which replica, and what
does the user see? One table, written before the resolver that will replace the
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
  `action: stop`: the two branches are the same code (`main.py:3268-3296`).
  `REST cancel` is `POST /agent-runs/{run_id}/cancel`. `ws chat` is a chat
  frame that steers a running turn. `ws approval` is `tool_approval_response`.
- **Address.** What the frame names: `run_id`, `conversation_id`, both, neither.
  When both are given the run id is tried first, and the conversation is the
  fallback for a run id nobody knows.
- **Kind.** `tracked` is a run in the registry. `untracked turn` is a chat turn
  that is not a run (no chat history, or local / incognito save mode): it is
  only the connection's `active_chat_task`, never in the registry or the
  directory. `finished parent` is a terminal run whose sub-conversation runs are
  still live (stopping it cascades, `registry.py:766-815`). `API follow-up` is a
  finished API run whose conversation now holds a live follow-up with a new run
  id. `off-record turn` is an incognito or not-saved turn: for stop and approval
  it is an untracked turn; it only differs for steering.
- **Location.** `local` is this replica's registry. `remote live` is another
  replica, run live. `remote terminal` is another replica, run finished, its
  directory record still kept (300 s). `unreadable` is the directory (Redis)
  failing. Record expiry is its own row where it changes the answer.

Expected outcomes are what the user observes. "No frame" means the client gets
nothing back; "status" is a `run_status` frame; "unavailable" is an error frame
with `error_type: run_control_unavailable` that keeps the client's running state.

## Collapsed cells

- `REST cancel` takes only a run id in the path: `conversation only`, `neither`
  and `both` do not exist (row `R12`). The conversation is looked up from the record.
- `REST cancel` is API-runs only: a run the UI started, an untracked turn and an
  off-record turn all answer `run_not_found` (`external_runs.py:460-476`).
- `ws chat` reads `conversation_id` only (`main.py:2209`); a `run_id` on a chat
  frame is ignored. `run id only` equals `neither`, `both` equals `conversation
  only`. With no conversation a chat frame never looks anything up: it starts a
  turn, or steers the connection's own untracked agent loop when that has no
  conversation either (`should_steer`).
- An untracked turn is never in the directory, so it has no `remote` rows (the
  one consequence is row `C12`). It also has no run id, so `run id only` for it
  is a stale or foreign id (row `S05`).
- An approval is keyed by `invocation_id`, which names exactly one request. A
  bare `tool_call_id` is ambiguous when two runs hold the same provider id
  (`call_0`): `run_id` or `conversation_id` then disambiguates
  (`approval_manager.py:355-388`, `approval_resolution.py:100-133`), rows `P07`
  and `P08`. With an `invocation_id` every address resolves the same request,
  so the other approval rows are per location. The directory only wakes the owner (`route_approval`); the
  write-once outcome settles the approval, so an unreadable directory or a
  missing nudge delays the tool by one poll and never loses the answer.
- A finished parent has no `remote live` row (live would make it `tracked`).

## ws stop (stop_streaming, agent_control stop)

Code path: `_stop_remote_run` (`main.py:723-768`), then `_cancel_addressed_run`
(`main.py:686-710`), then the untracked slot (`main.py:3274-3281`). The tests
mirror that glue in `ws_stop`.

| entry point | address given | run kind | location | expected outcome | today's code path | test |
|---|---|---|---|---|---|---|
| `S01` ws stop | run_id only | tracked | local | run cancelled, no frame | `_find_addressed_run` hit, `registry.cancel` | `atlas/tests/test_run_addressing_table.py` |
| `S02` ws stop | run_id only | tracked | remote live | owner cancels, status frame | `route_stop(run_id)`, owner `_stop` | `atlas/tests/test_run_addressing_table.py` |
| `S03` ws stop | run_id only | tracked | remote terminal | status frame, run stays as it ended | `route_stop` with `include_terminal` | `atlas/tests/test_run_addressing_table.py` |
| `S04` ws stop | run_id only | tracked | unreadable | unavailable, run untouched | `route_stop` -> `owner_unavailable` -> `stop_frame` | `atlas/tests/test_run_addressing_table.py` |
| `S05` ws stop | run_id only | untracked turn | local | no frame; the untracked turn is not stopped (the id names a run) | `_cancel_addressed_run` returns `bool(run_id)` (`main.py:706`) | `atlas/tests/test_run_addressing_table.py` |
| `S06` ws stop | run_id only | untracked turn | unreadable | unavailable naming the id | as `S04` | `atlas/tests/test_run_addressing_table.py` |
| `S07` ws stop | run_id only | finished parent | local | children stopped | `registry.cancel` cascade above the terminal check | `atlas/tests/test_run_addressing_table.py` |
| `S08` ws stop | run_id only | finished parent | remote terminal | children stopped on owner, status frame | `route_stop` include_terminal, owner cascade | `atlas/tests/test_run_addressing_table.py` |
| `S09` ws stop | run_id only | finished parent | remote terminal, record expired | children stopped on owner | GAP: `owner_entry` finds nothing after 300 s, `route_stop` returns None and `main.py:750-758` no-ops, while the owner's registry keeps the parent 30 min (`run_directory.py:42`) | `atlas/tests/test_run_addressing_table.py` |
| `S10` ws stop | run_id only | finished parent | unreadable | unavailable, children untouched | as `S04` | `atlas/tests/test_run_addressing_table.py` |
| `S11` ws stop | run_id only | API follow-up | local | the named run and its children only; the follow-up keeps running | `get_for_user` hit on the finished run | `atlas/tests/test_run_addressing_table.py` |
| `S12` ws stop | conversation only | tracked | local | run cancelled | `active_for_conversation` | `atlas/tests/test_run_addressing_table.py` |
| `S13` ws stop | conversation only | tracked | remote live | owner cancels, status frame | `route_stop(None, conv)` via `run_for_conversation` | `atlas/tests/test_run_addressing_table.py` |
| `S14` ws stop | conversation only | tracked | remote terminal | nothing to stop, no frame | conversation key released with the run | `atlas/tests/test_run_addressing_table.py` |
| `S15` ws stop | conversation only | tracked | unreadable | unavailable | `route_stop` -> `owner_unavailable`, no live untracked turn | `atlas/tests/test_run_addressing_table.py` |
| `S16` ws stop | conversation only | untracked turn | local | the untracked turn is stopped | `_cancel_addressed_run` False, then `task.cancel()` | `atlas/tests/test_run_addressing_table.py` |
| `S17` ws stop | conversation only | untracked turn | local, turn in another conversation | nothing stopped | GAP: the fallback (`main.py:3274-3281`) never compares `active_chat_task["conversation_id"]` with the named conversation | `atlas/tests/test_run_addressing_table.py` |
| `S18` ws stop | conversation only | untracked turn | unreadable | live turn stopped silently; none live: unavailable | `main.py:762-766` | `atlas/tests/test_run_addressing_table.py` |
| `S19` ws stop | conversation only | finished parent | local | children stopped | GAP: `active_for_conversation` skips terminal records (`registry.py:274-288`, `main.py:713-720`) and `children_of_conversation` is never used | `atlas/tests/test_run_addressing_table.py` |
| `S20` ws stop | conversation only | finished parent | remote terminal | children stopped on owner | GAP: `run_for_conversation` returns live runs only (`run_directory.py:81-90`), so `route_stop` finds nothing | `atlas/tests/test_run_addressing_table.py` |
| `S21` ws stop | conversation only | API follow-up | local | follow-up stopped | `active_for_conversation` finds it | `atlas/tests/test_run_addressing_table.py` |
| `S22` ws stop | conversation only | API follow-up | remote live | follow-up stopped on owner | `route_stop(None, conv)` | `atlas/tests/test_run_addressing_table.py` |
| `S23` ws stop | both | tracked | local | the named run only, never the other conversation's | run id wins in `_find_addressed_run` | `atlas/tests/test_run_addressing_table.py` |
| `S24` ws stop | both | tracked | remote live | owner cancels, status frame | `route_stop(run_id)` | `atlas/tests/test_run_addressing_table.py` |
| `S25` ws stop | both | tracked | remote terminal | status frame | as `S03` | `atlas/tests/test_run_addressing_table.py` |
| `S26` ws stop | both | tracked | unreadable | unavailable naming the run | as `S04` | `atlas/tests/test_run_addressing_table.py` |
| `S27` ws stop | both, run id stale | tracked | local | the conversation's run is stopped | `main.py:747-749` falls back to local | `atlas/tests/test_run_addressing_table.py` |
| `S28` ws stop | both, run id stale | tracked | remote live | the conversation's run is stopped on its owner | `route_stop(run_id)` None, then `route_stop(None, conv)` | `atlas/tests/test_run_addressing_table.py` |
| `S29` ws stop | both, run id stale | untracked turn | local | no frame; the turn keeps running (a stale id never stops an unrelated turn) | `main.py:706`, documented on `_cancel_addressed_run` | `atlas/tests/test_run_addressing_table.py` |
| `S30` ws stop | both | finished parent | remote terminal | children stopped on owner | as `S08` | `atlas/tests/test_run_addressing_table.py` |
| `S31` ws stop | both | API follow-up | local | named run and the conversation's live follow-up stopped (REST cancel does) | GAP: the run id wins and the conversation is ignored once it resolves (`main.py:713-720`) | `atlas/tests/test_run_addressing_table.py` |
| `S32` ws stop | both | API follow-up | remote live | as `S31`, on the owner | GAP: `route_stop` stops the named run only (`run_control.py:236-258`) | `atlas/tests/test_run_addressing_table.py` |
| `S33` ws stop | neither | any | any (never read) | only the connection's untracked turn is stopped; tracked runs untouched | `_stop_remote_run` returns False at `main.py:735`, then the untracked slot | `atlas/tests/test_run_addressing_table.py` |
| `S34` ws stop | run_id | tracked | remote live, two real replicas | owner stops, other users refused | via `route_stop` | `test_websocket_stop_via_non_owner_and_other_users_are_refused` |

## REST cancel

Code path: `cancel_run` (`agent_run_routes.py:406-433`): the local registry
first (`cancel_api_run`), then `route_api_cancel`, then
`route_api_follow_up_cancel` for a finished named run.

| entry point | address given | run kind | location | expected outcome | today's code path | test |
|---|---|---|---|---|---|---|
| `R01` REST cancel | run_id only | API run (tracked) | remote live | owner cancels, 200 status | `route_api_cancel`, service-actor stop | `atlas/tests/test_run_addressing_table.py` |
| `R02` REST cancel | run_id only | API run | remote terminal | 200, `cancelled: false` | `include_terminal` stop, no follow-up | `atlas/tests/test_run_addressing_table.py` |
| `R03` REST cancel | run_id only | API run | unreadable | 503 retryable, run untouched | `route_api_cancel` -> `owner_unavailable` | `atlas/tests/test_run_addressing_table.py` |
| `R04` REST cancel | run_id only | UI-started, untracked, off-record | remote live | 404 `run_not_found`, run untouched | owner `cancel_api_run_here` refuses a non-API id | `atlas/tests/test_run_addressing_table.py` |
| `R05` REST cancel | run_id only | any | unknown everywhere | 404 | `route_api_cancel` None, then `_refused` | `atlas/tests/test_run_addressing_table.py` |
| `R06` REST cancel | run_id only | any | unreadable, id unknown | 503, never 404 | `route_api_cancel` -> `owner_unavailable` | `atlas/tests/test_run_addressing_table.py` |
| `R07` REST cancel | run_id only | finished API run | remote terminal, record expired | run and its children stopped on owner | GAP: same cause as `S09`; `_remote_entry` None, 404 (`agent_run_routes.py:430-432`) | `atlas/tests/test_run_addressing_table.py` |
| `R08` REST cancel | run_id only | finished parent | remote terminal | children stopped | owner cascade | `test_stopping_a_finished_parent_stops_its_live_child_on_the_owner` |
| `R09` REST cancel | run_id only | API follow-up | named run local, follow-up remote | follow-up stopped on its owner | `route_api_follow_up_cancel` | `test_api_cancel_of_a_finished_run_held_here_stops_its_follow_up_elsewhere` |
| `R10` REST cancel | run_id only | API follow-up | named run remote terminal, follow-up here | follow-up stopped | `route_api_cancel`, then `route_api_follow_up_cancel` | `test_api_cancel_of_a_finished_run_stops_its_follow_up_elsewhere` |
| `R11` REST cancel | run_id only | API follow-up | unreadable on the follow-up lookup | 503, not `cancelled: false` | `route_api_follow_up_cancel` returns the failure | n/a: untested; `test_api_cancel_reports_a_follow_up_it_could_not_stop` covers the owner timing out (504) |
| `R12` REST cancel | conversation only, both, neither | any | any | n/a: the path takes a run id only | n/a | n/a |

## ws chat (steer)

Code path: local tracked run (`main.py:2177-2231`), then the directory
(`main.py:2237-2290`: `remote_run_for_conversation`, `route_steer`), then the
connection's untracked turn (`should_steer`), then a new turn.

| entry point | address given | run kind | location | expected outcome | today's code path | test |
|---|---|---|---|---|---|---|
| `C01` ws chat | conversation (or both) | tracked | remote live, loop draining | message queued on the owner, `steering_queued` | `route_steer`, owner `_steer` | `atlas/tests/test_run_addressing_table.py` |
| `C02` ws chat | conversation | tracked | remote live, loop not draining | refused `conversation_busy`, client keeps running state | owner `_steer` | `atlas/tests/test_run_addressing_table.py` |
| `C03` ws chat | conversation | tracked | remote terminal | starts a new turn | `run_for_conversation` is live only | `atlas/tests/test_run_addressing_table.py` |
| `C04` ws chat | conversation | tracked | unreadable | refused `run_control_unavailable`, no turn started | `RunDirectoryUnavailable` -> `directory_unavailable_frame` | `atlas/tests/test_run_addressing_table.py` |
| `C05` ws chat | conversation | finished parent | remote terminal | starts a new turn (children keep running) | conversation key released | `atlas/tests/test_run_addressing_table.py` |
| `C06` ws chat | conversation | API follow-up | remote live | steered like any tracked run | `route_steer` | `atlas/tests/test_run_addressing_table.py` |
| `C07` ws chat | conversation | off-record turn | remote live | refused `off_record`, nothing written | off-record guard in `RedisRunControlBus.send` | `atlas/tests/test_run_addressing_table.py` |
| `C08` ws chat | conversation | tracked | local, loop draining | message queued, `steering_queued` | `main.py:2177-2214` | `atlas/tests/test_run_addressing_table.py` |
| `C09` ws chat | conversation | tracked | local, loop not draining | refused `conversation_busy` | `main.py:2215-2231` | `atlas/tests/test_run_addressing_table.py` |
| `C10` ws chat | conversation | off-record turn | local | refused `off_record`, as the remote path does | GAP: the local branch (`main.py:2177-2214`) never reads `turn_is_off_record`, so incognito text is queued into a recorded run | `atlas/tests/test_run_addressing_table.py` |
| `C11` ws chat | conversation | untracked turn | local | queued into the connection's loop when the conversation matches, else a new turn | `should_steer` (`agent/steering.py:84-110`) | `test_agent_steering_through_chat_service` |
| `C12` ws chat | conversation | untracked turn | on another replica | busy refusal | GAP: the directory cannot see an untracked turn (`main.py:2237-2290`), so a second turn starts beside it | n/a: needs two real processes, none exists yet (step E retires the untracked turn) |
| `C13` ws chat | run_id only, neither | any | any | starts a new turn, except that a frame naming no conversation is steered into the connection's own untracked agent loop that also has none (a chat frame names its conversation, never a run) | `main.py:2209`, no lookup when the conversation is None, then `should_steer` | `test_both_none_conversation_ids_steer` for the steer; the new-turn case is not exercised here |
| `C14` ws chat | conversation | tracked | remote live, two real replicas | steer reaches the loop once | via `route_steer` | `test_steer_via_non_owner_reaches_the_loop_once` |

## ws tool_approval_response

Code path: `decide_tool_approval` (`approval_resolution.py:136-250`) settles the
write-once outcome, `_resume_waiting_run` (`main.py:1167`) resumes a local run,
and `nudge_approval_owner` calls `route_approval` to wake a remote owner.

| entry point | address given | run kind | location | expected outcome | today's code path | test |
|---|---|---|---|---|---|---|
| `P01` ws approval | run_id (with any hint) | tracked | remote live | the owner's tool resumes once | outcome write, `route_approval`, owner `_approval` | `atlas/tests/test_run_addressing_table.py` |
| `P02` ws approval | no run id | tracked | remote live | resolves the same request; no wake-up, the executor polls | `route_approval(None)` returns None | `atlas/tests/test_run_addressing_table.py` |
| `P03` ws approval | run_id | tracked | remote terminal | answer recorded, no nudge | `_remote_entry` without terminal | `atlas/tests/test_run_addressing_table.py` |
| `P04` ws approval | run_id | tracked | unreadable | answer recorded, tool resumes on its next poll | `route_approval` returns None | `atlas/tests/test_run_addressing_table.py` |
| `P05` ws approval | any | tracked, untracked, off-record | local | the local future resolves | `find_open_request`, `accepted` | n/a: not exercised by the table tests |
| `P06` ws approval | run_id | tracked | remote live, two real replicas | tool runs once | via `route_approval` | `test_approval_via_non_owner_runs_the_tool_once` |
| `P07` ws approval | tool_call_id only, shared by two runs | tracked | local | refused `unknown_tool_approval`; neither request settles | `find_open_request` finds two matches | `atlas/tests/test_run_addressing_table.py` |
| `P08` ws approval | tool_call_id plus run_id | tracked | local | the hinted run's request is selected | `_bare_matches` filters on `run_id` | `atlas/tests/test_run_addressing_table.py` |

## Counts

Rows `S01-S34`, `R01-R12`, `C01-C14` and `P01-P08`: 68 rows. `GAP` rows (9):
`S09`, `S17`, `S19`, `S20`, `S31`, `S32`, `R07`, `C10`, `C12`. Eight are strict
`xfail` tests (`C10` runs for two off-record flags); `C12` has no test yet.
