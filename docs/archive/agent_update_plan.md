# Agent Update Plan — Strict Reason–Act–Observe Loop

This plan adds a strict 3-phase agent loop (reason → act → observe) to the backend, streams rich UI updates for every phase, and introduces agent UX controls (stop button, max iterations, and a request_input pause tool). It is designed to keep separation of concerns and leverage dependency injection.

## Goals
- Each agent step must execute in order: reason (LLM) → act (tools) → observe (LLM).
- Reason and Observe are plain LLM calls; Act uses the existing tool workflow.
- Stream UI updates for all phases, tool inputs/outputs, and completion states.
- Add agent controls in the UI: stop the loop, configure max iterations, and pause for user input via a special tool.
- Keep code modular and DI-friendly; avoid coupling transport, tools, and LLM logic.

## Requirements (checklist)
- [ ] Enforce 3-phase loop per step: reason → act → observe (all required).
- [ ] Reason and Observe use plain LLM calls (no tools, no RAG unless enabled for the chat).
- [ ] Act uses existing tool schemas, argument injections, notifications, and results handling.
- [ ] UI receives per-phase events: agent_reason, agent_tool_start/complete, agent_observe, agent_completion.
- [ ] UI stop control: user can stop the loop; later user can send a new message to continue.
- [ ] UI max-iterations control (per-run) surfaced similarly to tools/integrations.
- [ ] Special tool: request_input pauses the loop and asks the user a question (softly flashing app notification).
- [ ] Maintain strict separation of concerns and DI.

## Architecture changes (high level)

1) Prompts (Reason/Observe)
- Add two prompt templates:
  - prompts/agent_reason_prompt.md
  - prompts/agent_observe_prompt.md
- Extend PromptProvider with getters for both prompts.
- Keep templates simple and request a small JSON control block in addition to natural language text so the backend can decide whether to continue/finish.

2) ChatService agent loop
- Refactor `ChatService._handle_agent_mode` into a strict ROA loop:
  - Reason: plain LLM call with reason prompt; emit `agent_reason` event.
  - Act: execute tool calls (existing utilities) and stream tool events; allow internal special tool handling (request_input) to pause.
  - Observe: plain LLM call with observe prompt summarizing tool results; emit `agent_observe` event.
  - Exit conditions: parse JSON hints from Reason/Observe (finish/should_continue/final_answer) and honor `max_steps` and `stop` flag.
- Include the internal `request_input` virtual tool schema during agent mode only.

3) Agent controls
- Stop control:
  - UI sends `{ type: "agent_control", action: "stop" }` on WS.
  - Backend tracks a per-session stop flag or `asyncio.Event` and checks it between phases and after each tool call.
  - Loop stops gracefully at phase boundaries; emits `agent_update` with `update_type="agent_stopped"`.
- Max iterations:
  - UI exposes a number input (agent overlay) to set `agent_max_steps`.
  - Reuse existing `agent_max_steps` parameter already supported by backend WS payload.

4) Special tool: request_input
- Tool schema added only when `agent_mode` is true.
- When LLM calls `request_input`, backend:
  - Emits `agent_update` with `update_type="agent_request_input"` and question payload.
  - Pauses the loop and waits for a WS message `{ type: "agent_user_input", content: "..." }` (with timeout safety).
  - Resumes loop, adding the user response into the conversation messages before Observe (or next Reason, depending on prompt design).

5) Notifications and event contracts
- Continue to use `notification_utils.notify_agent_update` for agent-phase events.
- Continue to use `notify_tool_start/complete/error` for tools; these are already DI-friendly and sanitized.

## File-by-file plan

### Backend — Config and Prompts
- backend/modules/config/manager.py
  - AppSettings: add
    - `agent_reason_prompt_filename: str = "agent_reason_prompt.md"`
    - `agent_observe_prompt_filename: str = "agent_observe_prompt.md"`
