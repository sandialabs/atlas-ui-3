# Plan: Authoritative run state for reopened conversations (#1122)

Issue: https://github.com/sandialabs/atlas-ui-3/issues/1122
Branch: `fix/1122-reopened-run-state`

## Problem

A conversation does not show the same execution state after navigate-away /
reconnect as it does while the user watches it. Live state is split across
the process-local run registry, the session history (flushed only at a step
boundary), and the open token segment. Approval timeout and rejection never
emit a settle frame, so the run stays `waiting_for_input` and the card is
replayed forever. Pending cards are delivered twice (restore and `list_runs`).
Provider `tool_call_id`s are reused across steps and runs, and both the
approval manager and the client treat that id as a global key. `atlas_sleep`
is shown as thinking or as a pending approval.

## Contract

One serializable execution projection per conversation, owned by the run:

- `revision` — monotonic, incremented on every mutation, compared only within
  a run.
- `invocations[]` — one row per call, identity `run_id:step:tool_call_id`,
  status `calling | in_progress | sleeping | completed | failed | interrupted
  | expired | rejected`.
- `pending_requests[]` — approval and elicitation frames still outstanding,
  each carrying `run_id`, `conversation_id`, and `invocation_id`.
- `activity` — `executing | sleeping | waiting_for_approval`.
- `status` — the run lifecycle the sidebar already understands. Sleep stays
  `running` with `activity=sleeping`. Approval stays `waiting_for_input`.

The projection is coordination state. When `SESSION_REPOSITORY_TYPE=redis` it
is stored in the shared Redis pool (`atlas:exec:{email}:{conversation_id}`),
so any replica can answer `GET /api/conversations/{id}`. Durable tool rows
still flush into chat history at the step boundary. With the memory session
repository the same store is process-local; that is the single-instance mode,
the same boundary the session repository already has. `RunRegistry` and
`ToolApprovalManager` stay the in-process wait/cancel machinery. They are not
the source of truth for a fetch.

Approval outcomes are a separate Redis key, first write wins, so a duplicate
or cross-replica response returns the outcome already recorded.

## Changes

1. `atlas/application/chat/runs/execution_state.py` — projection, reducer,
   memory store, Redis store, merge into a conversation payload.
2. Publish an invocation at the start of `execute_single_tool` (before the
   step waits), and fold every tool frame into the projection. Stamp
   `invocation_id`, `run_id`, `step` on frames. Pass `agent_step` from both
   tool loops.
3. Timeout and rejection emit `tool_error` (status `expired` / `rejected`)
   before the approval request is dropped, so the registry leaves
   `waiting_for_input` and the recorder persists the row. Elicitation timeout
   emits the same kind of settle frame.
4. Approval manager keys by `invocation_id` when the call has one, and a bare
   `tool_call_id` resolves only when exactly one pending request has it.
   A second response returns the stored outcome instead of "unknown".
   Responses that land on another replica are written to Redis; the executor
   waits on the local future or that record.
5. `list_runs` stops replaying pending cards. Restore still replays them
   once. The client dedupes by request identity and upserts tool rows by
   invocation identity. `unknown_tool_approval` drops the stale card and does
   not end the turn or the joined-view poll.
6. `atlas_sleep` / `atlas_agent_sleep` is status `sleeping`. The busy
   indicator says so. A snapshot with no pending request removes a phantom
   approval card. Earlier invocations stay in the projection.

`GET /api/conversations/{id}` attaches `execution` and merges invocation rows
into `messages`, including a stored conversation whose run is executing on
another replica.

## Tests

- Reducer: ten parallel invocations mid-step (running, completed, one
  pending) and an `atlas_sleep` that does not look like an approval.
- Two Redis stores, one fakeredis server: a write on one is the read on the
  other.
- Executor timeout and rejection drive `RunRegistry.note_event` and leave the
  run running, with a failed transcript row.
- Elicitation timeout emits a settle frame.
- Two `call_0` approvals do not clobber each other. A duplicate response is
  the stored outcome.
- Frontend: approval dedupe, stale-card error does not end the stream,
  reused `tool_call_id` updates only its invocation, reconnect prefers a
  newer revision, sleep label.

## Out of scope

Live push to every socket and session resume stay #760. Run leases and orphan
detection stay #1091. This change makes the snapshot they will build on
authoritative and readable from any replica that shares Redis.

## Verification

Recorded 2026-10-29 from this worktree. `atlas.__file__` resolved to this tree, not the primary checkout. Redis compare-and-swap uses WATCH/MULTI/EXEC; the fakeredis in this environment does not implement EVAL.

- `ruff check` on the touched `atlas/` modules: all checks passed.
- `pytest` `test_execution_state.py` `test_reopened_run_approval.py` `test_approval_manager.py` `test_tool_approval_cleanup.py` `test_tool_call_persistence.py`: passed.
- `pytest` `test_run_in_flight_conversation.py` `test_multi_tool_calling.py` `test_tool_start_edited_args.py` `test_interrupted_turn_persistence.py` `test_run_stream_replay.py`: 97 passed, 1 failed. `test_stop_mid_tool_then_continue_sees_the_prior_work` raises during AppSettings setup because the local capability-token secret is a placeholder. The test body did not run.
- `bash test/pr-validation/test_pr1122_reopened_run_state.sh`: Passed 2, Failed 0. The scenario's replica, timeout, and alignment checks passed, then pytest reported 46 passed.
- Frontend vitest: `execution-state` (8), `transcript-alignment` (17), `tool-approval-websocket` (13), `agent-busy-indicator` (8), `use-messages-invocation` (3), `joined-run-refresh` (27), `sleep-tool-timer` (3), `tool-approval-message` (33). All passed.
- `npx eslint` on the touched frontend files: clean.
- `bash scripts/check-docs.sh`: OK (123 markdown files, 19 indexes, no orphans or broken links).

