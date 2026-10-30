# Execution State

Last updated: 2026-10-30

How a reopened conversation reconstructs an in-progress run (issue #1122).
Related: [Interrupted Turns](interrupted-turns.md), [Interactive Tool Elicitation](elicitation.md).

## What a client can fetch

While a run is executing, the session history grows at a step boundary. A
fetch in the middle of a step, or a fetch served by a replica that did not
start the run, has to show the same tool rows and approval cards as a client
that stayed on the conversation.

That view is one JSON document per conversation:

| Field | Meaning |
| --- | --- |
| `revision` | Monotonic counter owned by the run. A client ignores an older revision of the same run. |
| `invocations` | One row per tool call. Identity is `run_id:step:tool_call_id`. |
| `pending_requests` | Approval and elicitation frames still outstanding. |
| `activity` | `executing`, `sleeping`, or `waiting_for_approval`. |
| `status` | Run lifecycle the sidebar already uses. Sleep stays `running`. |

`GET /api/conversations/{id}` attaches the document as `execution` and merges
invocation rows into `messages`. The merge is idempotent: a row that already
carries `invocation_id` is updated in place, and a legacy row with only
`tool_call_id` is reused once.

## Where it lives

The document is coordination state, not the transcript of record. Durable
tool rows still flush into chat history when the step finishes.

| Mode | Store |
| --- | --- |
| `SESSION_REPOSITORY_TYPE=redis` | Shared Redis (`atlas:exec:{email}:{conversation}` plus a revision key). Any replica answers a fetch. |
| memory session repository | Process-local store. Single-instance mode only, the same boundary sessions already have. |

A Redis write uses WATCH/MULTI/EXEC on the revision key so two replicas
applying events do not lose an update. Approval outcomes are a separate key;
the first write wins, and a later response returns that outcome.

`RunRegistry` and `ToolApprovalManager` stay the in-process wait and cancel
machinery. They are not what a fetch reads. Run tasks stay on the replica
that started them.

## Approval and sleep

A timeout, a rejection, and an elicitation timeout emit a settled `tool_error`
(`expired` or `rejected`). That leaves `waiting_for_input`, drops the pending
card, and keeps a failed transcript row. Reopening the conversation does not
replay the card.

An approval response is idempotent. The first one is accepted. A duplicate
returns `tool_approval_settled` and does not resume the run. A bare
`tool_call_id` resolves only when exactly one pending request has it, because
providers reuse `call_0` across steps and runs.

`atlas_sleep` is `activity=sleeping` and a tool row with status `sleeping`.
Earlier tool rows stay visible. It is not an approval and it is not
"Agent thinking...".

When the run completes, fails, or is cancelled, that status is written into
the same document and the pending set is cleared. A later tool frame for
that run does not bring it back. A run list keeps a local terminal status
when the shared document has not caught up, and it does not add a finished
projection as a new active run.

Pending approval `arguments` are stored in full. Approving the card sends
those arguments. Other fields in the document are shortened so a fetch stays
bounded.

A shared approval outcome exists only for a call that has an invocation id,
and the key includes the authenticated owner. A turn with no run does not
write `tool:call_0` into the store, so one user's approval or timeout cannot
answer a later call. A closing frame never opens a different run. Once a run
has been replaced, its later frames, including a delayed `tool_start`, are
ignored.

## Client

The joined-view poll and a saved-conversation load reconcile `execution` into
the messages on screen (`frontend/src/utils/executionState.js`). Tool frames
update the invocation they name. A frame that only has `tool_call_id` updates
legacy rows that have no invocation id.
