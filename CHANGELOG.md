# Changelog

All notable changes to Atlas UI 3 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### PR #278 - 2026-01-30
- Replace boolean file extraction toggle with 3-mode system (`full` | `preview` | `none`) for fine-grained control over how file content is injected into LLM prompts.
- Add backward-compatible normalization of legacy config values (`"extract"` -> `"full"`, `"attach_only"` -> `"none"`).

### PR #274 - 2026-01-30
- **Feature**: Add multipart form-data upload support for file content extraction. Extractors can now use `request_format: "multipart"` to send files via multipart upload instead of base64 JSON, enabling compatibility with standard file upload APIs.
- **Config**: Add `form_field_name` field to extractor config for controlling the multipart form field name (default: `"file"`).

### PR #264 - 2026-01-28
- **Feature**: Add metrics logging for user activity tracking without capturing sensitive data. Logs LLM calls, tool usage, file uploads, and errors with only metadata (counts, sizes, types).
- **Feature**: Add `FEATURE_METRICS_LOGGING_ENABLED` environment variable to enable/disable metrics logging.
- **Privacy**: Metrics explicitly exclude prompts, tool arguments, file names, and error details - only non-sensitive metadata is logged.
- **Format**: All metrics use consistent `[METRIC] [username] event_type key=value ...` pattern for easy filtering and analysis.
- **Documentation**: Add comprehensive metrics logging documentation in `docs/metrics-logging.md` with examples and query patterns.

### PR #TBD - 2026-01-27
- **Feature**: Add non-interactive CLI (`atlas_chat_cli.py`) and Python API (`atlas_client.py`) for one-shot LLM chat with full MCP tools, RAG, and agent mode support. Enables scripted workflows, E2E testing, and MCP development without the browser UI.
- **Feature**: Add CLI event publisher for headless operation with streaming and collecting modes.
- **Architecture**: Add `initialize()` async method and `create_headless_chat_service()` to `AppFactory` for use outside FastAPI context.

### PR #TBD - 2026-01-26
- **Fix**: Add `:U` suffix to bind mounts in docker-compose.yml to fix permissions issues on some platforms where logs and config directories were owned by root instead of appuser.

### PR #250 - 2026-01-24
- **Feature**: Add support for displaying images returned by MCP tools via ImageContent. When MCP tools return ImageContent objects with base64-encoded images, Atlas now automatically extracts and displays them in the canvas panel.
- **Enhancement**: Images are automatically opened in the canvas panel for easy viewing, supporting PNG, JPEG, GIF, and other image formats.
- **Security**: Validate ImageContent base64 data and mime types against an allowlist of safe image types.
- **Testing**: Add comprehensive unit tests for ImageContent extraction, including single images, multiple images, mixed content, and edge cases.
- **Example**: Add image_demo MCP server demonstrating how to return images from tools.
- **Fix**: Correctly filter tool arguments when schema has empty parameters. Previously, tools with no parameters would incorrectly keep extra arguments instead of filtering them out.

### PR #253 - 2026-01-25
- **Feature**: Add per-user MCP API key, JWT, and bearer token authentication flow. Users can now authenticate with MCP servers that require API keys or tokens through the UI.
- **Feature**: Secure token storage with Fernet encryption. Tokens are encrypted at rest and isolated per-user.
- **Feature**: Add MCP Server Manager search filter on admin page for quickly finding servers by name, description, or author.
- **UI Enhancement**: Token input uses password field with show/hide toggle for security.
- **Fix**: Admin page "reload and reconnect" button now refreshes tools list without requiring F5.
- **Security**: Replace generic 500 error details with safe messages to prevent internal info leakage.

### PR #TBD - 2026-01-23
- **Fix**: Display configured app name instead of hardcoded "Chat UI" in the thinking spinner. Fixes #244.

### PR #245 - 2026-01-23
- **Fix**: Preserve line breaks in user messages by adding `whitespace-pre-wrap` CSS class. Previously, multi-line user input displayed as a wall of text without line breaks.

### PR #243 - 2026-01-23
- **UI Enhancement**: Implement responsive header with mobile hamburger menu for improved usability on small screens and mobile devices. Header controls collapse into a slide-out menu on screens smaller than 1024px, and button text labels are hidden on mobile while maintaining icon visibility.

### PR #237 - 2026-01-22
- **Fix**: Add exponential backoff to admin dashboard MCP status polling to prevent toast notification spam when backend is disconnected. Polling backs off from 1s to 30s max delay between retries, then continues polling at 30s intervals until the backend recovers.

### PR #TBD - 2026-01-23
- **Feature**: Add unified RAG configuration via `rag-sources.json`. Multiple RAG backends (HTTP and MCP) can now be configured in a single file.
- **Feature**: Add ATLAS RAG API integration with `AtlasRAGClient` supporting Bearer token auth with `as_user` impersonation.
- **Feature**: Add RAG feature toggle (`FEATURE_RAG_ENABLED`) and `/search` autocomplete command in chat UI.
- **Architecture**: Add `UnifiedRAGService` in `backend/domain/unified_rag_service.py` for aggregating RAG discovery and queries across multiple backends.
- **Architecture**: Add `RAGSourceConfig` and `RAGSourcesConfig` Pydantic models for type-safe configuration.
- **Architecture**: `LiteLLMCaller` now uses `UnifiedRAGService` for all RAG queries instead of a separate RAG client injection.
- **UI**: Integrate RAG feature toggle and search command handling in ChatArea and RagPanel components.
- **Fix**: Fix 404 error when querying ATLAS RAG API - server prefix is now properly stripped before calling the RAG API.
- **Config**: Support environment variable substitution (`${ENV_VAR}`) in `rag-sources.json` for secrets like bearer tokens.
- **Testing**: Add `mocks/atlas-rag-api-mock/` mock service with grep-based search for testing RAG integration.
- **Docs**: Update RAG documentation to reflect the new unified configuration approach.

