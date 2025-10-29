# Service Refactor Plan

## Objective

Make the backend—and especially `backend/application/chat/service.py`—clearer, more modular, and easier to test by separating concerns into well-defined layers and services, without changing behavior.

---

## What’s off today

From `backend/application/chat/service.py` and the current repo layout:

- ChatService is doing too much:
  - Session management (in-memory)
  - Request orchestration and branching (plain, tools, RAG, agent)
  - MCP prompt override injection
  - Tool ACL filtering
  - File ingestion and artifact persistence
  - Agent-loop event translation to UI
  - Streaming notifications
  - Error wrapping
- Inconsistent message typing (`List[Dict[str, str]]` vs `List[Dict[str, Any]]`) and ad-hoc message shapes.
- Large handler `handle_chat_message` with many flags and deep branching logic.
- Inline authorization and inline prompt override hide important policies in orchestration code.
- Transport concerns (WebSocket streaming) are coupled into application logic via direct `connection` + `notification_utils` calls.
- Legacy remnants and duplication (commented-out older implementation blocks).

Net effect: lower testability, higher change risk, and difficulty adding new modes/transports.

---

## Target architecture (ports and adapters)

- Domain (pure): entities/models, errors, value objects
  - Existing: `domain/messages`, `domain/sessions`, `domain/errors`
  - Add: typed DTOs where needed (LLMMessage, ChatRequest, ChatResponse)
- Application (use-cases/orchestration):
  - ChatOrchestrator: single entrypoint that wires steps but delegates to strategies/services
  - Mode runners/strategies: PlainMode, ToolsMode, RagMode, AgentMode
  - Preprocessors: MessageBuilder (history + files manifest), PromptOverrideService (MCP), RiskCheck (optional)
  - Policy services: ToolAuthorizationService (ACL filtering), ToolSelectionPolicy (required/auto)
  - ArtifactIngestor: updates session context with tool artifacts and emits file/canvas updates
  - SessionManager: get/create/update session (backed by repository)
  - EventPublisher: abstraction for UI updates (no direct transport dependency)
- Interfaces (ports/contracts):
  - LLMCaller (reuse `LLMProtocol`)
  - ToolManager (existing interface), plus a PromptOverrideProvider port if helpful
  - FileStorage (file_manager port)
  - SessionRepository (in-memory now; replaceable later)
  - EventPublisher (UI transport-agnostic)
  - Authorization (tool ACL port)
- Infrastructure (adapters):
  - WebSocketEventPublisher (wraps `notification_utils` and `connection.send_json`)
  - MCP ToolManager adapter and MCP PromptProvider adapter
  - S3/MinIO FileStorage adapter (existing)
  - InMemorySessionRepository (drop-in for current dict)
  - Config-backed AuthorizationManager adapter (wraps `create_authorization_manager()`)

Outcome: ChatOrchestrator stays thin and stable; each part evolves independently with strong contracts.

---

## Key design decisions

- Strong DTOs
  - ChatRequest: model, content, selected_tools, selected_prompts, selected_data_sources, flags, temperature, user_email
  - ChatResponse: final text + metadata
  - LLMMessage: type-safe shape used across runners (role, content, optional tool_calls)
- Strategies for modes
  - PlainModeRunner: LLM plain
  - ToolsModeRunner: tool schemas + LLM + tool workflow + final synthesis
  - RagModeRunner: rag-aware call
  - AgentModeRunner: bridges AgentLoopProtocol, delegates EventPublisher and ArtifactIngestor
- Preprocessing pipeline
  - Build base messages (history + files manifest)
  - Apply MCP prompt override (first valid only, as today)
  - Optional risk scoring/logging (from `core.prompt_risk`)
- Policies extracted out of orchestrator
  - ToolAuthorizationService handles ACL per user, including special cases (e.g., `canvas_canvas`)
  - ToolSelectionPolicy enforces “required” vs “auto”
- Eventing decoupled
  - EventPublisher abstracts all UI updates; mapping to `notification_utils` lives in infra
  - Agent events translation moved to an AgentEventRelay using EventPublisher
- Session management separated
  - SessionManager + SessionRepository port (keep in-memory impl initially)
- Cleanup
  - Remove legacy commented code
  - Normalize message typing and signatures (no more `Dict[str, Any]` everywhere)

