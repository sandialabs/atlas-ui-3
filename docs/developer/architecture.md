# Architecture Overview

Last updated: 2026-10-04

The application is composed of a React frontend and a FastAPI backend, communicating via WebSockets.

## Backend

The backend follows a clean architecture pattern, separating concerns into distinct layers:

*   **`domain`**: Contains the core business logic and data models, with no dependencies on frameworks or external services.
*   **`application`**: Orchestrates the business logic from the domain layer to perform application-specific use cases.
*   **`infrastructure`**: Handles communication with external systems like databases, web APIs, and the file system. It's where adapters for external services are implemented.
*   **`interfaces`**: Defines the contracts (protocols) that the different layers use to communicate, promoting loose coupling.
*   **`routes`**: Defines the HTTP API endpoints.

### Chat mode entry points

The chat orchestrator dispatches plain, RAG, tools, and agent turns through each
mode runner's `run_streaming` entry point. Buffered CLI output uses the same turn
pipeline; it does not require a second non-streaming mode implementation.
Non-streaming LLM calls remain available as fallbacks inside the streaming
helpers and for active synthesis paths.

Regression coverage for tools mode belongs on `ToolsModeRunner.run_streaming`.
The CLI smoke test at
`test/pr-validation/test_pr1018_dead_code_cleanup.sh` exercises plain streaming,
buffered output, text completion in tools mode, and provider-error propagation
against the bundled local LLM mock.

The dead-code cleanup is the first stage of issue #1012. Service ownership,
WebSocket decomposition, typed turn requests, loop/RAG consolidation, and context
splitting remain separate follow-up work; the four chat modes and token-flush
timing are unchanged.

## Frontend

The frontend is a modern React 19 application built with Vite.

*   **State Management**: Uses React's Context API for managing global state. There is no Redux.
    *   `ChatContext`: Manages the state of the chat, including messages and user selections. Validates persisted selections (tools, prompts, data sources) against the live `/api/config` response and removes stale entries automatically.
    *   `WSContext`: Manages the WebSocket connection.
    *   `MarketplaceContext`: Manages MCP server discovery and marketplace selections. Prunes servers that no longer exist in the backend config.
*   **Styling**: Uses Tailwind CSS for utility-first styling.

Admin configuration actions live in `useAdminConfigActions` and the active
`components/admin/` cards. `AdminDashboard` hosts those cards rather than
maintaining duplicate fetch handlers. The file-manager entry point is
`FileManagerPanel`, which uses `SessionFilesView`.

Install E2E dependencies from `test_e2e/package-lock.json` with `npm ci` in that
directory. Dependency directories and generated Playwright reports are ignored,
not checked in.
