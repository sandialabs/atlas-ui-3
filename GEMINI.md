# GEMINI.md

This file provides guidance to the Gemini AI agent when working with code in this repository.

## Project Overview

Atlas UI 3 is a full-stack LLM chat interface with Model Context Protocol (MCP) integration, supporting multiple LLM providers (OpenAI, Anthropic Claude, Google Gemini), RAG, and agentic capabilities.

**Tech Stack:**
- Backend: FastAPI + WebSockets, LiteLLM, FastMCP
- Frontend: React 19 + Vite 7 + Tailwind CSS
- Python Package Manager: **uv** (NOT pip!)
- Configuration: Pydantic with YAML/JSON configs

## Building and Running

### Quick Start (Recommended)
```bash
bash agent_start.sh
```
This script handles: killing old processes, clearing logs, building frontend, and starting the backend.

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

### Testing

**Run all tests:**
```bash
./test/run_tests.sh all
```

**Individual test suites:**
```bash
./test/run_tests.sh backend
./test/run_tests.sh frontend
./test/run_tests.sh e2e
```

## Development Conventions

- **Python Package Manager**: **ALWAYS use `uv`**, never pip or conda.
- **Frontend Development**: **NEVER use `npm run dev`**, it has WebSocket connection problems. Always use `npm run build`.
- **Backend Development**: **NEVER use `uvicorn --reload`**, it causes problems.
- **File Naming**: Do not use generic names like `utils.py` or `helpers.py`. Use descriptive names that reflect the file's purpose.
- **No Emojis**: No emojis should ever be added anywhere in this codebase (code, comments, docs, commit messages). If you find one, remove it.
- **Linting**: Run `ruff check backend/` for Python and `npm run lint` for the frontend before committing.
- **Documentation Requirements**: Every PR or feature implementation MUST include updates to relevant documentation in the `/docs` folder. This includes:
  - Architecture changes: Update architecture docs
  - New features: Add feature documentation with usage examples
  - API changes: Update API documentation
  - Configuration changes: Update configuration guides
  - Bug fixes: Update troubleshooting docs if applicable
- **Changelog Maintenance**: For every PR, add an entry to CHANGELOG.md in the root directory. Each entry should be 1-2 lines describing the core features or changes. Format: "### PR #<number> - YYYY-MM-DD" followed by a bullet point list of changes.

When testing or developing MCP-related features, example configurations can be found in config/mcp-example-configs/ with individual mcp-{servername}.json files for testing individual servers.


Also read.
/workspaces/atlas-ui-3/.github/copilot-instructions.md

and CLAUDE.md
