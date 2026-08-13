# Interrupted Turns

What happens when a chat turn is cancelled before it finishes — the Stop
button, a client disconnect, or `reset_session`. All three arrive as
`task.cancel()` on the background chat task, so they share one code path.

Related: [Architecture](architecture.md), [Progress Updates](progress-updates.md).

## The contract

A cancelled turn is **committed, not discarded**:

1. Everything that already completed is persisted — the user message, agent
   narration, and every recorded `tool_call` row — and `conversation_saved` is
   emitted, so the turn survives a page reload.
2. The turn is **closed** by a terminal assistant message carrying
   `metadata["interrupted"] = True`. Without it the saved history could end on
   the user's message and the next request would open `user -> user`, which
   strict-alternation providers reject.
3. A follow-up message in the same conversation can see what the agent already
   ran, so it does not redo the work.

## Where it is implemented

| Concern | Location |
| --- | --- |
| Commit on both the normal and cancelled path | `ChatService._commit_turn()` |
| Close a turn no mode runner closed | `application/chat/utilities/interrupted_turn.py` |
| Flush in-flight tool calls while unwinding | `agent/agentic_loop.py`, `modes/tools.py` |
| Keep already-streamed text | `modes/streaming_helpers.py` (`partial_sink`) |
| Model-visible record of the turn's tools | `application/chat/utilities/agent_digest.py` |

`CancelledError` is a `BaseException`, so a guard must name it: `except
asyncio.CancelledError`, `except BaseException` where the block re-raises, or
`except (Exception, asyncio.CancelledError)` where it deliberately swallows.
An `except Exception` will not see a cancel.

### Incognito

`ChatService.handle_chat_message` snapshots the save policy (incognito flag and
save floor) **before** the turn runs and passes it into `_commit_turn`. A cancel
site may call `end_session()` without awaiting the cancelled task, which clears
that state; a live lookup during cleanup could then read a torn-down incognito
session as savable.

## The tool digest

Persisted `tool_call` and `agent_intermediate` rows are display-only
(`DISPLAY_ONLY_MESSAGE_TYPES`) and the agent loop's working `assistant`/`tool`
transcript is local to the turn, so neither reaches a later request. Instead,
each agent turn's closing assistant message carries a compact digest of the
calls it made in `metadata["agent_tool_digest"]`, and
`ConversationHistory.get_messages_for_llm()` folds that digest into **that
message's content**.

Folding into an existing message (rather than appending a new one) keeps the
role sequence identical, so no provider sees back-to-back assistant turns and no
orphaned `tool` message is ever replayed.

Bounds, all in `agent_digest.py` and `domain/messages/models.py`:

- 300 characters of arguments and 400 of result per call; 30 calls per digest
  (head and tail kept, the elided middle announced). Those budgets are spent on
  *source* characters, so a fetched HTML page does not pay its budget to its own
  markup; the escaped form has a separate ceiling (twice the budget) so a value
  made only of delimiters still cannot crowd later calls out.
- A digest never exceeds `MAX_FOLDED_DIGEST_CHARS`.
- A request folds at most `MAX_FOLDED_DIGESTS` digests, newest first, within
  that character budget; an oversized newest digest is trimmed on a line
  boundary rather than dropped.

### Tool output is untrusted data

Results and arguments come from fetched pages, external MCP servers, and command
output, and they end up inside **assistant-role** content that is replayed for
several turns. The digest header states that the quoted text is verbatim tool
output and not instruction, each field is wrapped in a `<<<…>>>` fence, and `&`,
`<`, `>` are escaped so no value can close or forge a delimiter. `tool_name` is
whitespace-collapsed and length-capped so it cannot inject extra digest lines.
`status` carries only literals written by the recorder, but it goes through the
same quoting, so "every value on the line is escaped" holds by construction
rather than by an audit of the call sites.

## Frontend contract

| Signal | Effect |
| --- | --- |
| `tool_interrupted` (top-level WS event) | Closes the live tool row: `status: 'interrupted'`, "Stopped" label |
| `intermediate_update` → `tool_result` with `status: 'interrupted'` | Same, for the intermediate channel |
| Persisted row `status: "interrupted"` | Renders `STOPPED` (gray, `⏹`) with a "Stopped Before Result" detail heading |

A stopped call is deliberately **not** `failed`: the user stopping their own
turn is not a tool error, and the two must be distinguishable. The status ladder
in `Message.jsx` names each terminal state; anything unrecognized (for example a
legacy row saved with no status) renders as a neutral `NO STATUS` rather
than claiming an outcome.

`tool_interrupted` is best-effort — on a disconnect the socket is already gone,
which is exactly the case where the live view no longer matters. The persisted
row is the source of truth after a reload.

## Testing notes

`atlas/tests/test_interrupted_turn_persistence.py` covers the paths end to end,
including cancels delivered by a real `asyncio.Task.cancel()` rather than a
raise inside a mock — that distinction matters, because only the real cancel
exercises how `CancelledError` propagates through the loop.
