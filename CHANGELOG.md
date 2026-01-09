# Changelog

All notable changes to Atlas UI 3 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### PR #197 - 2026-01-08
- **Configuration**: Synchronized docker-compose.yml environment variables with .env.example. Added all missing feature flags, API keys, agent configuration, and other application settings to ensure Docker deployments have the same configuration options as local development.
- **CI**: Updated test container build to include `.env.example` and `docker-compose.yml` so docker env sync tests can run.

### 2026-01-07 - Elicitation Routing Fix and Testing
- **Fix**: Resolve elicitation dialog not appearing by switching from `contextvars.ContextVar` to dictionary-based routing. The MCP receive loop runs in a separate asyncio task that cannot access context variables set in the tool execution task. Now uses per-server routing with proper cross-task visibility.
- **Fix**: Add `setPendingElicitation` to WebSocket handler destructuring so dialog state updates work correctly.
- **Fix**: Add `sendMessage` to ChatContext exports so ElicitationDialog can send responses.
- **Fix**: Close elicitation dialog after user responds (accept/decline/cancel).
- Add comprehensive logging to trace `update_callback` flow from WebSocket to MCP tool execution.
- Add validation and fallback mechanism in `ToolsModeRunner` to ensure update_callback is never None during tool execution.
- Create per-server elicitation handlers using closures to capture server_name for proper routing.
- **Tests**: Add comprehensive unit tests for elicitation routing (8 backend tests, 7 frontend tests) to prevent regression.

### PR #191 - 2026-01-06
- **MCP Tool Elicitation Support**: Implemented full support for MCP tool elicitation (FastMCP 2.10.0+), allowing tools to request structured user input during execution via `ctx.elicit()`. Includes backend elicitation manager, WebSocket message handling, and a modal dialog UI supporting string, number, boolean, enum, and structured multi-field forms.
- **Elicitation Demo Server**: Added `elicitation_demo` MCP server showcasing all elicitation types including scalar inputs, enum selections, structured forms, multi-turn flows, and approval-only requests.
- Fix elicitation handler integration to use `client.set_elicitation_callback()` instead of passing as kwarg (resolves FastMCP API compatibility).
- Admin UI: Fix duplicate "MCP Configuration & Controls" card rendering.
- Admin UI: Clarify MCP Server Manager note that available configs are loaded from `config/mcp-example-configs/`.

### PR #190 - 2026-01-05
- Add a "Back to Admin Dashboard" navigation button to the admin LogViewer.

### PR #184 - 2025-12-19
- Add configurable log levels for controlling sensitive data logging. Set `LOG_LEVEL=INFO` in production to prevent logging user input/output content, or `LOG_LEVEL=DEBUG` for development/testing with verbose logging.
- Fix logging in error_utils.py to prevent full LLM response objects from being logged at INFO level.
- Redact tool approval response logging so tool arguments are never logged at INFO.
- Remove unused local variables in test_log_level_sensitive_data.py (code quality improvement).

### PR #180 - 2025-12-17
- Add MCP Server Management admin panel and update Admin Dashboard panel layout.

### PR #181 - 2025-12-17
- Add unsaved changes confirmation dialog to tools panel

### PR 177 Security Fixes - 2025-12-13
- **SECURITY FIX**: Fixed MD5 hash usage in S3 client by adding `usedforsecurity=False` parameter to address cryptographic security warnings while maintaining S3 ETag compatibility
- **SECURITY FIX**: Enhanced network binding security by making host binding configurable via `ATLAS_HOST` environment variable, defaulting to localhost (127.0.0.1) for secure development while allowing 0.0.0.0 for production deployments
- Updated Docker configuration to properly handle new host binding environment variable
### PR #176 - 2025-12-15
- Add Quay.io container registry CI/CD workflow for automated container publishing from main and develop branches
- Update README and Getting Started guide with Quay.io pre-built image information

### PR #173 - 2025-12-13
- Increase unit test coverage across backend and frontend; add useSettings localStorage error-handling tests and harden the hook against localStorage failures.
### PR #169 - 2025-12-11
- Implement MCP server logging infrastructure with FastMCP log_handler
- Add log level filtering based on environment LOG_LEVEL configuration
- Forward MCP server logs to chat UI via intermediate_update websocket messages
- Add visual indicators (badges and colors) for different log levels (debug, info, warning, error, alert)
- Create comprehensive test suite for MCP logging functionality
- Add demo MCP server (logging_demo) for testing log output

### PR #172 - 2025-12-13
- Resolve all frontend ESLint errors and warnings; update ESLint config and tests for consistency.
### PR #170 - 2025-12-12
- Improve LogViewer performance by memoizing expensive computations with `useMemo`


### PR  163 - 2024-12-09
- **SECURITY FIX**: Fixed edit args security bug that allowed bypassing username override security through approval argument editing
- Added username-override-demo MCP server to demonstrate the username security feature
- Server includes tools showing how Atlas UI prevents LLM user impersonation
- Added comprehensive documentation and example configuration

### PR #158 - 2025-12-10
- Add explicit "Save Changes" and "Cancel" buttons to Tools & Integration Panel (ToolsPanel)
- Add explicit "Save Changes" and "Cancel" buttons to Data Sources Panel (RagPanel)
- Implement pending state pattern to track unsaved changes
- Save button is disabled when no changes are made, enabled when changes are pending
- Changes only persist to localStorage when user clicks "Save Changes"
- Cancel button reverts all pending changes and closes panel
- Updated tests to verify save/cancel functionality


### PR #156 - 2024-12-07
- Add CHANGELOG.md to track changes across PRs
- Update agent instructions to require changelog entries for each PR

## Recent Changes

### PR #157 - 2024-12-07
- Enhanced ToolsPanel UI with improved visual separation between tools and prompts
- Added section headers with icons for tools and prompts
- Updated color scheme to use consistent green styling for both tools and prompts
- Added horizontal divider between tools and prompts sections
- Increased font size and weight for section headers
- Improved vertical spacing between UI sections

### PR #155 - 2024-12-06
- Add automated documentation bundling for CI/CD artifacts
