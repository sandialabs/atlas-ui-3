# Agent Busy Indicator

Date: 2026-08-08 (issue #748)

## Problem

In agent mode the chat area went silent between loop steps. After a tool
finished and the loop called the LLM again for the next turn, nothing on screen
moved: no spinner, no status text. The only evidence the run was still alive was
the red Stop button in the composer, so a healthy multi-step run was
indistinguishable from a frozen app.

The cause is the scope of the `isThinking` flag in
[`ChatContext`](../../../frontend/src/contexts/ChatContext.jsx). It is set once
when the user sends a message and cleared by the first `token_stream` frame with
`is_first`. Nothing sets it again for later turns: `agent_turn_start` only
records the step number, and `tool_start` / `tool_complete` only mutate the tool
message. So from the first streamed token onward, every inter-step LLM wait ran
with `isThinking === false` and no indicator.

## Approach

A separate presentational component,
[`AgentBusyIndicator`](../../../frontend/src/components/AgentBusyIndicator.jsx),
rendered by `ChatArea` directly below the existing thinking indicator. It owns
its own visibility rule and returns `null` unless all three hold:

- `isAgentRunning` - an agent run is in flight (set on send, cleared by the
  terminal agent events in `websocketHandlers`)
- `!isThinking` - the legacy indicator is not already on screen
- `!isStreaming` - no message is currently receiving tokens

Text is `Agent working on step N...` when `currentAgentStep` is a positive
number, and `Agent thinking...` otherwise. The markup reuses the thinking
indicator's avatar, spinner SVG, and spacing classes so the two are visually
interchangeable.

The alternative was to set `isThinking = true` on `agent_turn_start` and
re-clear it on `tool_start` / `token_stream`. That was rejected: `isThinking`
also gates the follow-up suggestion buttons, the send/stop button swap, and the
guards in `sendChatMessage` that reject a send while generating. Widening its
lifetime would have moved all of those behaviors at once. The separate component
adds a purely additive render path and leaves the `isThinking` state machine
untouched.

## Files

- `frontend/src/components/AgentBusyIndicator.jsx` - the component and its
  visibility rule
- `frontend/src/components/ChatArea.jsx` - renders it; pulls `currentAgentStep`
  out of `useChat()`
- `frontend/src/test/agent-busy-indicator.test.jsx` - covers the visible case,
  the step label, and each of the three suppression conditions

## Verifying

Turn on agent mode with at least one tool selected and send a task that needs
several steps. Between the tool result and the next chunk of output the spinner
stays on screen with `Agent working on step N...`, and the step number advances
as the loop reports each `agent_turn_start`.
