# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Atlas UI 3 is a full-stack LLM chat interface with Model Context Protocol (MCP) integration, supporting multiple LLM providers (OpenAI, Anthropic Claude, Google Gemini), RAG, and agentic capabilities.

**Tech Stack:**
- Backend: FastAPI + WebSockets, LiteLLM, FastMCP
- Frontend: React 19 + Vite 7 + Tailwind CSS
- Python Package Manager: **uv** (NOT pip!)
- Configuration: Pydantic with YAML/JSON configs

## Do This First

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Setup and run
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
bash agent_start.sh   # builds frontend, starts backend, seeds/mocks
```

Manual quick run (alternative):
```bash
(frontend) cd frontend && npm install && npm run build
(backend)  cd backend && python main.py  # don't use uvicorn --reload
```

## Style and Conventions

**No Emojis**: No emojis should ever be added anywhere in this codebase (code, comments, docs, commit messages). If you find one, remove it.

**File Naming**: Do not use generic names like `main.py`, `cli.py`, `utils.py`, or `helpers.py`. Use descriptive names that reflect the file's purpose (e.g., `chat_service.py`, `mcp_tool_manager.py`, `websocket_handler.py`). Exception: top-level entry points like `backend/main.py` are acceptable.

**File Size**: Prefer files with 400 lines or fewer when practical.

**Documentation Requirements**: Every PR or feature implementation MUST include updates to relevant documentation in the `/docs` folder. This includes:
- Architecture changes: Update architecture docs
- New features: Add feature documentation with usage examples
- API changes: Update API documentation
- Configuration changes: Update configuration guides
- Bug fixes: Update troubleshooting docs if applicable

**Changelog Maintenance**: For every PR, add an entry to CHANGELOG.md in the root directory. Each entry should be 1-2 lines describing the core features or changes. Format: "### PR #<number> - YYYY-MM-DD" followed by a bullet point list of changes.

**Documentation Date-Time Stamping**: When creating markdown (.md) files, always include date-time stamps either in the filename or as a header in key sections to help track if docs are stale. Format: `YYYY-MM-DD` or `YYYY-MM-DD HH:MM`. Examples:
- Filename: `feature-plan-2025-11-02.md`
- Section header: `## Implementation Plan (2025-11-02)`
- Status update: `Last updated: 2025-11-02 14:30`

## Claude Code Agents

This project uses Claude Code agents to ensure quality and completeness. Use these agents frequently:

**test-report-runner**: Use this agent frequently after making code changes to run tests and verify correctness. Invoke proactively after each logical chunk of work: implementing a feature, fixing a bug, or refactoring code.

**final-checklist-reviewer**: Use this agent once at the end of a PR, feature, or bug fix to validate that all project requirements, coding standards, and quality gates have been met. This is a final validation step, not something to run after every change. Invoke when work is complete and you hear phrases like "I think I'm done", "ready to merge", "let's create a PR", or "branch is finished".

## Tests

Before you mark a job as finished, be sure to run the unit test script:

```bash
bash run_test_shortcut.sh
```

All tests must pass before a feature is pushed.

## Architecture Overview

### Backend: Clean Architecture Pattern

```
backend/
   main.py              # FastAPI app + WebSocket endpoint at /ws, serves frontend/dist
   infrastructure/
      app_factory.py    # Dependency injection - wires LLM (LiteLLM), MCP, RAG, files, config
   application/
      chat/
         service.py     # ChatService - main orchestrator and streaming
         agent/         # ReAct, Think-Act, and Act agent loops
         utilities/     # Helper functions
   domain/
      messages/         # Message and conversation models
      sessions/         # Session models
      rag_mcp_service.py # RAG over MCP discovery/search/synthesis
   interfaces/          # Protocol definitions (abstractions)
      llm.py            # LLMProtocol
      tools.py          # ToolManagerProtocol
      transport.py      # ChatConnectionProtocol
   modules/
      llm/              # LiteLLM integration
      mcp_tools/        # FastMCP client, tool/prompt discovery, auth filtering
      rag/              # RAG client
      file_storage/     # S3 storage (mock and MinIO)
      prompts/          # Prompt provider
      config/
         config_manager.py  # Pydantic configs + layered search
   core/
      middleware.py     # Auth, logging
      auth.py           # Authorization
      compliance.py     # Compliance-levels load/validate/allowlist
      otel_config.py    # OpenTelemetry
   routes/              # HTTP endpoints
```