A browser pass of a live sleep or approval turn was not run. No browser tool was available, and that turn needs a model. The labels and the reconcile rules are covered by the vitest files above.

## Review follow-up (2026-10-30)

CI on the first push failed `websocketHandlers.test.js`: a stopped tool row with no `type` stayed `calling`. Tool-row matching now treats a typeless `tool_call_id` row as a tool row and leaves typed non-tool rows alone.

The review also required four behavior fixes, all in this follow-up:

- A terminal `run_status` is published from `RunRegistry.set_status` into the shared projection. `merge_run_snapshots` does not overlay a non-terminal projection onto a finished local run, and it does not list a finished projection as a detached active run.
- An approval response claims the shared outcome before it resolves the local future. A rejection that already won is what the local future returns.
- Pending approval arguments are not truncated.
- Approval cards and tool rows are deduped inside one `mapMessages` updater.

CodeQL notes in the same change: newline-strip the approval log fields, close the alignment scenario file, and drop the unused bindings in the race test. The `await task` expression that CodeQL called effectless now assigns its outcome and asserts cancellation.

Follow-up verification, same worktree, `atlas.__file__` still this tree:

- `ruff check` on the touched modules: all checks passed.
- `pytest` `test_execution_state.py` `test_reopened_run_approval.py` `test_approval_manager.py` `test_tool_approval_cleanup.py` `test_parallel_run_transport.py` `test_run_in_flight_conversation.py` `test_run_stream_replay.py`: 118 passed.
- `bash test/pr-validation/test_pr1122_reopened_run_state.sh`: Passed 2, Failed 0, then 53 pytest passed.
- Vitest: `websocketHandlers.test.js` 24, `tool-approval-websocket.test.js` 15, `execution-state` 8, `transcript-alignment` 17, `use-messages-invocation` 3. All passed. The previously failing stopped-tool assertion is in the 24.
- `npx eslint` on `websocketHandlers.js` and `tool-approval-websocket.test.js`: clean.
- `bash scripts/check-docs.sh`: OK (123 markdown files, 19 indexes).

A browser pass was still not run.

The next `ci-test` run failed `parallel-conversation-runs.test.js`: a visible `tool_start` is now applied with `mapMessages`, and that test still expected `addMessage`. The assertion follows the reducer. A background `tool_start` still must not call `mapMessages`.

Review on `c13ea029` required the approval store to stop using a global `tool:<tool_call_id>` key. Shared outcomes now require an invocation id and the owner's email. Untracked `call_0` requests get distinct local futures, and each remembered outcome is stored under that unique key. A closing frame never opens a different run. Once a run has been replaced, its later frames, including a delayed `tool_start`, are ignored. Reconnect counts invocation rows for the snapshot's run only. The in-memory store drops projections and approval outcomes older than the projection TTL and caps remembered outcomes. An indefinite approval wait backs off its poll; a finite wait stays at 0.2s so a short timeout still wins.

Verification after that review, same worktree, `atlas.__file__` this tree:

- `ruff check` on the touched modules: all checks passed.
- `pytest` `test_execution_state.py` `test_reopened_run_approval.py` `test_approval_manager.py` `test_tool_approval_cleanup.py`: 58 passed.
- `bash test/pr-validation/test_pr1122_reopened_run_state.sh`: Passed 2, Failed 0. The scenario checks passed, then pytest reported 58 passed.
- Vitest: `execution-state.test.js` 9, `tool-approval-websocket.test.js` 15, `parallel-conversation-runs.test.js` 12, `websocketHandlers.test.js` 24. All passed.
- `npx eslint` on `ChatContext.jsx`, `executionState.js`, and `execution-state.test.js`: clean.
- `bash scripts/check-docs.sh`: OK (123 markdown files, 19 indexes).

A browser pass of a live sleep or approval turn was still not run.

A later browser pass of an unapproved `atlas_sleep` showed a wake-up timer beside the Approve/Reject card. The invocation stayed `calling` until `tool_start`, and a reload shows the card without a tool row until the tool starts. After start the row is `sleeping` and the card is gone.

Verified for that fix, worktree, `atlas.__file__` this tree:

- `ruff check` on `execution_state.py` and the two test modules: all checks passed.
- `pytest` `test_execution_state.py` `test_execution_state_routes.py`: 33 passed.
- Vitest `execution-state.test.js`: 13 passed.
- `npx eslint` on `executionState.js` and `execution-state.test.js`: clean.
- `bash scripts/check-docs.sh`: OK.

A browser pass then showed every approved `atlas_sleep` reported as a user edit. The schema stores `seconds` as `3.0` and the browser sends `3`. Argument comparison now goes through JSON and compares values, so `3` and `3.0` are the same arguments and a real change still counts as an edit.

Verified for that comparison, worktree, `atlas.__file__` this tree:

- `ruff check` on `tool_executor.py` and `test_reopened_run_approval.py`: all checks passed.
- `pytest` `test_an_unchanged_number_is_not_an_edit` and `test_a_repeated_call_id_in_one_step_is_refused`: 2 passed.
