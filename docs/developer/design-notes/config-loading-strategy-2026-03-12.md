# Config Loading Strategy

Last updated: 2026-03-12

## Problem

On startup (F5 refresh), there is a noticeable lag between the page rendering and the UI reflecting the user's saved customizations. The app briefly shows default/unconfigured state before snapping to the user's actual settings. This is most noticeable in production environments with many MCP tool servers, where `/api/config` can take 1-5+ seconds due to MCP tool/prompt discovery and RAG source discovery.

## Solution

Two complementary strategies eliminate the flash:

### 1. localStorage Config Cache (Instant Hydration)

The last successful `/api/config` response is cached in `localStorage` under the key `chatui-config-cache`. On page load, `useChatConfig` immediately hydrates all state from this cache before any network requests complete.

**Flow:**
1. Component mounts -> reads cache -> applies cached config -> `configReady=true`
2. Network responses arrive -> reconcile (fresh data always wins)
3. After successful full config fetch -> update cache

This eliminates the flash for the common case where config hasn't changed between page loads.

### 2. Split Config Endpoint (`/api/config/shell`)

A new fast endpoint `/api/config/shell` returns only UI-affecting metadata:

- `app_name`, `user`, `models`, `features`, `agent_mode_available`, `is_in_admin_group`, `banner_enabled`, `file_extraction`

It skips the slow operations:
- MCP tool/prompt discovery (can take 1-30+ seconds per server)
- RAG source discovery (HTTP + MCP)
- Per-user token validity checks

The frontend fetches shell and full config in parallel. The shell response arrives first and updates the UI immediately, while tools/prompts load in the background.

## Three-Phase Startup

1. **Instant** (0ms): Hydrate from localStorage cache
2. **Fast** (~50-200ms): `/api/config/shell` response updates features and models
3. **Complete** (~200-5000ms): Full `/api/config` response updates tools, prompts, RAG sources

## Key Files

- `frontend/src/hooks/chat/useChatConfig.js` - Three-phase loading with cache
- `atlas/routes/config_routes.py` - `/api/config/shell` endpoint
- `atlas/tests/test_config_shell_endpoint.py` - Backend tests
- `frontend/src/test/config-cache-hydration.test.js` - Frontend tests

## API Reference

### GET `/api/config/shell`

Returns fast UI shell data. Same auth as `/api/config`.

**Response fields:**
- `app_name` (string)
- `models` (array) - model names and descriptions (no `user_has_key` field)
- `user` (string)
- `features` (object) - all feature flags
- `agent_mode_available` (boolean)
- `is_in_admin_group` (boolean)
- `banner_enabled` (boolean)
- `file_extraction` (object)

**Excluded fields** (available only in full `/api/config`):
- `tools`, `prompts`, `data_sources`, `rag_servers`, `authorized_servers`, `tool_approvals`, `help_config`