- backend/modules/prompts/prompt_provider.py
  - Add:
    - `get_agent_reason_prompt(user_question: str, files_manifest: str | None, last_observation: str | None) -> Optional[str]`
    - `get_agent_observe_prompt(user_question: str, tool_summaries: str, step: int) -> Optional[str]`
  - Implement via `_load_template` + `.format(...)` with safe guards.
- prompts/agent_reason_prompt.md (new)
  - Content outline:
    - Brief: Plan how to solve the user’s request, which tools (by name) you might use and why.
    - Output: free-form reasoning for UI + JSON block:
      ```
      { "plan": "...", "tools_to_consider": ["tool_a"], "finish": false }
      ```
- prompts/agent_observe_prompt.md (new)
  - Content outline:
    - Summarize the results just obtained, what they mean with respect to the user’s goal, and what to do next.
    - Output JSON block:
      ```
      { "observation": "...", "should_continue": true, "final_answer": "" }
      ```

### Backend — Agent loop
- backend/application/chat/service.py
  - `_handle_agent_mode` changes:
    - At start of loop step N: emit `agent_update` `{ update_type: "agent_turn_start", step: N }` (already present).
    - Reason phase:
      - Build `reason_messages = [history, files manifest (if any), system reason prompt]`.
      - `reason_text = await self.llm.call_plain(model, reason_messages)`
      - Emit: `{ type: "agent_update", update_type: "agent_reason", content: reason_text, step: N }`.
      - Try to parse trailing JSON control block for `finish`/`tools_to_consider`.
      - Check stop flag before proceeding.
    - Act phase:
      - Compose tools schema: selected tools + internal `request_input` tool if `agent_mode`.
      - Call LLM with tools; execute via `tool_utils.execute_single_tool` with `update_callback=self.connection.send_json` to stream tool events.
      - If tool call name == `request_input`:
        - Intercept and emit `{ update_type: "agent_request_input", question: str, step: N }`.
        - Pause (await user input) and append a tool-style message representing the user’s response, then continue.
      - Append assistant tool_calls and tool messages to `messages`.
      - Update session context/files from tool artifacts (existing `_update_session_from_tool_results`).
      - Check stop flag.
    - Observe phase:
      - Create `tool_summaries` from sanitized tool results.
      - Build observe prompt messages and call plain LLM.
      - Emit `{ update_type: "agent_observe", content: observe_text, step: N }`.
      - Parse JSON: `should_continue` or `final_answer`.
      - Exit or continue.
    - Completion:
      - On finish or max steps: ensure final assistant message is added to history; emit `{ update_type: "agent_completion", steps }` and `response_complete`.
  - Stop control integration:
    - Add per-session `AgentControl` (e.g., `self.sessions_controls[session_id] = AgentControl(stop_event=asyncio.Event(), input_queue=asyncio.Queue())`).
    - Check `stop_event.is_set()` at phase boundaries and break gracefully.

### Backend — Internal virtual tool: request_input
- Tool schema (agent-only):
  ```json
  {
    "type": "function",
    "function": {
      "name": "request_input",
      "description": "Pause the agent and ask the user a clarifying question.",
      "parameters": {
        "type": "object",
        "properties": {
          "question": { "type": "string", "description": "Question to present to user" }
        },
        "required": ["question"]
      }
    }
  }
  ```
- Execution behavior (do not pass to MCP): handled in `_handle_agent_mode` as a special case.
- UI contract when triggered:
  - Emit `agent_update` `{ update_type: "agent_request_input", question, step }`.
  - Soft flash notification (UI) and show input box.
  - Backend awaits `agent_user_input` WS message with the response.

### Backend — WebSocket message contracts (new/extended)
- From backend to UI:
  - `agent_update` events (payload examples):
    - `{ type: "agent_update", update_type: "agent_start", max_steps }`
    - `{ type: "agent_update", update_type: "agent_turn_start", step }`
    - `{ type: "agent_update", update_type: "agent_reason", content, step }`
    - `{ type: "agent_update", update_type: "agent_request_input", question, step }`
    - `{ type: "agent_update", update_type: "agent_observe", content, step }`
    - `{ type: "agent_update", update_type: "agent_stopped" }`
    - `{ type: "agent_update", update_type: "agent_completion", steps }`
  - Existing tool events unchanged: `tool_start`, `tool_complete`, `tool_error`.
  - Existing: `response_complete`.
