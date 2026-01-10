# Admin Panel

The application includes an admin panel that provides access to configuration values, MCP server controls, application logs, and user feedback.

*   **Access**: To access the admin panel, a user must be in the `admin` group. This requires a correctly configured `is_user_in_group` function.
*   **Icon**: Admin users will see a shield icon on the main page, which leads to the admin panel.
*   **Features**:
    *   View the current application configuration.
    *   View the application logs (`app.jsonl`).
    *   Inspect and manage MCP server connections (reload config, reconnect failed servers, view status).
    *   View and download user feedback data.

## MCP Admin Endpoints

The backend exposes dedicated admin-only endpoints for managing MCP servers. These are typically surfaced in the admin UI, but you can also call them directly (for example, using `curl`) when needed.

### `POST /admin/mcp/reload`

Reloads MCP server configuration from disk and reinitializes all connections.

- Reloads `mcp.json` from `config/overrides/` (with defaults as fallback).
- Reinitializes all MCP clients.
- Rediscovers tools and prompts from every configured server.
- Returns a summary of configuration changes and which servers loaded successfully.

Use this after editing `config/overrides/mcp.json` to apply changes **without restarting** the application.

### `POST /admin/mcp/reconnect`

Manually triggers reconnection attempts for MCP servers that previously failed to connect.

- Only targets servers tracked as failed.
- Respects the same exponential backoff logic used by the background auto-reconnect task.
- Returns which servers were attempted, which reconnected, and which are still failing.

This is useful when you know a previously failing MCP server has been fixed and want to nudge reconnection from the admin panel.

### `GET /admin/mcp/status`

Returns the current MCP connection status and auto-reconnect settings.

The response includes:

- `connected_servers`: list of servers with active clients.
- `configured_servers`: all servers defined in `mcp.json`.
- `failed_servers`: per-server error details and timing information (`attempt_count`, `error`, `last_attempt`, `backoff_delay`, `next_retry_in_seconds`).
- `auto_reconnect`: configuration and runtime state for the background reconnect task (`enabled`, `base_interval`, `max_interval`, `backoff_multiplier`, `running`).

You can use this endpoint to quickly see which MCP servers are healthy, which are failing, and when the next reconnect attempt will occur.

## Feedback Admin Endpoints

The admin panel includes a User Feedback card that displays feedback statistics and allows downloading feedback data.

### `GET /api/feedback`

Returns paginated feedback data with statistics (admin only).

Query parameters:
- `limit`: Maximum number of feedback entries to return (default: 50)
- `offset`: Pagination offset (default: 0)

Returns feedback entries, pagination info, and rating statistics (positive/neutral/negative counts).

### `GET /api/feedback/stats`

Returns feedback statistics summary including total count, rating distribution, recent feedback count (last 24 hours), and unique user count.

### `GET /api/feedback/download`

Downloads all feedback data as a file (admin only).

Query parameters:
- `format`: Export format, either `csv` or `json` (default: `csv`)

The CSV export includes columns: id, timestamp, user, rating, comment.
The JSON export includes all feedback data including session info and server context.

### `DELETE /api/feedback/{feedback_id}`

Deletes a specific feedback entry by ID (admin only).
