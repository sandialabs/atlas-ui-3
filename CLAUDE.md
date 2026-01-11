# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Atlas UI 3 is a full-stack LLM chat interface with Model Context Protocol (MCP) integration, supporting multiple LLM providers (OpenAI, Anthropic Claude, Google Gemini), RAG, and agentic capabilities.

**Tech Stack:**
- Backend: FastAPI + WebSockets, LiteLLM, FastMCP
- Frontend: React 19 + Vite 7 + Tailwind CSS
- Python Package Manager: **uv** (NOT pip!)
- Configuration: Pydantic with YAML/JSON configs

# Style note

**No Emojis**: No emojis should ever be added anywhere in this codebase (code, comments, docs, commit messages). If you find one, remove it.

**File Naming**: Do not use generic names like `main.py`, `cli.py`, `utils.py`, or `helpers.py`. Use descriptive names that reflect the file's purpose (e.g., `chat_service.py`, `mcp_tool_manager.py`, `websocket_handler.py`). Exception: top-level entry points like `backend/main.py` are acceptable.

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

**Implementation Summaries**: After completing a long or complex task, write an implementation summary document in `docs/agent-implementation-summaries/` with a descriptive filename and date stamp (e.g., `security-check-feature-2025-12-02.md`). Include what was implemented, key decisions made, and any important technical details for future reference.

# Tests

Before you mark a job as finished, be sure to run the unit test script. 

`bash run_test_shortcut.sh`

All test must pass before a feature is pushed. 

## Critical Setup Requirements

### Python Package Manager
**ALWAYS use `uv`**, never pip or conda:
```bash
# Install uv first
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create venv and install dependencies
uv venv
source .venv/bin/activate
uv pip install -r requirements.txt
```

### Environment Setup
```bash
# Copy and configure environment
cp .env.example .env
# Set DEBUG_MODE=true for development

# Create required directories
mkdir -p logs
```

## Development Commands

### Quick Start (Recommended)
```bash
bash agent_start.sh
```
This script handles: killing old processes, clearing logs, building frontend, starting S3 storage (MinIO or Mock based on `USE_MOCK_S3` in `.env`), and starting backend.

**Options:**
- `bash agent_start.sh -f` - Only rebuild frontend
- `bash agent_start.sh -b` - Only restart backend

**Note:** The script automatically reads `USE_MOCK_S3` from `.env`:
- If `true`: Uses in-process Mock S3 (no Docker)
- If `false`: Starts MinIO via docker-compose

### Manual Development Workflow

**Frontend Build (CRITICAL):**
```bash
cd frontend
npm install
npm run build  # Use build, NOT npm run dev (WebSocket issues)
```

**Backend Start:**
```bash
cd backend
python main.py  # NEVER use uvicorn --reload (causes problems)
```

**S3 Storage (Mock vs MinIO):**

The project supports two S3 storage backends:

1. **Mock S3 (Default, Recommended for Development)**
   - Set `USE_MOCK_S3=true` in `.env`
   - Uses in-process FastAPI TestClient (no Docker required)
   - Files stored in `minio-data/chatui/` on disk
   - No external server needed - integrated directly into backend
   - Faster startup, simpler development workflow

2. **MinIO (Production-like)**
   - Set `USE_MOCK_S3=false` in `.env`
   - Requires Docker: `docker-compose up -d minio minio-init`
   - Full S3 compatibility with all features
   - Use for testing production-like scenarios

