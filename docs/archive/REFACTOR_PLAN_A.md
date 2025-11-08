# Refactor Plan A: Layered, Ports-and-Adapters Architecture

Goal: reduce coupling in backend/core by moving toward a clean, layered design (domain, application, interfaces, infrastructure) with explicit protocols and adapters. Keep modules cohesive, testable, and transport-agnostic.

Outcomes
- No singletons in core paths (remove global orchestrator).
- Domain logic free from FastAPI/WebSocket details.
- Clear public interfaces (Protocols) between components.
- Incremental migration with zero behavior change per step.

Guiding principles
- Dependency inversion: depend on abstractions, not concretions.
- Stable boundaries: transport and vendor SDKs only at the edges.
- Small surface area: fewer dicts, more typed models at boundaries.
- Observability and error handling standardized.

Target package structure (new and existing)
- backend/
  - domain/                # Pure models and logic (no FastAPI, no I/O)
    - messages/
    - sessions/
    - files/
    - errors.py
  - interfaces/            # Protocols (ports)
    - chat.py
    - llm.py
    - tools.py
    - agent.py
    - transport.py
    - auth.py
    - http.py
    - storage.py
  - application/           # Use-cases/services orchestrating domain + ports
    - chat/
    - agent/
    - file_context/
    - config/
    - events/
  - infrastructure/        # Adapters (FastAPI, WebSocket, HTTP, storage, tracing)
    - transport/
    - http/
    - storage/
    - auth/
    - observability/
    - middleware/
  - modules/               # Reuse existing module families (llm, prompts, file_storage, config, etc.)
  - core/                  # To be emptied; temporary shims only during migration

Key boundary decisions
- Domain never imports application or infrastructure.
- Application depends on domain and interfaces only.
- Infrastructure depends on application for wiring and exposes adapters.

Core abstractions to define (interfaces/)
- LLMCallerProtocol: call_plain, call_with_tools
- ToolExecutorProtocol: execute(tool_calls, context)
- AgentExecutorProtocol: run(input, context)
- ChatConnectionProtocol: send_json, close (FastAPI WebSocket adapter later)
- MessageRepositoryProtocol (optional if persistence emerges)
- SessionRepositoryProtocol (optional; for multi-instance scaling)
- AuthzProtocol: authorize tools/features for a user/context
- HTTPClientProtocol: get/post with typed errors
- FileContextBuilderProtocol: decide what files can enter LLM context

Minimal model layer (domain/)
- Message (role: enum{user,assistant,system,tool}, content: str, meta: dict)
- ConversationHistory (append, list, last_user)
- Session (id, history, context)
- ToolCall, ToolResult
- FileRef (path, mime, size, policy)
- Errors: LLMCallError, ToolError, AuthError, TransportError

Application services (application/)
- ChatService
  - handle_user_message(session, message, options) -> events
  - delegates to LLMCaller / AgentExecutor via interfaces
  - appends to history; emits domain events
- AgentService
  - orchestrates multi-step/tool loops behind interface
- FileContextService
  - composes FilePolicy + FileRef to inform prompts
- EventBus
  - publish/subscribe internal events; transport emits outward

Infrastructure adapters (infrastructure/)
- WebSocketConnectionAdapter (implements ChatConnectionProtocol)
- FastAPI routers moved from core routes to dedicated modules packages
- UnifiedHTTPClient (adapts http client; moved from core/http_client.py)
- S3/File storage adapters (moved from core/files_routes.py into modules/file_storage)
- Otel/logging adapters (moved from core/otel_config.py)

Transport decoupling example
- ChatSession should not hold FastAPI WebSocket directly. Inject ChatConnectionProtocol and ChatService.
- WebSocket endpoint wraps FastAPI WebSocket as ChatConnectionAdapter and forwards to application layer.

