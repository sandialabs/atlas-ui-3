# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Atlas UI 3 is a full-stack LLM chat interface with Model Context Protocol (MCP) integration, supporting multiple LLM providers (OpenAI, Anthropic Claude, Google Gemini), RAG, and agentic capabilities.

**Tech Stack:**
- Backend: FastAPI + WebSockets, LiteLLM, FastMCP
- Frontend: React 19 + Vite 7 + Tailwind CSS
- Python Package Manager: **uv** (NOT pip!)
- Configuration: Pydantic with YAML/JSON configs

**PyPI Packaging**: The CI workflow bundles the frontend into `atlas/static/` before building the wheel; at runtime `atlas/main.py` checks `atlas/static/` first (package install) then falls back to `frontend/dist/` (local dev), so both paths work transparently.

**Dependency Management**: All Python dependencies are defined in `pyproject.toml` (the single source of truth); there is no `requirements.txt` -- always use `uv pip install -e ".[dev]"` for development.

**Lazy Imports**: `atlas/__init__.py` uses `__getattr__` to lazily import `AtlasClient` and `ChatResult` so that lightweight CLIs like `atlas-init` do not pay the cost of loading the full dependency chain (SQLAlchemy, litellm, FastAPI, etc.).

## Installation

### As a Python Package (Recommended for Users)

```bash
# Install from PyPI
pip install atlas-chat

# Or with uv
uv pip install atlas-chat

# Use the CLI tools
atlas-chat "Hello, how are you?" --model gpt-4o
atlas-server --port 8000

# Or use programmatically
from atlas import AtlasClient
client = AtlasClient()
result = await client.chat("Hello!")
print(result.message)
```