The mock automatically activates when the backend starts if `USE_MOCK_S3=true`.

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
# This command auto-installs ruff if not present, then runs the check
ruff check backend/ || (uv pip install ruff && ruff check backend/)
```

**Frontend:**
```bash
cd frontend && npm run lint
```

### Docker

**Build and run:**
```bash
docker build -t atlas-ui-3 .
docker run -p 8000:8000 atlas-ui-3
```

**Container uses Fedora latest** (note: GitHub Actions use Ubuntu runners).

## Architecture Overview

### Backend: Clean Architecture Pattern

```
backend/
├── application/          # Use cases and business logic orchestration
│   └── chat/
│       ├── service.py   # ChatService - main orchestrator
│       ├── agent/       # ReAct and Think-Act agent loops
│       └── utilities/   # Helper functions
├── domain/              # Pure business logic (framework-agnostic)
│   ├── messages/        # Message and conversation models
│   └── sessions/        # Session models
├── infrastructure/      # Framework/external dependencies
│   ├── app_factory.py  # Dependency injection container
│   └── transport/      # WebSocket adapter
├── interfaces/          # Protocol definitions (abstractions)
│   ├── llm.py          # LLMProtocol
│   ├── tools.py        # ToolManagerProtocol
│   └── transport.py    # ChatConnectionProtocol
├── modules/             # Technical implementations
│   ├── llm/            # LiteLLM integration
│   ├── mcp_tools/      # MCP client and tool manager
│   ├── rag/            # RAG client
│   ├── file_storage/   # S3 storage
│   └── prompts/        # Prompt provider
├── core/                # Cross-cutting concerns
│   ├── middleware.py   # Auth, logging
│   ├── auth.py         # Authorization
│   └── otel_config.py  # OpenTelemetry
├── routes/              # HTTP endpoints
└── main.py              # FastAPI app + WebSocket endpoint
```

**Key Architectural Patterns:**

1. **Protocol-Based Dependency Injection**: Uses Python `Protocol` (structural subtyping) instead of ABC inheritance for loose coupling

2. **Agent Loop Strategy Pattern**: Three implementations selectable via `APP_AGENT_LOOP_STRATEGY`:
   - `react`: Reason-Act-Observe cycle (structured reasoning)
   - `think-act`: Extended thinking (slower, complex reasoning)
   - `act`: Pure action loop (fastest, minimal overhead)

3. **MCP Transport Auto-Detection**: Automatically detects stdio, HTTP, or SSE based on config

4. **Configuration Layering**:
   - Code defaults (Pydantic models)
   - `config/defaults/` (versioned)
   - `config/overrides/` (not in repo)
   - Environment variables (highest priority)

### Frontend: Context-Based State Management

```
frontend/src/
├── contexts/         # React Context API (no Redux)
│   ├── ChatContext  # Chat state (messages, selections, canvas)
│   ├── WSContext    # WebSocket lifecycle
│   └── MarketplaceContext  # MCP server discovery
├── components/       # UI components
├── hooks/            # Custom hooks (useMessages, useSelections, etc.)
└── handlers/         # WebSocket message handlers
```

**Event Flow:**
```
User Input → ChatContext → WebSocket → Backend ChatService
  ← Streaming Updates ← tool_use/canvas_content/files_update ←
```

## Important Development Notes

### Critical Restrictions
- **NEVER use `uvicorn --reload`** - causes development issues
- **NEVER use `npm run dev`** - has WebSocket connection problems
- **ALWAYS use `npm run build`** for frontend development
- **NEVER use pip** - this project requires `uv`
- **NEVER CANCEL builds or tests** - they may take time but must complete
- **File limit: 400 lines max** per file for maintainability

### Configuration Files
- **LLM Config**: `config/defaults/llmconfig.yml` and `config/overrides/llmconfig.yml`
- **MCP Servers**: `config/defaults/mcp.json` and `config/overrides/mcp.json`
- **Environment**: `.env` (copy from `.env.example`)

### Prompt System (Updated 2025-11-24)
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

### WebSocket Communication
Backend serves WebSocket at `/ws` with message types:
- `chat` - User sends message
- `download_file` - Request file from S3
- `reset_session` - Clear conversation history

Backend streams responses:
- `token_stream` - Text chunks
- `tool_use` - Tool execution events
- `canvas_content` - HTML/markdown for canvas
- `intermediate_update` - Files, images, etc.

### MCP Integration
MCP servers defined in `config/defaults/mcp.json`. The backend:
1. Auto-detects transport type (stdio/HTTP/SSE)
2. Connects on startup via `MCPToolManager`
3. Exposes tools to LLM via `ToolManagerProtocol`
4. Supports group-based access control

When testing or developing MCP-related features, example configurations can be found in config/mcp-example-configs/ with individual mcp-{servername}.json files for testing individual servers.

### Agent Modes
Three agent loop strategies implement different reasoning patterns:
- **ReAct** (`backend/application/chat/agent/react_loop.py`): Reason-Act-Observe cycle, good for tool-heavy tasks with structured reasoning
- **Think-Act** (`backend/application/chat/agent/think_act_loop.py`): Deep reasoning with explicit thinking steps, slower but more thoughtful
- **Act** (`backend/application/chat/agent/act_loop.py`): Pure action loop without explicit reasoning steps, fastest with minimal overhead. LLM calls tools directly and signals completion via the "finished" tool

### File Storage
S3-compatible storage via `backend/modules/file_storage/`:
- Production/MinIO: `s3_client.py` - boto3-based client for real S3/MinIO
- Development: `mock_s3_client.py` - TestClient-based in-process mock (no Docker)
- Controlled by `USE_MOCK_S3` env var (default: true)
- Both implementations share the same interface

### Security Middleware Stack
```
Request → SecurityHeaders → RateLimit → Auth → Route
```
- Rate limiting before auth to prevent abuse
- Prompt injection risk detection in `backend/core/prompt_risk.py`
- Group-based MCP server access control

### Testing Expectations
- Backend tests: ~5 seconds
- Frontend tests: ~6 seconds
- E2E tests: ~70 seconds (may fail without auth)
- Full suite: ~2 minutes
- Always set adequate timeouts (120-180 seconds for full suite)

### Common Issues

1. **"uv not found"**: Install uv package manager (most common)
2. **WebSocket fails**: Use `npm run build` instead of `npm run dev`
3. **Backend won't start**: Check `.env` exists and `APP_LOG_DIR` is valid
4. **Frontend not loading**: Verify `npm run build` completed
5. **Container build SSL errors**: Use local development instead

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