### PR #234 - 2026-01-20
- **UI Enhancement**: Renamed "Chat UI Admin Dashboard" to "ATLAS Admin Dashboard" for consistency with branding.
- **UI Fix**: Moved toast notifications from top-right to top-center to prevent covering the "Back to Chat" button.

### PR #T231 - 2026-01-20
- **Fix**: Merged duplicate GEMINI.md and gemini.md files into a single GEMINI.md file to resolve case-insensitive filesystem conflicts on macOS.

### PR #225 - 2026-01-19
- **Feature**: Implement automatic file content extraction for uploaded PDFs and images. When enabled, files are processed by configurable HTTP extractor services and their content is included in the LLM context.
- **Feature**: Add mock file extractor service (`mocks/file-extractor-mock/`) supporting PDF text extraction, image analysis, and OCR endpoints for development and testing.
- **Feature**: Add API key and custom headers support to file extractor configuration for authenticating with external extraction services.
- **Feature**: Support `${ENV_VAR}` syntax in file extractor configuration for `api_key`, `headers`, and `url` fields, matching the pattern used by LLM and MCP configs.
- **Feature**: Add per-file extraction toggle in the UI, allowing users to control which files are extracted.
- **Config**: Add `file-extractors.json` configuration with extension-to-extractor mapping and service definitions.
- **Tests**: Add comprehensive tests for file extraction routes, content extractor, and API key/headers functionality.

### PR #215 - 2026-01-18
- **Fix**: Restored MCP sampling implementation, re-adding per-server sampling handlers and routing context so sampling tests can import `_SamplingRoutingContext` again.
- **Fix**: Re-enabled backend sampling workflows, ensuring the restored sampling handler uses LiteLLM preferences and the MCP client initializes with sampling support.
### PR #217 - 2026-01-15
- **Feature**: Add info icon (i) to prompts in the Tools & Integrations panel, matching the existing tool info icon behavior. Users can now click the icon to view prompt descriptions instead of relying on hover tooltips.
- **UX Enhancement**: Long prompt descriptions (>500 characters) are automatically truncated, showing the first 200 and last 200 characters with "..." in between, making very long prompts (100s of pages) more manageable.
- **UI Consistency**: Prompts now have the same expandable description UI as tools, improving discoverability and user experience.
- **Tests**: Add 6 comprehensive unit tests for prompt info icon functionality including expansion, truncation, and edge cases.
- **Demo/Test Data**: Add a super-long prompt description to the prompts MCP server to validate truncation behavior in the UI.

### PR #195 - 2026-01-13
- **Fix**: Fix file upload registration issue where files attached in one WebSocket connection were not visible in subsequent chat messages. The issue was caused by each ChatService instance creating its own session repository, preventing session sharing across connections.
- **Architecture**: Created a shared InMemorySessionRepository in AppFactory that is passed to all ChatService instances, ensuring sessions and attached files are properly shared across WebSocket connections.

### PR #211 - 2026-01-11
- **Feature**: Add drag and drop file attachment support to the chat area. Users can now drag files directly onto the chat interface to attach them to messages.
- **UI**: Visual overlay with dashed border appears when dragging files over the chat area, providing clear feedback.
- **Tests**: Add comprehensive frontend tests for drag and drop functionality (8 tests).

### PR #210 - 2026-01-12
- **Fix**: Treat approval-only elicitation (`response_type=None`) as expecting an empty response object on accept, preventing `approve_deletion` from failing when the UI returns placeholder data.
- **Tests**: Add backend regression coverage for approval-only elicitation accept payload normalization.

### PR #192 - 2026-01-10
- **File Access**: Add `BACKEND_PUBLIC_URL` configuration so remote MCP servers (HTTP/SSE) can download attached files via absolute URLs.
- **File Access**: Add optional `INCLUDE_FILE_CONTENT_BASE64` fallback to include base64 file content in tool arguments (disabled by default).
- **Docs**: Add troubleshooting and developer documentation for remote MCP file access configuration.
- **Tests**: Add coverage for absolute/relative download URL generation.
### PR #206 - 2026-01-11
- **Tools & Integrations Panel**: Display custom MCP server metadata (author, short_description, help_email) in the Tools & Integrations panel. Previously these fields from mcp.json were returned by the backend but not displayed in the UI.
- **UI Enhancement**: Add expandable description with "Show more details..." / "Show less" toggle to keep the UI compact while making full descriptions available on demand.
- **Tests**: Add 8 comprehensive unit tests for custom information display and description expansion functionality.
### PR #207 - 2026-01-11
- **Fix**: Keep loaded custom prompts available when switching back to the default prompt by separating loaded prompts from the active prompt selection.
- **Tests**: Add frontend regression coverage for prompt persistence when clearing the active prompt.

### PR #203 - 2026-01-10
- **Admin Panel**: Add User Feedback viewer card to admin dashboard with statistics display (positive/neutral/negative counts)
- **Admin Panel**: Add feedback download functionality supporting CSV and JSON export formats
- **Backend**: Add `/api/feedback/download` endpoint for exporting feedback data

### PR #201 - 2026-01-10
- **Fix**: Include feedback_router in main.py to fix 404 on /api/feedback endpoint. The feedback routes were defined but never registered with the FastAPI app.
- **Tests**: Add comprehensive test suite for feedback routes (13 tests) to prevent regression. Tests cover route registration, feedback submission, admin-only access controls, and deletion.

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
