# GitHub Copilot Guide: Atlas UI 3

Concise rules for getting productive fast in this repo. Prefer these over exploration; fall back to code/docs only if something is missing.

**PyPI Packaging**: The CI workflow bundles the frontend into `atlas/static/` before building the wheel; at runtime `atlas/main.py` checks `atlas/static/` first (package install) then falls back to `frontend/dist/` (local dev), so both paths work transparently.

## Do This First

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Setup and run
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
bash agent_start.sh   # builds frontend, starts atlas backend, seeds/mocks
```

Manual quick run (alternative):
```bash
(frontend) cd frontend && npm install && npm run build
(atlas)    cd atlas && python main.py  # don't use uvicorn --reload
```

## Style and Conventions

**No Emojis**: No emojis anywhere in codebase (code, comments, docs, commit messages). If you find one, remove it.

**File Naming**: Avoid generic names (`utils.py`, `helpers.py`). Prefer descriptive names; `atlas/main.py` is the entry-point exception.

**File Size**: Prefer files with 400 lines or fewer when practical.

**Documentation**: Every PR or feature MUST include updates to relevant docs in `/docs` folder.

**Changelog**: For every PR, add an entry to CHANGELOG.md. Format: "### PR #<number> - YYYY-MM-DD" followed by bullet points.

**AI Instruction File Maintenance**: For every PR, you MUST do the following for all three AI instruction files (`CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`):
1. **Add one helpful sentence** to each file that captures a useful insight, convention, or lesson learned from the PR's changes (e.g., a new pattern introduced, a gotcha discovered, or a clarification of existing behavior).
2. **Scan all three files for stale or out-of-date information** (e.g., references to renamed directories, removed features, changed commands, or outdated architecture descriptions). If stale content is found, **warn the user** about what is outdated and where, but do **NOT** delete or modify the stale content unless the user explicitly asks you to.

**Date Stamps**: Include date-time stamps in markdown files (filename or section header). Format: `YYYY-MM-DD`.

## Architecture Overview

```
atlas/
   main.py              # FastAPI app + WebSocket endpoint at /ws, serves frontend/dist
   infrastructure/
      app_factory.py    # Dependency injection - wires LLM (LiteLLM), MCP, RAG, files, config
   application/
      chat/
         service.py     # ChatService - main orchestrator and streaming
         agent/         # ReAct, Think-Act, and Act agent loops
   domain/
      messages/         # Message and conversation models
      sessions/         # Session models
      rag_mcp_service.py # RAG over MCP discovery/search/synthesis
   interfaces/          # Protocol definitions (LLMProtocol, ToolManagerProtocol, ChatConnectionProtocol)
   modules/
      llm/              # LiteLLM integration
      mcp_tools/        # FastMCP client, tool/prompt discovery, auth filtering
      rag/              # RAG client
      file_storage/     # S3 storage (mock and MinIO)
      config/
         config_manager.py  # Pydantic configs + layered search
   core/
      middleware.py     # Auth, logging
      compliance.py     # Compliance-levels load/validate/allowlist
      prompt_risk.py    # Prompt injection risk detection
   routes/              # HTTP endpoints

frontend/src/
   contexts/            # React Context API (no Redux) - ChatContext, WSContext, MarketplaceContext
   components/          # UI components
   hooks/               # Custom hooks (useMessages, useSelections, etc.)
   handlers/            # WebSocket message handlers
