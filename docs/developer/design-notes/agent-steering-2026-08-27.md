# Agent Steering (mid-run user messages)

Last updated: 2026-08-27

Issue #824 (PR #854): while the native agentic loop is mid-run, a chat message
the user sends should reach the LLM at the next iteration boundary as a normal
user turn — without breaking or stopping the loop. This lets the user steer an
agent that is already working, the way OpenCode and Claude Code do.

## Problem

The WebSocket endpoint runs each chat turn as a background task and keeps
receiving frames. When a second `chat` arrived while an agent turn was still
running, the endpoint simply overwrote `active_chat_task["task"]` and started a
second `handle_chat` coroutine appending to the same `session.history` — a race
that corrupts the transcript. There was no path to inject the message into the
running loop at all.

## Design

A `SteeringChannel` (`atlas/application/chat/agent/steering.py`) is a small
`asyncio.Queue` plus an `active` flag. It is **transport-owned** (the WebSocket
endpoint creates it) and **loop-consumed** (the `AgenticLoop` drains it). The
`active` flag is the single authoritative answer to “is a loop genuinely
consuming right now?” — and it is flipped on by the `AgentModeRunner`, not by
the transport, so the transport never has to guess from the request payload.

Wiring, transport → loop:

```
WS endpoint  ──steering──▶  ChatService.handle_chat_message
                           └─▶  ChatOrchestrator.execute
                               └─▶  AgentModeRunner.run  (activate / finally deactivate)
                                   └─▶  AgenticLoop.run ─▶  _run_steps (drain)
```

### Injection points in `_run_steps`

1. **Top of each iteration.** A steering message that arrived during the
   previous step’s tool execution is drained here, so the in-flight tool call
   finishes before the new user turn reaches the model. This is the “next
   iteration boundary once the in-flight tool call finishes” the issue asks
   for.
2. **The text-only break point.** If the model produced a would-be final
   (text-only) answer while a steer was pending, that answer is folded in as
   display-only `agent_intermediate` narration and the loop `continue`s rather
   than `break`ing — so a steer that lands during the final stream is not
   ignored.

### Why `active` is set by the runner, not the transport

A turn can request `agent_mode` but fall back to a non-agent turn (the selected
model lacks tool support, or no tools were selected). If the transport guessed
“agent ⇒ route to the channel”, the channel would never be drained and the
message would be silently lost. By activating only inside `AgentModeRunner.run`
(and deactivating in a `finally`), the channel only accepts messages while a
loop is genuinely consuming. When the channel is inactive, the transport’s
routing check fails and the message starts a fresh turn — the same path that
handles the rare race where the agent finished just before the message arrived.

### Land as a normal user turn

The injected message is added to `context.history` as a `MessageRole.USER`
message with **no display-only `message_type`**. It is therefore included by
`ConversationHistory.get_messages_for_llm` for later turns and counts toward
rewind ordinals (the frontend’s `userMessageOrdinal` excludes only messages
flagged `_agentInput`, which the dead `agent_user_input` path used). This
matches the issue’s “land as a normal user turn” requirement, as opposed to a
separate steering annotation.

## What is not handled

- Steering is text-only: a steered frame contributes its `content` to the loop;
  attachments, model/tool selections, and rewind on the same frame are not
  carried through (the agent is mid-run with its own tools). The transport
  routes only the text and acknowledges it with a `steering_queued` event so
  "queued" is distinguishable from "dropped" at the client.
- If the step budget is exhausted while a steer is pending, the steer is not
  consumed by the loop (the budget is a hard limit). Anything still queued when
  the loop stops draining is surfaced to the user as a warning telling them to
  resend, rather than silently dropped or persisted as a USER turn no model saw.
- The channel is bounded (`STEERING_QUEUE_MAXSIZE`); a flood of steers is
  rejected with a `steering_queue_full` error frame instead of stacking into
  history and every later prompt.
- The pre-existing, unused `agent_user_input` / `agentPendingQuestion`
  frontend path (dead code from a removed control-tool loop) is left
  untouched.