# Unified Refactor Plan: Layered + Feature Modules (Ports-and-Adapters)

Objective
- Reduce coupling and collapse “core” by combining a layered (domain/application/interfaces/infrastructure) architecture with your feature-oriented modules.
- Keep business logic testable and transport-agnostic, while retaining clear feature boundaries (auth, files, llm, etc.).

Are Plan A and Plan B the same?
- Overlap: Both aim to move logic out of core, centralize LLM/tools/prompts/files/auth into modules, and improve structure.
- Differences:
  - Plan B focuses on feature modules and rearranging routes/utilities.
  - Plan A enforces clean layering, protocols (ports), DI, and transport decoupling.
- Best: Blend them. Use Plan A’s layers to prevent coupling; use Plan B’s modules as adapters and feature packages.

Key risks in Plan B alone
- Chat still tied to FastAPI/WebSocket types.
- Global orchestrator singleton sustains hidden dependencies.
- Dict-shaped messages spread across modules.
- Potential import cycles between modules.

Unified Architecture
- domain/ (pure models, no I/O)
- interfaces/ (Protocols for LLM, Tools, Agent, Transport, Auth, HTTP, Storage)
- application/ (use-cases/services: ChatService, AgentService, FileContextService, EventBus)
- modules/ (feature adapters: auth, llm, tools, prompts, file_storage, file_handling, http, middleware, observability, admin, feedback, config)
- infrastructure/ (transport adapters and app wiring: FastAPI/WebSocket adapters, app factory, DI)
- core/ (temporary shims during migration; goal: empty)

Target Structure
- backend/
  - domain/
    - messages/, sessions/, files/
    - errors.py
  - interfaces/
    - llm.py, tools.py, agent.py, transport.py, auth.py, http.py, storage.py, file_context.py
  - application/
    - chat/ (ChatService)
    - agent/ (AgentExecutor/Service)
    - file_context/ (FileContextService, policies over domain types)
    - events/ (EventBus, event types)
  - modules/
    - auth/ (moved from core/auth*.py, validate_selected_tools)
    - callbacks/ (callbacks, callback manager)
    - chat/ (optional: transport-facing glue; not business logic)
    - config/ (config_api.py, utils)
    - feedback/ (feedback_api.py, models)
    - file_handling/ (policies previously in core/file_config.py)
    - file_storage/ (file_storage_api.py, S3 adapters)
    - http/ (UnifiedHTTPClient adapter + errors)
    - middleware/ (AuthMiddleware)
    - observability/ (otel, log formatting)
    - prompts/ (prompt_utils)
    - llm/ (callers, tool creation, vendor SDK integrations)
    - admin/ (admin_api.py)
  - infrastructure/
    - transport/
      - websocket_connection_adapter.py (implements ChatConnectionProtocol)
      - routers/ (compose feature routers)
    - app_factory.py (composition root; replaces orchestrator singleton)
  - core/
    - chat_session.py (shim delegating to ChatService, to be removed)
    - orchestrator.py (temporary; replaced by app_factory wiring)

Responsibilities
- domain: Message, ConversationHistory, Session, ToolCall/ToolResult, FileRef; dataclasses/enums only.
- interfaces: Protocols that the application uses; no vendor or FastAPI types.
- application: ChatService orchestrates LLM/Agent/Tools via interfaces; emits events; no FastAPI/WebSocket imports.
- modules: Concrete adapters for protocols (LLM callers, tools, http, auth, storage, prompts); FastAPI routes live here.
- infrastructure: Transport adapters and DI wiring (composition root), not business logic.

Core Refactors
- Remove global orchestrator: introduce app_factory.py to wire dependencies; inject into routes and ChatSession shim.
- Decouple transport: ChatService takes a ChatConnectionProtocol; WebSocket wrapped by WebSocketConnectionAdapter.
- Replace dicts with models: domain.Message and domain.ToolResult; convert at the transport boundary.
- Event-driven outbound: application publishes events; transport subscribers serialize to client.

Mapping from current core
- core/chat_session.py → application/chat/service.py (logic) + infrastructure/transport/websocket_connection_adapter.py (adapter). Keep a thin shim temporarily.
- core/orchestrator.py → infrastructure/app_factory.py (DI), then delete.
- core/utils.py → modules/llm (call_llm*, tool creation), modules/auth (validate_selected_tools), remove remainder.
- core/file_config.py → modules/file_handling/ (or application/file_context with domain models).
- core/http_client.py → modules/http/.
- core/middleware.py → modules/middleware/.
- core/otel_config.py → modules/observability/.
- core/prompt_utils.py → modules/prompts/.
- core/config_routes.py → modules/config/config_api.py.
- core/feedback_routes.py → modules/feedback/feedback_api.py.
- core/files_routes.py → modules/file_storage/file_storage_api.py.
- core/admin_routes.py → modules/admin/admin_api.py.
- core/auth.py, core/auth_utils.py → modules/auth/.
- core/callbacks.py, core/custom_callbacks.py → modules/callbacks/.
- core/old_session.py → delete if unused.

Incremental Migration (safe steps)
1) Introduce domain models and interfaces Protocols; add adapters that wrap current implementations (no behavior change).
2) Extract ChatService from chat_session.py; keep chat_session as a delegating shim.
3) Add infrastructure/app_factory.py; replace orchestrator singleton; wire services via DI.
4) Create WebSocketConnectionAdapter; ChatSession/route use injected adapter.
5) Move routes and utilities into modules/* as above; keep import surfaces stable.
6) Introduce EventBus for internal events; add a WebSocket emitter subscriber; migrate direct send_json calls.
7) Migrate file policies to modules/file_handling (or application/file_context) using domain types.
8) Remove core/* shims; enforce import rules (domain → interfaces → application → modules/infrastructure).

Import Rules (to avoid cycles)
- domain imports nothing from application/modules/infrastructure.
- interfaces import only typing and domain types.
- application imports domain + interfaces (no FastAPI).
- modules implement interfaces and may import vendor SDKs or FastAPI.
- infrastructure wires everything; imports application and modules.

Testing
- Unit test ChatService/AgentService with fakes for interfaces.
- Contract test adapters in modules against Protocols.
- Snapshot test outbound event payloads.
- No network or WebSocket in domain/application tests.

Acceptance Criteria
- No references to fastapi.WebSocket in domain/application.
- No core.orchestrator imports; DI via app_factory.py.
- Chat flow covered by unit tests with FakeLLM and FakeToolExecutor.
- “core” empty or transitional only, slated for deletion.

Notes
- Feature modules (Plan B) remain the home for routes/adapters.
- Layering (Plan A) prevents cross-feature coupling and transport bleed-through.
- Keep changes incremental; maintain API payload stability until frontend aligned.