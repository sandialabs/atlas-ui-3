# Changelog

All notable changes to Atlas UI 3 will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### PR #TBD - 2025-12-13
- Add comprehensive OAuth 2.1 / Bearer token authentication e2e tests for MCP HTTP servers
- Add Python test suite (oauth_e2e_test.py) validating authentication flow, token resolution, and error handling
- Add Playwright test suite (oauth-authentication.spec.js) for browser-based OAuth workflow testing
- Update e2e test runner to include OAuth 2.1 tests in the standard test suite
- Add detailed documentation for OAuth 2.1 e2e tests (test/OAUTH_E2E_TESTS.md)

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