### For Development

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Setup and run
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
bash agent_start.sh   # builds frontend, starts backend, seeds/mocks
```

Manual quick run (alternative):
```bash
(frontend) cd frontend && npm install && npm run build
(backend)  cd atlas && python main.py  # don't use uvicorn --reload
```

## Style and Conventions

**No Emojis**: No emojis should ever be added anywhere in this codebase (code, comments, docs, commit messages). If you find one, remove it.

**File Naming**: Do not use generic names like `main.py`, `cli.py`, `utils.py`, or `helpers.py`. Use descriptive names that reflect the file's purpose (e.g., `chat_service.py`, `mcp_tool_manager.py`, `websocket_handler.py`). Exception: top-level entry points like `atlas/main.py` are acceptable.

**File Size**: Prefer files with 400 lines or fewer when practical.

**Documentation Requirements**: Every PR or feature implementation MUST include updates to relevant documentation in the `/docs` folder. This includes:
- Architecture changes: Update architecture docs
- New features: Add feature documentation with usage examples
- API changes: Update API documentation
- Configuration changes: Update configuration guides
- Bug fixes: Update troubleshooting docs if applicable

**Changelog Maintenance**: For every PR, add an entry to CHANGELOG.md in the root directory. Each entry should be 1-2 lines describing the core features or changes. Format: "### PR #<number> - YYYY-MM-DD" followed by a bullet point list of changes.

**AI Instruction File Maintenance**: For every PR, you MUST do the following for all three AI instruction files (`CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`):
1. **Add one helpful sentence** to each file that captures a useful insight, convention, or lesson learned from the PR's changes (e.g., a new pattern introduced, a gotcha discovered, or a clarification of existing behavior).
2. **Scan all three files for stale or out-of-date information** (e.g., references to renamed directories, removed features, changed commands, or outdated architecture descriptions). If stale content is found, **warn the user** about what is outdated and where, but do **NOT** delete or modify the stale content unless the user explicitly asks you to.

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
atlas/
   main.py              # FastAPI app + WebSocket endpoint at /ws, serves frontend/dist
   __init__.py          # Package exports (AtlasClient, ChatResult)
   atlas_client.py      # Python API client for programmatic use
   atlas_chat_cli.py    # CLI tool entry point
   server_cli.py        # Server CLI entry point (atlas-server)
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

**Stale Selection Cleanup:** ChatContext validates persisted tool/prompt selections against the current `/api/config` response and removes entries that no longer exist (e.g., removed servers, changed authorization). MarketplaceContext similarly prunes stale server selections. Follow this pattern when adding new persisted selections.

**Polling with Backoff:** All frontend polling must use exponential backoff with jitter on failures to prevent backend DOS. Use the shared `usePollingWithBackoff` hook or `calculateBackoffDelay` from `hooks/usePollingWithBackoff.js`. Never use bare `setInterval` for backend polling.

**RAG Activation vs Selection:** In `ChatContext.sendChatMessage`, data sources are only sent to the backend when RAG is explicitly activated (`ragEnabled` toggle or `/search` command). Selecting data sources in the UI only marks availability; the backend orchestrator routes to RAG mode only when `selected_data_sources` is non-empty, so the frontend must gate what it sends.

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

4. **Two-Layer Configuration**: User config in `config/` (created by `atlas-init`) overrides package defaults in `atlas/config/`. Set `APP_CONFIG_DIR` to customize the user config directory.

## Configuration and Feature Flags

### Configuration Files
- **LLM Config**: `atlas/config/llmconfig.yml` (user overrides in `config/llmconfig.yml` or via `--llm-config`)
- **MCP Servers**: `atlas/config/mcp.json` (user overrides in `config/mcp.json` or via `--mcp-config`)
- **RAG Sources**: `atlas/config/rag-sources.json` (user overrides in `config/rag-sources.json` or via `--rag-sources-config`)
- **Help Config**: `atlas/config/help-config.json`
- **Compliance Levels**: `atlas/config/compliance-levels.json`
- **MCP Examples**: `atlas/config/mcp-example-configs/` (shipped with package)
- **Environment**: `.env` (copy from `.env.example`)

### Feature Flags (AppSettings)
- `FEATURE_TOOLS_ENABLED` - Enable/disable MCP tools
- `FEATURE_RAG_MCP_ENABLED` - Enable/disable RAG over MCP
- `FEATURE_COMPLIANCE_LEVELS_ENABLED` - Enable/disable compliance level enforcement
- `FEATURE_AGENT_MODE_AVAILABLE` - Enable/disable agent mode UI toggle

## Per-User LLM API Keys

Models in `llmconfig.yml` can set `api_key_source: "user"` to require per-user API keys instead of system env vars. The `MCPTokenStorage` is reused with `"llm:{model_name}"` as the server_name key, and `user_email` is threaded through all LLM call paths (`LLMProtocol`, `LiteLLMCaller`, agent loops, orchestrator). REST endpoints live at `/api/llm/auth/` and the frontend reuses `TokenInputModal`.

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

### PPTX Generator MCP Server
The `pptx_generator` MCP server (`atlas/mcp/pptx_generator/main.py`) uses a three-tier layout strategy: custom template file (via `PPTX_TEMPLATE_PATH` env var or search paths) -> built-in Office "Title and Content" layout -> blank layout with manual textboxes.

### Testing MCP Features
When testing or developing MCP-related features, example configurations can be found in `atlas/config/mcp-example-configs/` with individual `mcp-{servername}.json` files for testing individual servers.

## Compliance Levels

Definitions in `atlas/config/compliance-levels.json` with user overrides in `config/compliance-levels.json`. `core/compliance.py` loads, normalizes aliases, and enforces `allowed_with`.

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
- `tool_start` / `tool_progress` / `tool_complete` - Direct tool lifecycle events with status transitions (`calling` -> `in_progress` -> `completed`/`failed`); Message.jsx renders spinners and elapsed timers for active states
- `canvas_content` - HTML/markdown for canvas
- `intermediate_update` - Files, images, etc.

### REST API
- `/api/heartbeat` - Minimal uptime check (`{"status":"ok"}`), no auth, rate-limited
- `/api/health` - Service status with version and timestamp, no auth, rate-limited
- `/api/config` - Models, tools, prompts, data_sources, rag_servers, features
- `/api/compliance-levels` - Compliance level definitions
- `/api/feedback` - Submit (POST) and view (GET, admin) user feedback; conversation history is stored inline in the feedback JSON when the user opts in
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
ruff check atlas/ || (uv pip install ruff && ruff check atlas/)
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

**Container uses RHEL 9 UBI** (note: GitHub Actions use Ubuntu runners).

## Agent Modes

Three agent loop strategies implement different reasoning patterns:

- **ReAct** (`atlas/application/chat/agent/react_loop.py`): Reason-Act-Observe cycle, good for tool-heavy tasks with structured reasoning
- **Think-Act** (`atlas/application/chat/agent/think_act_loop.py`): Deep reasoning with explicit thinking steps, slower but more thoughtful
- **Act** (`atlas/application/chat/agent/act_loop.py`): Pure action loop without explicit reasoning steps, fastest with minimal overhead. LLM calls tools directly and signals completion via the "finished" tool

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
- To bypass auth for a new endpoint, add it to the path check in `AuthMiddleware.dispatch()` (`atlas/core/middleware.py`); rate limiting still applies to bypassed routes
- Prompt injection risk detection in `atlas/core/prompt_risk.py`
- Group-based MCP server access control

### Auth Assumption
In production, reverse proxy injects `X-User-Email` (after stripping client headers); dev falls back to test user.

## Extend by Example

**Add a tool server:**
Edit `config/mcp.json` (your local config, created by `atlas-init`). Set `groups`, `transport`, `url/command`, `compliance_level`. Restart or call discovery on startup.

**Add a RAG provider:**
Edit `config/rag-sources.json` (your local config). For MCP RAG servers, set `type: "mcp"` and ensure it exposes `rag_*` tools. For HTTP RAG APIs, set `type: "http"` with `url` and `bearer_token`. UI consumes `/api/config.rag_servers`.

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
8. **Host binding ignored**: `agent_start.sh` and the Dockerfile both use `ATLAS_HOST` env var for host binding; `main.py` also reads it directly -- keep all three in sync when changing network configuration
9. **DuckDB FK constraints**: DuckDB does not support CASCADE or UPDATE on foreign-key-constrained tables; the `chat_history` module avoids all database-level ForeignKey constraints and enforces referential integrity manually in the repository layer instead
10. **Chat history default**: Chat history with DuckDB is enabled by default in `.env.example`; new setups get conversation persistence without extra configuration

## PR Validation Scripts (Required)

**Any PR that changes backend code MUST include a validation script** in `test/pr-validation/` before the code is committed, the PR is created, or the PR is reviewed/merged.

**Naming:** `test_pr{NUMBER}_{short_description}.sh` (e.g., `test_pr271_cli_rag_features.sh`)

**What goes in the script:** Every item from the PR's "Test plan" section, plus a backend unit test run at the end. See `test/pr-validation/README.md` for the full template and structure.

**IMPORTANT: Scripts must exercise features end-to-end using actual CLI commands and tools.** Do not write validation scripts that only check imports, parse flags, or run unit tests. The script must invoke the feature as a real user would -- by running CLI commands (`python atlas_chat_cli.py ...`), calling API endpoints (`curl`), starting the backend and checking behavior, or running actual tooling. Import checks and unit tests are supplementary, not the primary validation.

Examples of end-to-end validation:
- Run `python atlas_chat_cli.py --list-tools` to verify CLI features work
- Start the backend and hit API endpoints with `curl` to verify behavior
- Set environment variables and run commands to verify feature flags take effect
- Invoke tool use via CLI: `python atlas_chat_cli.py "query" --tools tool_name`

**Custom .env and config files for testing:** PR validation scripts can and should create custom `.env` files and config overrides to test different feature flag combinations. Store test-specific config files in `test/pr-validation/fixtures/pr{NUMBER}/` (e.g., `test/pr-validation/fixtures/pr264/.env`). This allows testing with `FEATURE_*` flags set to specific values without modifying the project's real `.env` or config files.

**Running:**
```bash
bash test/run_pr_validation.sh 271       # Run one PR
bash test/run_pr_validation.sh            # Run all
bash test/run_pr_validation.sh --list     # List available
```

**When creating a PR validation script:**
1. Write the script in `test/pr-validation/test_pr{NUMBER}_{description}.sh`
2. Use `PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"` to locate the project root
3. Activate `.venv/bin/activate`, run assertions, print PASSED/FAILED per check
4. **Run actual CLI commands and tools** to exercise the feature end-to-end
5. Always run `./test/run_tests.sh backend` as the final step
6. Exit 0 on success, non-zero on failure

