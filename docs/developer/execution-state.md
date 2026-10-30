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
carries `invocation_id` is updated in place, and an open legacy row with only
`tool_call_id` is reused once. A settled history row keeps its status, result,
and arguments, because history is the full copy and the projection may lag
it. A finished run merges nothing, so rows a rewind or edit removed do not
come back. Canvas calls are not projected: they render in the canvas panel
and never become a transcript row.

## Where it lives

The document is coordination state, not the transcript of record. Durable
tool rows still flush into chat history when the step finishes.

| Mode | Store |
| --- | --- |
| `SESSION_REPOSITORY_TYPE=redis` | Shared Redis (`atlas:exec:{email}:{conversation}` plus a revision key). Any replica answers a fetch. |
| memory session repository | Process-local store. Single-instance mode only, the same boundary sessions already have. |

A Redis write uses WATCH/MULTI/EXEC on the revision key so two replicas
applying events do not lose an update. Inside one replica, writes are
serialized per conversation, not store-wide. The memory store sweeps expired
documents and caps how many it keeps. Approval outcomes are a separate key;
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
providers reuse `call_0` across steps and runs. Only the caller's own requests
count toward that match, so another user's pending `call_0` neither blocks it
nor gets settled by it. A response that names an `invocation_id` resolves that
exact invocation and never falls back to its `tool_call_id`. A shared outcome
is written only for a pending request the caller owns, under that request's
own invocation id.

A response that only writes the shared outcome, because the executor is on
another replica, resumes nothing locally. A response that names a run or
conversation this replica does not hold does not fall back to guessing the
user's single waiting run.

An elicitation timeout settles the question, not the tool. The dialog closes
and the card leaves the pending set, but the tool row keeps its status until
the tool reports its own result.

Stop closes every open row, including `sleeping` and `in_progress` ones. The
`tool_interrupted` notice carries the invocation identity and is written into
the document, so the live row, a reload, and a joined tab all show it stopped.

A `tool_start` for an invocation drops that invocation's approval card, in
the projection and in the executor's run registry. The answer may have landed
on another replica, and the tool starting is what proves it was approved, so
a reload while the tool runs does not offer Approve/Reject again.

`atlas_sleep` is `activity=sleeping` and a tool row with status `sleeping`
only after the tool has started. While it is still waiting for approval the
invocation stays `calling`, the view shows the approval card and not a row,
and the activity stays `waiting_for_approval`. Earlier tool rows stay visible.
It is not "Agent thinking...".

When the run completes, fails, or is cancelled, that status is written into
the same document, the pending set is cleared, and any invocation still open
is marked `interrupted`. A later tool frame for
that run does not bring it back. A run list keeps a local terminal status
when the shared document has not caught up, ignores a document from a
different run than the one this replica is executing, and it does not add a finished
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
legacy rows that have no invocation id. An identified frame falls back to a
legacy row only while that row is still open; a settled legacy `call_0` from
an earlier turn is left alone and the new row is added beside it.
