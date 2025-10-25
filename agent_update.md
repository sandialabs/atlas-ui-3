# Agent Loop Manager: Extraction and DI Plan

This document proposes extracting `_handle_agent_mode` from `ChatService` into an injectable Agent Loop Manager so different agent strategies can be swapped without touching core chat orchestration.

## Objectives
- Decouple the agent Reason–Act–Observe loop from `ChatService` with a clear interface.
- Preserve current behavior: streaming updates, tool execution, RAG integration, prompts, and control messages.
- Enable multiple loop strategies (baseline ReAct, tool-first, LLM-only, plan-and-execute) via dependency injection.

## Scope
- No frontend contract changes. Use existing `notification_utils` event types.
- Minimal changes to `ChatService` (delegate to agent loop, handle events, persist artifacts).
- Introduce new interfaces and a default implementation that mirrors current behavior.

## High-level design

### Key abstractions
- AgentLoopProtocol
  - run(session, model, messages, context, selected_tools, data_sources, max_steps, temperature, event_handler) -> AgentResult
  - Owns the Reason–Act–Observe loop, including user-input pauses and stop control.
- AgentEventHandler (adapter)
  - Receives structured agent events and forwards to existing `notification_utils` for UI streaming.
- ToolExecutor (adapter)
  - Thin wrapper around `tool_utils` and `tool_manager` to execute a single tool call per step and return ToolResult.

### Data contracts (shapes)
- AgentContext
  - session_id: UUID
  - user_email: string | null
  - files: dict
  - history: ConversationHistory
  - cancel_token: asyncio.Event (optional)
- AgentEvent
  - type: one of [agent_start, agent_turn_start, agent_reason, agent_request_input, agent_tool_start, agent_tool_complete, agent_observe, agent_completion, agent_error]
  - payload: dict (message, step, tool name, outputs, max_steps, etc.)
- AgentResult
  - final_answer: string
  - steps: number
  - metadata: dict (e.g., used tools, timings)

### Default strategy: ReActAgentLoop
- Mirrors current `_handle_agent_mode` semantics
  - Reason: prompt with `PromptProvider.get_agent_reason_prompt` and a control tool (`agent_decide_next`) with JSON fallback.
  - Act: obtain tool schema from selected tools; call LLM (with RAG if provided); execute only the first tool call; append messages and persist artifacts.
  - Observe: prompt with `PromptProvider.get_agent_observe_prompt` and a control tool (`agent_observe_decide`) with JSON fallback.
  - Finish when `finish=true`, `final_answer` provided, `should_continue=false`, or max steps reached (then do a plain LLM call).
  - Handle user input pauses and stop control by polling connection.

## Wiring and DI

### ChatService changes (surgical)
- __init__(..., agent_loop: Optional[AgentLoopProtocol] = None)
  - If None, construct default ReActAgentLoop with available deps: llm, tool_executor, prompt_provider.
- handle_chat_message(..., agent_mode=True)
  - Build AgentContext from session.
  - Create an AgentEventHandler that maps agent events -> `notification_utils` calls (existing payload shapes).
  - Call agent_loop.run(...).
  - Append final assistant message to history and return chat response.
- Keep `_update_session_from_tool_results` and file ingestion path unchanged. ToolExecutor will call back into this via a supplied callback or return ToolResults for the service to persist.

### ToolExecutor
- execute_single(tool_call, session_context, update_callback) -> ToolResult
- Optional: execute_workflow(...) in future strategies. For now, preserve single-tool-per-step behavior.

### Configuration
- Add config key: `agent.loop.strategy`
  - Values: `react` (default), `tool-first`, `llm-only`, `plan-and-execute` (future)
- Factory at app wiring time (or within `ChatService` if config_manager is present) to pick the loop implementation.
- Optional per-request override via a new `loop_strategy` argument.

## Event mapping (no frontend change)
- agent_start -> notify_agent_update(update_type="agent_start", max_steps)
- agent_turn_start -> ...("agent_turn_start", step)
- agent_reason -> ...("agent_reason", message, step)
- agent_request_input -> ...("agent_request_input", question, step)
- agent_tool_start -> ...("tool_start", tool)
- agent_tool_complete -> ...("tool_complete", tool, result)
- agent_observe -> ...("agent_observe", message, step)
- agent_completion -> ...("agent_completion", steps)
- agent_error -> ...("agent_error", message)

Ensure payloads match existing `notification_utils` calls to avoid UI changes.

## Testing plan
- Unit tests
  - ReActAgentLoop: happy path (tool call, observe, finish), no-tools path, RAG+tools path, tools produce no call, max steps fallback.
  - User input request and stop control handling.
  - Error paths and retries (basic).
- Contract tests
  - Sequence of AgentEvents for typical scenarios.
- Integration tests
  - ChatService with a MockAgentLoop to validate event-to-UI mapping and history updates.
  - Existing e2e tests should pass unchanged.

## Migration steps
1) Introduce types: AgentLoopProtocol, AgentContext, AgentEvent, AgentResult, AgentEventHandler.
2) Implement ReActAgentLoop by moving logic from `_handle_agent_mode` with small helpers for JSON parsing and control polling.
3) Create ToolExecutor adapter over `tool_utils` and `tool_manager`.
4) Add event adapter in `ChatService` that translates AgentEvents to `notification_utils` calls.
5) Inject agent_loop via `ChatService.__init__`; default to ReActAgentLoop.
6) Delegate `agent_mode=True` path in `handle_chat_message` to `agent_loop.run`.
7) Keep `_handle_agent_mode` temporarily as a thin wrapper around the loop; deprecate and remove after validation.
8) Add config-driven strategy selection (react only initially); wire factory.
9) Add tests and run full suite; fix deltas.

## Risks and mitigations
- Event ordering/duplication
  - Use structured, ordered events with a `step` field; consolidate emission in the loop.
- Streaming regressions
  - Maintain existing `notification_utils` payloads; only adapt at the event adapter layer.
- Tool side-effects
  - Preserve single-tool-per-step execution; keep existing artifact ingestion via `ChatService`.
- Backwards compatibility
  - Keep wrapper `_handle_agent_mode` during rollout; enable flag-based fallback.

## Future enhancements
- Alternative strategies: parallel tools, tool-first, pure-LLM with function parsing.
- State machine for clearer transitions and telemetry (step timings, token usage, tool latency).
- Retry/backoff policy for transient tool/LLM errors.
- Cancellation token plumbed to allow immediate stop.

## Success criteria
- No frontend changes; same streaming semantics and message shapes.
- Existing tests pass; new tests validate loop events and outcomes.
- Strategy can be swapped via config or per-request override without touching `ChatService`.

## Work breakdown
- 0.5d: Define protocols, events, context types.
- 1.0d: Extract ReActAgentLoop, ToolExecutor, adapter wiring.
- 0.5d: ChatService DI and delegation; config factory.
- 1.0d: Tests (unit + integration) and fixes.
- 0.5d: Cleanup/deprecations and docs.

Total: ~3.5 days including validation; smaller if tests already cover agent mode flows.