**When reviewing a PR:** Verify the validation script exists and passes before approving.

## Validation Workflow

Before committing:
1. **Lint**: Address style issues before running tests
   - Python: `ruff check atlas/ || (uv pip install ruff && ruff check atlas/)`
   - Frontend: `cd frontend && npm run lint`
2. **PR validation script**: If backend code changed, write and run `test/pr-validation/test_pr{N}_{desc}.sh`
3. **Test**: `./test/run_tests.sh all`
4. **Build**: Frontend and backend build successfully
5. **Manual**: Test in browser at http://localhost:8000
6. **Exercise**: Test specific modified functionality

Before creating or accepting a PR:
- Run `cd frontend && npm run lint` to ensure no frontend syntax errors or style issues
- Run `bash test/run_pr_validation.sh {PR_NUMBER}` to verify the PR validation script passes

## Key File References

When referencing code locations, use `file_path:line_number` format for easy navigation.

**Core Entry Points:**
- Backend: `atlas/main.py` - FastAPI app + WebSocket
- Frontend: `frontend/src/main.jsx` - React app entry
- Chat Service: `atlas/application/chat/service.py:ChatService`
- Config Management: `atlas/modules/config/config_manager.py`
- MCP Integration: `atlas/modules/mcp_tools/mcp_tool_manager.py`

**Protocol Definitions:**
- `atlas/interfaces/llm.py:LLMProtocol`
- `atlas/interfaces/tools.py:ToolManagerProtocol`
- `atlas/interfaces/transport.py:ChatConnectionProtocol`

## Critical Restrictions

- **NEVER use `uvicorn --reload`** - causes development issues
- **NEVER use `npm run dev`** - has WebSocket connection problems
- **ALWAYS use `npm run build`** for frontend development
- **NEVER use pip** - this project requires `uv`
- **NEVER CANCEL builds or tests** - they may take time but must complete