```

## Configuration and Feature Flags

**Two-layer config**: User config in `config/` (created by `atlas-init`, set `APP_CONFIG_DIR` to customize) overrides package defaults in `atlas/config/`.

**Files:**
- `llmconfig.yml` - LLM model configurations
- `mcp.json` - MCP tool servers
- `rag-sources.json` - RAG sources (MCP and HTTP)
- `help-config.json` - Help system configuration
- `compliance-levels.json` - Compliance level definitions
- `.env` - Environment variables (copy from `.env.example`)

**Feature Flags (AppSettings):**
- `FEATURE_TOOLS_ENABLED` - Enable/disable MCP tools
- `FEATURE_RAG_MCP_ENABLED` - Enable/disable RAG over MCP
- `FEATURE_COMPLIANCE_LEVELS_ENABLED` - Enable/disable compliance level enforcement
- `FEATURE_AGENT_MODE_AVAILABLE` - Enable/disable agent mode UI toggle

## MCP and RAG Conventions

**MCP Servers:**
- MCP tool servers live in `mcp.json` (tools/prompts)
- RAG sources (MCP and HTTP) are configured in `rag-sources.json`
- Fields: `groups`, `transport|type`, `url|command/cwd`, `compliance_level`
- Transport detection: explicit transport -> command (stdio) -> URL protocol (http/sse) -> type fallback
- Tool names are fully-qualified: `server_toolName`. `canvas_canvas` is a pseudo-tool always available

**RAG Over MCP:**
- Expected tools: `rag_discover_resources`, `rag_get_raw_results`, optional `rag_get_synthesized_results`
- Resources and servers may include `complianceLevel`

**Testing MCP:**
Example configurations in `atlas/config/mcp-example-configs/` with individual `mcp-{servername}.json` files.

## Compliance Levels

Definitions in `config/(overrides|defaults)/compliance-levels.json`. `core/compliance.py` loads, normalizes aliases, and enforces `allowed_with`.

When `FEATURE_COMPLIANCE_LEVELS_ENABLED=true`:
- `/api/config` includes model and server `compliance_level`
- `domain/rag_mcp_service` filters using `ComplianceLevelManager.is_accessible(user, resource)`

## Key APIs and Contracts

**WebSocket (`/ws`):**
- Client messages: `chat`, `download_file`, `reset_session`, `attach_file`
- Server messages: `token_stream`, `tool_use`, `canvas_content`, `intermediate_update`

**REST API:**
- `/api/config` - Models, tools, prompts, data_sources, rag_servers, features
- `/api/compliance-levels` - Compliance level definitions
- `/api/feedback` - Submit (POST) and view (GET, admin) user feedback; conversation history is stored inline in the feedback JSON when the user opts in
- `/admin/*` - Configs and logs (admin group required)

## Agent Modes

Three agent loop strategies selectable via `APP_AGENT_LOOP_STRATEGY`:

- **ReAct** (`atlas/application/chat/agent/react_loop.py`): Reason-Act-Observe cycle, good for tool-heavy tasks with structured reasoning
- **Think-Act** (`atlas/application/chat/agent/think_act_loop.py`): Deep reasoning with explicit thinking steps, slower but more thoughtful
- **Act** (`atlas/application/chat/agent/act_loop.py`): Pure action loop without explicit reasoning steps, fastest with minimal overhead. LLM calls tools directly and signals completion via the "finished" tool

## Prompt System

- **System Prompt**: `prompts/system_prompt.md` - Prepended to all conversations
  - Supports `{user_email}` template variable
  - Can be overridden by MCP-provided prompts
- **Agent Prompts**: `prompts/agent_reason_prompt.md`, `prompts/agent_observe_prompt.md`
- **Tool Synthesis**: `prompts/tool_synthesis_prompt.md`

Prompts loaded from `prompt_base_path` (default: `prompts/`) and cached.

## Security

**Middleware Stack:**
```
Request -> SecurityHeaders -> RateLimit -> Auth -> Route
```

- Rate limiting before auth to prevent abuse
- Prompt injection risk detection in `atlas/core/prompt_risk.py`
- Group-based MCP server access control
- Auth: In prod, reverse proxy injects `X-User-Email`; dev falls back to test user

## Development Commands

**Testing (don't cancel):**
```bash
./test/run_tests.sh all      # ~2 minutes
./test/run_tests.sh atlas    # ~5 seconds
./test/run_tests.sh frontend # ~6 seconds
./test/run_tests.sh e2e      # ~70 seconds
```

**Linting:**
```bash
ruff check atlas/ || (uv pip install ruff && ruff check atlas/)
cd frontend && npm run lint
```

**Logs:** `project_root/logs/app.jsonl` (override with `APP_LOG_DIR`). Use `/admin/logs/*`.

## PR Validation Scripts (Required)

**Any PR that changes atlas (Python) code MUST include a validation script** in `test/pr-validation/` before the code is committed, the PR is created, or the PR is reviewed/merged.

**Naming:** `test_pr{NUMBER}_{short_description}.sh` (e.g., `test_pr271_cli_rag_features.sh`)

**What goes in the script:** Every item from the PR's "Test plan" section, plus an atlas unit test run at the end. See `test/pr-validation/README.md` for the full template and structure.

**IMPORTANT: Scripts must exercise features end-to-end using actual CLI commands and tools.** Do not write validation scripts that only check imports, parse flags, or run unit tests. The script must invoke the feature as a real user would -- by running CLI commands (`python atlas_chat_cli.py ...`), calling API endpoints (`curl`), starting the server and checking behavior, or running actual tooling. Import checks and unit tests are supplementary, not the primary validation.

**Custom .env and config files for testing:** PR validation scripts can and should create custom `.env` files and config overrides to test different feature flag combinations. Store test-specific config files in `test/pr-validation/fixtures/pr{NUMBER}/` (e.g., `test/pr-validation/fixtures/pr264/.env`). This allows testing with `FEATURE_*` flags set to specific values without modifying the project's real `.env` or config files.

**Running:**
```bash
bash test/run_pr_validation.sh 271       # Run one PR
bash test/run_pr_validation.sh            # Run all
bash test/run_pr_validation.sh --list     # List available
```

## Validation Workflow

Before committing:
1. **Lint**: Python and frontend
2. **PR validation script**: If atlas code changed, write and run `test/pr-validation/test_pr{N}_{desc}.sh`
3. **Test**: `./test/run_tests.sh all`
4. **Build**: Frontend and atlas build successfully
5. **Manual**: Test in browser at http://localhost:8000

Before PR:
- Run `cd frontend && npm run lint` to ensure no frontend syntax errors
- Run `bash test/run_pr_validation.sh {PR_NUMBER}` to verify the PR validation script passes

## Key File References

Use `file_path:line_number` format for easy navigation.

**Core Entry Points:**
- Atlas: `atlas/main.py` - FastAPI app + WebSocket
- Frontend: `frontend/src/main.jsx` - React app entry
- Chat Service: `atlas/application/chat/service.py:ChatService`
- Config: `atlas/modules/config/config_manager.py`
- MCP: `atlas/modules/mcp_tools/mcp_tool_manager.py`

**Protocol Definitions:**
- `atlas/interfaces/llm.py:LLMProtocol`
- `atlas/interfaces/tools.py:ToolManagerProtocol`
- `atlas/interfaces/transport.py:ChatConnectionProtocol`

## Extend by Example

**Add a tool server:**
Edit `config/mcp.json` (your local config, created by `atlas-init`). Set `groups`, `transport`, `url/command`, `compliance_level`. Restart atlas.

**Add a RAG provider:**
Edit `config/rag-sources.json` (your local config). For MCP RAG servers, set `type: "mcp"` and ensure it exposes `rag_*` tools. For HTTP RAG APIs, set `type: "http"` with `url` and `bearer_token`. UI consumes `/api/config.rag_servers`.

**Change agent loop:**
Set `APP_AGENT_LOOP_STRATEGY` to `react | think-act | act`.

## Common Issues

1. **"uv not found"**: Install uv package manager
2. **WebSocket fails**: Use `npm run build` instead of `npm run dev`
3. **Server won't start**: Check `.env` exists and `APP_LOG_DIR` is valid
4. **Frontend not loading**: Verify `npm run build` completed
5. **Missing tools**: Check MCP transport/URL and server logs
6. **Empty lists**: Check auth groups and compliance filtering

## Critical Restrictions

- **NEVER use `uvicorn --reload`** - causes development issues
- **NEVER use `npm run dev`** - has WebSocket connection problems
- **ALWAYS use `npm run build`** for frontend development
- **NEVER use pip** - this project requires `uv`
- **NEVER CANCEL builds or tests** - they may take time but must complete