Incremental migration plan (phases)
1) Introduce protocols + models (no behavior change)
   - Add interfaces/* Protocols.
   - Add domain/* dataclasses/enums.
   - Create thin adapters that wrap current implementations (e.g., LLM caller in modules/llm).

2) Extract ChatService
   - Move logic from core/chat_session.py into application/chat/service.py using injected protocols.
   - Keep a compatibility ChatSession shim delegating to ChatService.

3) Replace orchestrator singleton
   - Introduce composition root (backend/app_factory.py) to wire ChatService, LLMCaller, ToolExecutor, EventBus.
   - Inject via WebSocket route/session factory.

4) Move HTTP, middleware, observability to infrastructure/
   - http_client.py -> infrastructure/http/unified_client.py
   - middleware.py -> infrastructure/middleware/auth.py
   - otel_config.py -> infrastructure/observability/otel.py

5) Route and API module migration
   - config_routes.py -> modules/config/config_api.py
   - feedback_routes.py -> modules/feedback/feedback_api.py
   - files_routes.py -> modules/file_storage/file_storage_api.py
   - admin_routes.py -> modules/admin/admin_api.py

6) File policy boundary
   - file_config.py -> application/file_context/policy.py (domain types + policy), or modules/file_handling
   - Provide FileContextBuilderProtocol for ChatService to consume.

7) Prompts, LLM, tools
   - prompt_utils.py -> modules/prompts/
   - utils.py: call_llm*, create_agent_completion_tool -> modules/llm/
   - utils.py: validate_selected_tools -> modules/auth/ or modules/tools/

8) Auth consolidation
   - auth.py, auth_utils.py -> modules/auth/
   - Transport adapters use AuthzProtocol from interfaces/.

9) Event bus and emission
   - application/events/event_bus.py: in-memory pub/sub
   - infrastructure/transport/websocket_emitter.py subscribes to events and sends to client
   - Replace direct websocket.send_json calls with event publication.

10) Delete legacy and empty core
   - Remove core/old_session.py if unused.
   - core/utils.py emptied once functions relocated.
   - Optionally move core/orchestrator.py to backend/app_factory.py or delete after DI.

Concrete directory mapping (from current core)
- core/chat_session.py -> application/chat/service.py (+ infrastructure/transport/chat_session_adapter.py)
- core/orchestrator.py -> app_factory.py (composition root) or application/chat/orchestrator.py behind Protocol
- core/utils.py -> modules/llm/, modules/auth/, modules/prompts/ (split)
- core/file_config.py -> application/file_context/policy.py (and/or modules/file_handling)
- core/http_client.py -> infrastructure/http/unified_client.py (+ interfaces/http.py)
- core/middleware.py -> infrastructure/middleware/auth.py
- core/otel_config.py -> infrastructure/observability/otel.py
- core/prompt_utils.py -> modules/prompts/
- core/config_routes.py -> modules/config/config_api.py
- core/feedback_routes.py -> modules/feedback/feedback_api.py
- core/files_routes.py -> modules/file_storage/file_storage_api.py
- core/admin_routes.py -> modules/admin/admin_api.py
- core/auth.py, core/auth_utils.py -> modules/auth/
- core/callbacks.py, core/custom_callbacks.py -> modules/callbacks/

Sample Protocols (interfaces/)
```python
# ...existing code...
from typing import Protocol, Any, Dict, List, Iterable, Optional

class ChatConnectionProtocol(Protocol):
    async def send_json(self, payload: Dict[str, Any]) -> None: ...
    async def close(self, code: int = 1000) -> None: ...

class LLMCallerProtocol(Protocol):
    async def call_plain(self, model: str, messages: List[Dict[str, Any]], **kwargs: Any) -> str: ...
    async def call_with_tools(self, model: str, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]], **kwargs: Any) -> Dict[str, Any]: ...

class ToolExecutorProtocol(Protocol):
    async def execute(self, tool_calls: Iterable[Dict[str, Any]], context: Dict[str, Any]) -> List[Dict[str, Any]]: ...

class AgentExecutorProtocol(Protocol):
    async def run(self, input_messages: List[Dict[str, Any]], context: Dict[str, Any]) -> Dict[str, Any]: ...
```

Chat service sketch (application/)
```python
# ...existing code...
from typing import Dict, Any, List
from interfaces.llm import LLMCallerProtocol
from interfaces.transport import ChatConnectionProtocol

class ChatService:
    def __init__(self, llm: LLMCallerProtocol):
        self._llm = llm

    async def handle_chat(self, session, message: Dict[str, Any], conn: ChatConnectionProtocol) -> None:
        model = message.get("model")
        if not model:
            await conn.send_json({"type": "error", "message": "No model specified."})
            return

        session.history.append({"role": "user", "content": message.get("content")})
        reply = await self._llm.call_plain(model, session.history)
        session.history.append({"role": "assistant", "content": reply})
        await conn.send_json({"type": "chat_response", "message": reply})
```

WebSocket adapter sketch (infrastructure/)
```python
# ...existing code...
from fastapi import WebSocket
from interfaces.transport import ChatConnectionProtocol

class WebSocketConnectionAdapter(ChatConnectionProtocol):
    def __init__(self, ws: WebSocket):
        self._ws = ws

    async def send_json(self, payload):
        await self._ws.send_json(payload)

    async def close(self, code: int = 1000):
        await self._ws.close(code=code)
```

Import rules to avoid cycles
- domain/ imports nothing from application/ or infrastructure/
- interfaces/ imports only typing/domain types
- application/ imports domain + interfaces
- infrastructure/ imports application/ to wire and expose endpoints/adapters

Testing strategy
- Unit test application services with FakeLLMCaller, FakeToolExecutor.
- Contract-test infrastructure adapters against Protocols.
- Snapshot-test event payloads emitted to the frontend.

Measurable checkpoints
- ChatSession no longer imports core.orchestrator.
- No core/* routes remain; all under modules/ or infrastructure/.
- grep for fastapi.WebSocket in domain/application returns nothing.
- CI has unit tests for ChatService and AgentExecutor with fakes.

Risk mitigation
- Keep compatibility shims during migration.
- Move files module-by-module; avoid big-bang rename.
- Add type hints and dataclasses to replace raw dicts incrementally.

End state
- backend/core is empty or only contains transitional shims.
- Clear boundaries, simpler testing, and easier future extension (agents, tools, multi-LLM) without