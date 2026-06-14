# Rewind / Edit a Previous Prompt (issue #142)

Users can go back to any earlier prompt in a conversation, optionally edit it,
and resubmit from that point. This is the chat equivalent of "rewind and start
again here": the targeted prompt and everything after it are dropped, and the
(edited) prompt is sent as a fresh turn.

The model is **overwrite-in-place** — the conversation stays a single linear
thread; the discarded continuation is not kept as a branch. (Last updated:
2026-06-14.)

## Using it

1. Hover over any of your own messages in the transcript. A subtle pencil icon
   appears next to the existing copy button (same hover treatment, so it stays
   out of the way until you want it).
2. Click the pencil. The message turns into an inline editor prefilled with the
   original text.
3. Edit if you like (or leave it unchanged to simply regenerate from that
   point), then **Send**. **Cancel** or **Esc** leaves the conversation
   untouched; **Enter** submits (**Shift+Enter** inserts a newline).

On submit, everything from that prompt onward is removed from view and the new
turn streams in its place. For server-saved conversations the persisted history
is rewritten to match (the save path is a full upsert, so the shortened thread
is what gets stored).

You cannot rewind while a response is still streaming — finish or stop the
current response first.

## Walkthrough

The screenshots below were captured against a live server (`atlas/main.py`)
driving the built frontend through a browser, with a real LLM
(`groq-gpt-oss-120b`) answering.

### 1. A real answer, with the edit affordance

After the model replies ("...red, blue, and yellow."), hovering the user
message reveals a subtle pencil button beside the existing copy button (both
stay hidden until hover).

![Edit affordance on hover](screenshots/142-01-edit-affordance.png)

### 2. Edit the prompt in place

Clicking the pencil swaps the message for an inline editor prefilled with the
original text. Here the prompt is changed to ask about the *additive* primary
colors of light. A helper line spells out that resubmitting replaces the
messages below; **Send** resubmits, **Cancel** / **Esc** backs out.

![Inline editor with an edited prompt](screenshots/142-02-inline-editor.png)

### 3. Resubmitted — fresh answer in place of the old one

The old turn is gone and the model answers the edited prompt
("...red, green, and blue."), leaving a single linear thread.

![New answer after rewind](screenshots/142-03-after-rewind.png)

## How it works

Messages are addressed by their **user-message ordinal** (the 0-based position
among `user` messages), not by absolute transcript position. The frontend
renders extra system/tool rows that have no backend counterpart, so counting
user messages is the one indexing scheme both sides agree on. The counting rule
lives in one place — `frontend/src/utils/userMessageOrdinal.js` — and is shared
by the render and truncation paths so they cannot drift.

### Frontend

- `Message.jsx` renders the pencil affordance on user messages and the inline
  editor, calling `onRewind(userIndex, newContent)`. The editor stays open if
  the rewind is rejected (e.g. blocked mid-stream) so edits are never lost.
- `ChatArea.jsx` computes each *rewindable* user message's ordinal while mapping
  the transcript and passes it down.
- `ChatContext.jsx` `rewindAndResubmit(userIndex, newContent)` delegates to
  `sendChatMessage`, which adds `rewind_to_user_index` to the `chat` WebSocket
  payload and truncates the local transcript **only after the send is confirmed
  on the wire** — a failed or disconnected send never drops the visible
  conversation tail while the backend keeps its full history.

### Backend

- `ConversationHistory.truncate_at_user_index(user_index)`
  (`atlas/domain/messages/models.py`) drops the Nth user message and everything
  after it, returning the removed messages.
- `ChatOrchestrator.execute(..., rewind_to_user_index=...)`
  (`atlas/application/chat/orchestrator.py`) calls that truncation *before*
  appending the new prompt. The index arrives untrusted off the WebSocket frame,
  so it is coerced: a non-integer value is ignored, and an out-of-range index is
  a no-op that logs a warning (a likely frontend/backend ordinal desync) rather
  than crashing the turn.
- `ChatRequest.rewind_to_user_index` (`atlas/domain/chat/dtos.py`) carries the
  field; `atlas/main.py` reads it off the `chat` message.

## Known limitations / edge cases

- **Agent mode follow-up answers.** When the agent loop asks a follow-up
  question, the answer is rendered as a `user` row but sent as `agent_user_input`
  and consumed inside the transient agent loop — it is never appended to
  `ConversationHistory`. Those rows are marked `_agentInput` and excluded from
  the rewind ordinal and affordance, so they neither receive an edit pencil nor
  shift the ordinals of real prompts.
- **Session files persist across a rewind.** Uploaded/generated files live in
  `session.context["files"]` for the whole conversation by design and are not
  per-turn scoped, so rewinding to before a file was added still leaves that file
  available to the replayed turn. This matches existing session-file semantics;
  per-turn file scoping is out of scope for this feature.

## Relationship to issue #622

The same rewind primitive is what issue #622 (opt-in capture + forced-tool DPO
replay) builds on: rewinding to a turn and replaying it with
`selected_tools=[X]` and `tool_choice_required=True`. `truncate_at_user_index`
deliberately **returns** the removed messages so a future capture layer can
record the discarded ("rejected") trajectory without any further plumbing.
Capture/consent itself is out of scope here.