### Frontend: Context-Based State Management

```
frontend/src/
   contexts/         # React Context API (no Redux)
      ChatContext    # Chat state (messages, selections, canvas)
      WSContext      # WebSocket lifecycle
      MarketplaceContext  # MCP server discovery
   components/       # UI components
   hooks/            # Custom hooks (useMessages, useSelections, etc.)
   handlers/         # WebSocket message handlers
```

**Event Flow:**
```
User Input -> ChatContext -> WebSocket -> Backend ChatService
  <- Streaming Updates <- tool_use/canvas_content/files_update <-
```

### Key Architectural Patterns

1. **Protocol-Based Dependency Injection**: Uses Python `Protocol` (structural subtyping) instead of ABC inheritance for loose coupling

2. **Agent Loop Strategy Pattern**: Three implementations selectable via `APP_AGENT_LOOP_STRATEGY`:
   - `react`: Reason-Act-Observe cycle (structured reasoning)
   - `think-act`: Extended thinking (slower, complex reasoning)
   - `act`: Pure action loop (fastest, minimal overhead)

3. **MCP Transport Auto-Detection**: Automatically detects stdio, HTTP, or SSE based on config

4. **Configuration Layering** (in priority order):
   - Environment variables (highest priority)
   - `config/overrides/` (not in repo)
   - `config/defaults/` (versioned)
   - Code defaults (Pydantic models)

## Configuration and Feature Flags

### Configuration Files
- **LLM Config**: `config/defaults/llmconfig.yml` and `config/overrides/llmconfig.yml`
- **MCP Servers**: `config/defaults/mcp.json` and `config/overrides/mcp.json`
- **RAG Sources**: `config/defaults/rag-sources.json` and `config/overrides/rag-sources.json`
- **Help Config**: `config/defaults/help-config.json`
- **Compliance Levels**: `config/defaults/compliance-levels.json`
- **Environment**: `.env` (copy from `.env.example`)

### Feature Flags (AppSettings)
- `FEATURE_TOOLS_ENABLED` - Enable/disable MCP tools
- `FEATURE_RAG_MCP_ENABLED` - Enable/disable RAG over MCP
- `FEATURE_COMPLIANCE_LEVELS_ENABLED` - Enable/disable compliance level enforcement
- `FEATURE_AGENT_MODE_AVAILABLE` - Enable/disable agent mode UI toggle

## MCP and RAG Conventions

### MCP Servers
- MCP tool servers live in `mcp.json` (tools/prompts)
- RAG sources (both MCP and HTTP) are configured in `rag-sources.json`
- Fields: `groups`, `transport|type`, `url|command/cwd`, `compliance_level`
- Transport detection order: explicit transport -> command (stdio) -> URL protocol (http/sse) -> type fallback
- Tool names exposed to LLM are fully-qualified: `server_toolName`. `canvas_canvas` is a pseudo-tool always available

### RAG Over MCP
- RAG MCP tools expected: `rag_discover_resources`, `rag_get_raw_results`, optional `rag_get_synthesized_results`
- RAG resources and servers may include `complianceLevel`
- `domain/rag_mcp_service.py` handles RAG discovery/search/synthesis

### Testing MCP Features
When testing or developing MCP-related features, example configurations can be found in `config/mcp-example-configs/` with individual `mcp-{servername}.json` files for testing individual servers.