---

## Proposed file structure and new modules

- `backend/application/chat/`
  - `orchestrator.py`  (ChatOrchestrator – replaces most of ChatService’s `handle_chat_message`)
  - `service.py`       (Thin façade delegating to Orchestrator; retains public API temporarily)
  - `modes/`
    - `plain.py`       (PlainModeRunner)
    - `tools.py`       (ToolsModeRunner)
    - `rag.py`         (RagModeRunner)
    - `agent.py`       (AgentModeRunner; wraps AgentLoopFactory/Protocol and event relay)
  - `preprocessors/`
    - `message_builder.py`          (history + files manifest)
    - `prompt_override_service.py`  (MCP prompt override extraction/injection)
    - `risk_check.py`               (optional prompt risk logger using `core.prompt_risk`)
  - `policies/`
    - `tool_authorization.py`       (ACL filtering)
    - `tool_selection.py`           (required vs auto)
  - `artifacts/`
    - `ingestor.py`                 (wraps `file_utils.process_tool_artifacts` and session context updates)
  - `events/`
    - `publisher.py`                (EventPublisher interface; could live under `interfaces/`)
    - `agent_event_relay.py`        (maps AgentEvents -> EventPublisher calls)
  - `sessions/`
    - `manager.py`                  (SessionManager orchestrates fetch/update)
    - `repository.py`               (SessionRepository port + InMemory implementation)
- `backend/interfaces/`
  - `transport.py` (existing `ChatConnectionProtocol`; add `EventPublisher`)
  - `tools.py` (existing `ToolManagerProtocol`; add prompt retrieval port if needed)
  - `prompts.py` (PromptProvider / PromptOverrideProvider port)
  - `storage.py` (FileStorage port if not already abstracted)
  - `authorization.py` (AuthorizationManager port)
  - `sessions.py` (SessionRepository port)
- `backend/infrastructure/`
  - `events/websocket_publisher.py` (wraps `notification_utils` + connection)
  - `prompts/mcp_prompt_provider.py` (bridge `tool_manager.get_prompt` to PromptOverrideProvider)
  - `sessions/in_memory.py` (in-memory session repo)
  - `authorization/manager_adapter.py` (wrap `create_authorization_manager()`)

Note: keep existing utilities but progressively move their usage into the appropriate application/infrastructure modules.

---

## Phased refactor roadmap (no behavior change per phase)

Phase 0: Preparations
- Remove dead/commented blocks (old `_handle_tools_mode_with_utilities` copy).
- Introduce DTOs: ChatRequest, ChatResponse, LLMMessage.
- Normalize message typing in `ChatService` and internal methods.

Phase 1: Extract policies and preprocessing (low-risk)
- Move Tool ACL filtering into `policies/tool_authorization.py`.
- Extract MCP prompt override logic into `preprocessors/prompt_override_service.py` with an adapter using current `tool_manager.get_prompt`.
- Extract message building (history + files manifest) into `preprocessors/message_builder.py`.
- Keep `ChatService` calling these new modules.

Phase 2: EventPublisher and AgentEventRelay
- Create `events/publisher.py` interface and `infrastructure/events/websocket_publisher.py` implementation (wraps `notification_utils` and `connection.send_json`).
- Extract agent event mapping into `events/agent_event_relay.py`.
- Replace direct `notification_utils` calls in `ChatService` with EventPublisher calls through a thin wrapper, but keep `notification_utils` usage inside the infra publisher.

Phase 3: Mode strategies
- Extract `_handle_plain_mode`, `_handle_tools_mode_with_utilities`, `_handle_rag_mode`, `_handle_agent_mode_via_loop` into separate classes under `modes/`.
- Keep `ChatService.handle_chat_message` delegating to the proper ModeRunner based on flags.
- Ensure tool workflow + artifact ingest path is preserved, but routed through `artifacts/ingestor.py`.

Phase 4: Orchestrator + SessionManager
- Create `orchestrator.py` consolidating preprocessing, policy checks, mode dispatch, and event publisher wiring.
- `ChatService` becomes a thin façade: takes ChatRequest, delegates to Orchestrator.
- Introduce SessionManager + SessionRepository; replace internal `self.sessions` dict progressively.