- From UI to backend (WS):
  - Chat start (existing): `{ type: "chat", agent_mode: true, agent_max_steps?: number, ... }`
  - Stop control: `{ type: "agent_control", action: "stop" }`
  - Provide input for request_input: `{ type: "agent_user_input", content: string }`

### Frontend — UI changes (high level)
- Agent overlay panel (similar to tools/integrations):
  - Max iterations input (default from server config or 10).
  - Start/Stop button: when running, show Stop; emit `agent_control: stop`.
  - Display streaming phases: Reason text, Tool calls/results, Observe text with step badges.
  - Special prompt banner when `agent_request_input` arrives: flash app softly and focus an input field; submitting sends `{ type: "agent_user_input", content }`.

## Data flow (per step)
1) Reason
- Build messages: [history] + [files manifest, if any] + [system reason prompt]
- LLM → reason text (+ JSON)
- Emit `agent_reason`
- Check stop

2) Act
- LLM with tools → tool_calls
- For each tool_call:
  - If `request_input`: pause, emit `agent_request_input`, await UI input, record response as tool-like message
  - Else: execute via `tool_utils.execute_single_tool`, UI gets `tool_start/complete`
- Stitch messages (assistant tool_calls → tool results)
- Update session context/files
- Check stop

3) Observe
- Build messages + observe prompt (summarize latest tool results)
- LLM → observe text (+ JSON)
- Emit `agent_observe`
- Decide continue/finish

## Separation of concerns and DI
- ChatService orchestrates phases and session control.
- LLMProtocol remains unchanged; we reuse `call_plain`, `call_with_tools`.
- Tool utils keep their single-responsibility role; they only execute tools and emit tool events.
- Notification utils centralize UI messages; no FastAPI/transport types leak into domain logic.
- PromptProvider isolates file system/template access.
- WebSocket adapter stays as a transport wrapper only.

## Edge cases
- Reason/Observe JSON missing/malformed → treat as free text; continue unless `max_steps` exceeded.
- No tools selected → Reason/Observe still run; act phase performs no calls.
- Stop pressed mid-step → stop at next phase boundary; emit `agent_stopped` and `response_complete`.
- request_input timeout (e.g., 5–10 minutes) → emit `agent_update` with `update_type="agent_request_input_timeout"` and stop.
- Token growth → keep step prompts concise; optionally collapse old observations into short summaries (future enhancement).

## Testing strategy
- Unit tests (backend):
  - Reason–Act–Observe executes in order; Observe can terminate loop.
  - request_input pauses and resumes with provided input.
  - Stop control sets flag and loop exits gracefully.
  - Tool notifications emitted; filename sanitization preserved.
- Integration smoke (dev):
  - WS chat with agent_mode true shows `agent_reason`, tool events, `agent_observe`, completion.
  - Stop button halts; sending a new chat restarts fresh.
- Non-regression: tools-only and RAG modes unchanged.

## Acceptance criteria
- ROA loop is enforced per step and visible in UI via dedicated events.
- Stop and max-iterations work end-to-end.
- request_input tool pauses the loop and surfaces a question with soft flash.
- Code remains modular with DI; no transport-specific types in core logic.

## Implementation phases
1) Prompts and config keys (backend) — add files and getters.
2) Agent loop refactor (backend) — ROA logic, internal request_input tool handling, stop control wiring.
3) UI overlay controls and event rendering (frontend) — stop button, max iterations, request_input prompt.
4) Tests and docs — basic unit tests; short README updates.

## Rollback plan
- The refactor keeps existing tools-only and plain chat flows intact; toggling agent_mode off restores current behavior.
- Feature flags: a simple toggle in UI to hide agent overlay if needed.