## Compliance Levels

Definitions in `config/(overrides|defaults)/compliance-levels.json`. `core/compliance.py` loads, normalizes aliases, and enforces `allowed_with`.

When `FEATURE_COMPLIANCE_LEVELS_ENABLED=true`:
- `/api/config` includes model and server `compliance_level`
- `domain/rag_mcp_service` filters servers and per-resource `complianceLevel` using `ComplianceLevelManager.is_accessible(user, resource)`
- Validated on load for LLM models, MCP servers, and RAG MCP servers

## Key APIs and Contracts

### WebSocket API (`/ws`)
**Client Messages:**
- `chat` - User sends message
- `download_file` - Request file from S3
- `reset_session` - Clear conversation history
- `attach_file` - Attach file to conversation

**Server Messages:**
- `token_stream` - Text chunks
- `tool_use` - Tool execution events
- `canvas_content` - HTML/markdown for canvas
- `intermediate_update` - Files, images, etc.

### REST API
- `/api/config` - Models, tools, prompts, data_sources, rag_servers, features
- `/api/compliance-levels` - Compliance level definitions
- `/admin/*` - Configs and logs (admin group required)

## Development Commands

### Quick Start (Recommended)
```bash
bash agent_start.sh
```
This script handles: killing old processes, clearing logs, building frontend, starting S3 storage (MinIO or Mock based on `USE_MOCK_S3` in `.env`), and starting backend.

**Options:**
- `bash agent_start.sh -f` - Only rebuild frontend
- `bash agent_start.sh -b` - Only restart backend

### S3 Storage (Mock vs MinIO)

The project supports two S3 storage backends:

1. **Mock S3 (Default, Recommended for Development)**
   - Set `USE_MOCK_S3=true` in `.env`
   - Uses in-process FastAPI TestClient (no Docker required)
   - Files stored in `minio-data/chatui/` on disk
   - Faster startup, simpler development workflow

2. **MinIO (Production-like)**
   - Set `USE_MOCK_S3=false` in `.env`
   - Requires Docker: `docker-compose up -d minio minio-init`
   - Full S3 compatibility with all features

### Testing

**Run all tests:**
```bash
./test/run_tests.sh all  # Takes ~2 minutes, NEVER CANCEL
```

**Individual test suites:**
```bash
./test/run_tests.sh backend   # ~5 seconds
./test/run_tests.sh frontend  # ~6 seconds
./test/run_tests.sh e2e       # ~70 seconds (may fail without auth config)
```

**Frontend unit tests:**
```bash
cd frontend
npm test              # Run with Vitest
npm run test:ui       # Interactive UI
```

### Linting

**IMPORTANT: Run linting before every commit to catch style issues early.**

**Python:**
```bash
ruff check backend/ || (uv pip install ruff && ruff check backend/)
```

**Frontend:**
```bash
cd frontend && npm run lint
```

### Docker

```bash
docker build -t atlas-ui-3 .
docker run -p 8000:8000 atlas-ui-3
```

**Container uses Fedora latest** (note: GitHub Actions use Ubuntu runners).

## Agent Modes

Three agent loop strategies implement different reasoning patterns:

- **ReAct** (`backend/application/chat/agent/react_loop.py`): Reason-Act-Observe cycle, good for tool-heavy tasks with structured reasoning
- **Think-Act** (`backend/application/chat/agent/think_act_loop.py`): Deep reasoning with explicit thinking steps, slower but more thoughtful
- **Act** (`backend/application/chat/agent/act_loop.py`): Pure action loop without explicit reasoning steps, fastest with minimal overhead. LLM calls tools directly and signals completion via the "finished" tool

Change agent loop: set `APP_AGENT_LOOP_STRATEGY` to `react | think-act | act`; ChatService uses `app_settings.agent_loop_strategy`.

## Prompt System

The application uses a prompt system to manage various LLM prompts:

- **System Prompt**: `prompts/system_prompt.md` - Default system prompt prepended to all conversations
  - Configurable via `system_prompt_filename` in AppSettings (default: `system_prompt.md`)
  - Supports `{user_email}` template variable
  - Can be overridden by MCP-provided prompts
  - Loaded by `PromptProvider.get_system_prompt()`
  - Automatically injected by `MessageBuilder` at conversation start

- **Agent Prompts**: Used in agent loop strategies
  - `prompts/agent_reason_prompt.md` - Reasoning phase
  - `prompts/agent_observe_prompt.md` - Observation phase

- **Tool Synthesis**: `prompts/tool_synthesis_prompt.md` - Tool selection guidance

All prompts are loaded from the directory specified by `prompt_base_path` (default: `prompts/`). The system caches loaded prompts for performance.

## Security

### Middleware Stack
```
Request -> SecurityHeaders -> RateLimit -> Auth -> Route
```
- Rate limiting before auth to prevent abuse
- Prompt injection risk detection in `backend/core/prompt_risk.py`
- Group-based MCP server access control

### Auth Assumption
In production, reverse proxy injects `X-User-Email` (after stripping client headers); dev falls back to test user.

## Extend by Example

**Add a tool server:**
Edit `config/overrides/mcp.json` (set `groups`, `transport`, `url/command`, `compliance_level`). Restart or call discovery on startup.

**Add a RAG provider:**
Edit `config/overrides/rag-sources.json`. For MCP RAG servers, set `type: "mcp"` and ensure it exposes `rag_*` tools. For HTTP RAG APIs, set `type: "http"` with `url` and `bearer_token`. UI consumes `/api/config.rag_servers`.

**Change agent loop:**
Set `APP_AGENT_LOOP_STRATEGY` to `react | think-act | act`; ChatService uses `app_settings.agent_loop_strategy`.

## Common Issues

1. **"uv not found"**: Install uv package manager (most common)
2. **WebSocket fails**: Use `npm run build` instead of `npm run dev`
3. **Backend won't start**: Check `.env` exists and `APP_LOG_DIR` is valid
4. **Frontend not loading**: Verify `npm run build` completed
5. **Container build SSL errors**: Use local development instead
6. **Missing tools**: Check MCP transport/URL and server logs
7. **Empty lists**: Check auth groups and compliance filtering

## Validation Workflow

Before committing:
1. **Lint**: Address style issues before running tests
   - Python: `ruff check backend/ || (uv pip install ruff && ruff check backend/)`
   - Frontend: `cd frontend && npm run lint`
2. **Test**: `./test/run_tests.sh all`
3. **Build**: Frontend and backend build successfully
4. **Manual**: Test in browser at http://localhost:8000
5. **Exercise**: Test specific modified functionality

Before creating or accepting a PR:
- Run `cd frontend && npm run lint` to ensure no frontend syntax errors or style issues

## Key File References

When referencing code locations, use `file_path:line_number` format for easy navigation.

**Core Entry Points:**
- Backend: `backend/main.py` - FastAPI app + WebSocket
- Frontend: `frontend/src/main.jsx` - React app entry
- Chat Service: `backend/application/chat/service.py:ChatService`
- Config Management: `backend/modules/config/config_manager.py`
- MCP Integration: `backend/modules/mcp_tools/mcp_tool_manager.py`

**Protocol Definitions:**
- `backend/interfaces/llm.py:LLMProtocol`
- `backend/interfaces/tools.py:ToolManagerProtocol`
- `backend/interfaces/transport.py:ChatConnectionProtocol`

## Critical Restrictions

- **NEVER use `uvicorn --reload`** - causes development issues
- **NEVER use `npm run dev`** - has WebSocket connection problems
- **ALWAYS use `npm run build`** for frontend development
- **NEVER use pip** - this project requires `uv`
- **NEVER CANCEL builds or tests** - they may take time but must complete