Phase 5: Cleanup and documentation
- Update docstrings and docs/architecture notes.
- Remove transport-level calls from application layer.
- Consolidate `error_utils` usage into well-defined error boundaries in orchestrator and runners.

---

## Acceptance criteria

- Behavior unchanged:
  - Same inputs produce same UI updates and final assistant messages (including MCP prompt override behavior, tool ACL filtering, canvas/file events).
  - Existing tests pass without modification.
- Type hygiene:
  - No stray `Any` in new code paths; DTOs and protocols are typed.
- Clear separation:
  - No transport-level imports or calls in application layer.
  - Policies and preprocessing are not embedded in orchestrator code.
- Backwards compatibility:
  - `ChatService` public method signatures preserved for at least one release cycle (wrapping Orchestrator).
- Observability:
  - Logging remains at parity; sensitive fields still sanitized.

---

## File-by-file highlights (first waves)

- `backend/application/chat/service.py`
  - Keep class but reduce responsibility: delegate to Orchestrator
  - Remove inline tool ACL and prompt override; call services
  - Remove commented legacy block
  - Normalize messages typing via LLMMessage DTO
- `backend/application/chat/preprocessors/prompt_override_service.py`
  - Move MCP prompt override injection logic; keep “first valid prompt” rule
- `backend/application/chat/policies/tool_authorization.py`
  - Move ACL filtering logic, including `canvas_canvas` special-case and authorized server prefix check
- `backend/application/chat/modes/tools.py`
  - Hold tool schema resolution, LLM call with tools, tool workflow execution, artifact ingest, final synthesis and event publishing via EventPublisher
- `backend/application/chat/modes/agent.py`
  - Wrap AgentLoopFactory; use AgentEventRelay to publish updates and ingest artifacts
- `backend/application/chat/artifacts/ingestor.py`
  - Wrap `file_utils.process_tool_artifacts` and session context updates
- `backend/infrastructure/events/websocket_publisher.py`
  - All calls to `notification_utils.*` live here; application layer only publishes events

---

## Testing and migration

- Unit tests
  - PromptOverrideService: parses and injects system message correctly from varying MCP prompt shapes
  - ToolAuthorizationService: filters tools given user and servers, including underscore server names and the canvas special-case
  - Mode runners: happy path + “no tool calls” path + failure path
  - EventPublisher/WebSocketPublisher: calls the correct `notification_utils` functions
- Integration tests
  - Full chat flow (plain, tools, rag, agent) using LLM/ToolManager fakes or existing mocks in `mocks/`
  - Verify artifacts ingestion triggers file/canvas updates as before
- E2E
  - Re-use `./test/run_tests.sh all` as-is (per project docs)
- Migration plan
  - Phased PRs per phase above; each PR keeps tests green
  - Introduce DTOs and strategies without changing routes or API payloads
  - Keep `ChatService` API stable; wire new orchestrator under the hood
- Rollback plan
  - Each phase is reversible by toggling orchestrator/strategy injection back to legacy code path for that mode

---

## Risks and mitigations

- Behavior drift in event ordering or content
  - Mitigation: capture golden recordings of `notification_utils` calls in tests before refactor; assert on order and payloads
- Tool ACL discrepancies
  - Mitigation: explicit tests with multiple server names (including underscores) and the canvas special-case
- Async/event coupling in Agent mode
  - Mitigation: encapsulate AgentEventRelay; keep exact mapping semantics; add tests for sequence of events
- Message shape mismatches
  - Mitigation: introduce LLMMessage early; add adapters where legacy dicts still exist
- MCP prompt variations
  - Mitigation: preserve robust parsing with fallback to `str(prompt_obj)`; unit tests with multiple prompt shapes

---

## Small adjacent improvements

- Replace ad-hoc log sanitization calls with a `LogContext` helper used consistently.
- Cache tool schemas and MCP prompts per session to reduce repeated lookups.
- Standardize metadata keys in assistant messages, e.g., `{ "mode": "tools", "tools": [...], "data_sources": [...] }`.

---

## Next steps

- Start with Phase 1 (policies + preprocessors) — lowest risk, highest clarity gain.
- Scaffold modules and wire them into `ChatService` without changing behavior.
- Add focused unit tests for new modules and keep integration/E2E tests passing.
