# AI Agent Guide: Atlas UI 3

Concise rules for getting productive fast in this repo. Prefer these over exploration; fall back to code/docs only if something is missing.

## Do this first
- Use uv (not pip/conda). One-time: install uv. Then:
   ```bash
   uv venv && source .venv/bin/activate
   uv pip install -r requirements.txt
   bash agent_start.sh   # builds frontend, starts backend, seeds/mocks
   ```
- Manual quick run (alternative):
   ```bash
   (frontend) cd frontend && npm install && npm run build
   (backend)  cd backend && python main.py  # don’t use uvicorn --reload
   ```

## Architecture (big picture)
```
backend/ FastAPI app + WebSocket
   main.py → /ws, serves frontend/dist, includes /api/* routes
   infrastructure/app_factory.py → wires LLM (LiteLLM), MCP, RAG, files, config
   application/chat/service.py → ChatService orchestrator and streaming
   modules/mcp_tools/ → FastMCP client, tool/prompt discovery, auth filtering
   modules/config/manager.py → Pydantic configs + layered search
   domain/rag_mcp_service.py → RAG over MCP discovery/search/synthesis
   core/compliance.py → compliance-levels load/validate/allowlist
frontend/ React 19 + Vite + Tailwind; state via contexts (Chat/WS/Marketplace)
```

## Configuration & feature flags
- Layering (in priority): env vars → config/overrides → config/defaults → legacy backend/configfiles*. Env vars APP_CONFIG_OVERRIDES/DEFAULTS control search roots.
- Files: llmconfig.yml, mcp.json, mcp-rag.json, help-config.json; environment in .env (copy .env.example).
- Feature flags (AppSettings): FEATURE_TOOLS_ENABLED, FEATURE_RAG_MCP_ENABLED, FEATURE_COMPLIANCE_LEVELS_ENABLED, FEATURE_AGENT_MODE_AVAILABLE.

## MCP + RAG conventions
- MCP servers live in mcp.json (tools/prompts) and mcp-rag.json (RAG-only inventory). Fields: groups, transport|type, url|command/cwd, compliance_level.
- Transport detection order: explicit transport → command (stdio) → URL protocol (http/sse) → type fallback.
- Tool names exposed to LLM are fully-qualified: server_toolName. "canvas_canvas" is a pseudo-tool always available.
- RAG over MCP tools expected: rag_discover_resources, rag_get_raw_results, optional rag_get_synthesized_results. RAG resources and servers may include complianceLevel.
- When testing or developing MCP-related features, example configurations can be found in config/mcp-example-configs/ with individual mcp-{servername}.json files for testing individual servers.

## Compliance levels (explicit allowlist)
- Definitions in config/(overrides|defaults)/compliance-levels.json. core/compliance.py loads, normalizes aliases, and enforces allowed_with.
- Validated on load for LLM models, MCP servers, and RAG MCP servers. When FEATURE_COMPLIANCE_LEVELS_ENABLED=true:
   - /api/config includes model and server compliance_level
   - domain/rag_mcp_service filters servers and per-resource „complianceLevel“ using ComplianceLevelManager.is_accessible(user, resource)

## Key APIs/contracts
- WebSocket: /ws. Messages: chat, download_file, reset_session, attach_file. Backend streams token_stream, tool_use, canvas_content, intermediate_update.
- REST: /api/config (models/tools/prompts/data_sources/rag_servers/features), /api/compliance-levels, /admin/* for configs/logs (admin group required).

## Developer workflows
- Tests (don’t cancel):
   ```bash
   ./test/run_tests.sh backend | frontend | e2e | all
   ```
- Lint: uv pip install ruff && ruff check backend/; frontend: npm run lint.
- Logs: project_root/logs/app.jsonl (override with APP_LOG_DIR). Use /admin/logs/*.

## Repo conventions (important)
- Use uv; do not use npm run dev; do not use uvicorn --reload.
- File naming: avoid generic names (utils.py, helpers.py). Prefer descriptive names; backend/main.py is the entry-point exception.
- No emojis anywhere in codebase (code, comments, docs, commit messages). If you find one, remove it.
- Prefer files ≤ ~400 lines when practical.
- Auth assumption: in prod, reverse proxy injects X-User-Email (after stripping client headers); dev falls back to test user.
- Documentation requirements: Every PR or feature MUST include updates to relevant docs in /docs folder (architecture, features, API, config, troubleshooting).
- Implementation summaries: After completing a long or complex task, write an implementation summary document in docs/agent-implementation-summaries/ with a descriptive filename and date stamp (e.g., security-check-feature-2025-12-02.md). Include what was implemented, key decisions made, and important technical details.

## Extend by example
- Add a tool server: edit config/overrides/mcp.json (set groups, transport, url/command, compliance_level). Restart or call discovery on startup.
- Add a RAG provider: edit config/overrides/mcp-rag.json; ensure it exposes rag_* tools; UI consumes /api/config.rag_servers.
- Change agent loop: set AGENT_LOOP_STRATEGY to react | think-act | act; ChatService uses app_settings.agent_loop_strategy.

Common pitfalls: “uv not found” → install uv; frontend not loading → npm run build; missing tools → check MCP transport/URL and server logs; empty lists → check auth groups and compliance filtering.

# Style

No emojis please
