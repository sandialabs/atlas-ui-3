# Chat History Persistence

Last updated: 2026-02-15

## Overview

Atlas can persist conversation history to a database, allowing users to browse, search, reload, and manage past conversations. This feature supports both DuckDB (lightweight, local file) and PostgreSQL (production, multi-user).

The feature is **disabled by default** and controlled by a feature flag.

## Configuration

### Enable the Feature

Set these in your `.env` file:

```bash
FEATURE_CHAT_HISTORY_ENABLED=true
```

### Choose a Database Backend

#### Option A: DuckDB (Default - No Docker Required)

Best for single-user development. Stores conversations in a local file.

```bash
CHAT_HISTORY_DB_URL=duckdb:///data/chat_history.db
```

The path is relative to the project root. The `data/` directory is created automatically.

#### Option B: PostgreSQL (Production / Multi-User)

Best for production deployments with multiple users.

```bash
CHAT_HISTORY_DB_URL=postgresql://atlas:atlas@localhost:5432/atlas_chat_history
```

Start the PostgreSQL container:

```bash
docker compose up -d postgres
```

The `docker-compose.yml` includes a pre-configured PostgreSQL service with:
- User: `atlas`
- Password: `atlas`
- Database: `atlas_chat_history`
- Port: `5432`

For production, change the credentials and use a persistent volume.

### Startup Behavior

When using `agent_start.sh`, the script automatically:
- Detects if chat history is enabled from `.env`
- If PostgreSQL URL is configured, starts the container if not running
- If DuckDB URL is configured, ensures the `data/` directory exists
- Tables are created automatically on first startup (via `Base.metadata.create_all`)

## Database Schema

Four tables are created:

| Table | Purpose |
|-------|---------|
| `conversations` | Conversation metadata (title, model, timestamps, message count) |
| `conversation_messages` | Individual messages with role, content, sequence order |
| `tags` | User-defined tags for organizing conversations |
| `conversation_tags` | Many-to-many junction between conversations and tags |

**DuckDB compatibility note**: No database-level foreign key constraints are used because DuckDB does not support CASCADE or UPDATE on FK-constrained tables. Referential integrity is enforced in the application layer.

## Alembic Migrations

For production PostgreSQL deployments, use Alembic for schema migrations:

```bash
# Apply migrations
alembic upgrade head

# Check current revision
alembic current

# Create a new migration (PostgreSQL only; DuckDB does not support autogenerate)
alembic revision --autogenerate -m "description"
```

The `alembic.ini` is at the project root. Set `CHAT_HISTORY_DB_URL` in your environment before running Alembic commands.

For development with DuckDB, `init_database()` handles table creation automatically -- Alembic is not required.

## REST API

All endpoints are at `/api/conversations` and scoped to the authenticated user.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/conversations` | List conversations (supports `limit`, `offset`, `tag` params) |
| GET | `/api/conversations/search?q=...` | Search by title or message content |
| GET | `/api/conversations/{id}` | Get full conversation with messages |
| DELETE | `/api/conversations/{id}` | Delete a single conversation |
| POST | `/api/conversations/delete` | Delete multiple (body: `{"ids": [...]}`) |
| DELETE | `/api/conversations` | Delete all conversations |
| POST | `/api/conversations/{id}/tags` | Add a tag (body: `{"name": "..."}`) |
| DELETE | `/api/conversations/{id}/tags/{tag_id}` | Remove a tag |
| PATCH | `/api/conversations/{id}/title` | Update title (body: `{"title": "..."}`) |
| GET | `/api/conversations/tags/list` | List all tags with counts |

When the feature is disabled, all endpoints return empty results gracefully.

## Incognito Mode

Users can toggle incognito mode in the sidebar. When enabled:
- Conversations are **not saved** to the database
- A clear visual indicator (red background) appears in the sidebar
- The `incognito` flag is sent via WebSocket to the backend
- The backend tracks incognito session IDs and skips persistence

This is designed for national lab environments where users may need to discuss sensitive topics without creating a record.

## Frontend

The sidebar shows:
- **Conversation list** with title, preview, timestamp, message count, and tags
- **Search bar** for filtering by title or content
- **Tag filter** buttons for quick filtering
- **Multi-select** checkboxes for bulk deletion
- **Incognito toggle** with clear visual indicator
- **Delete All** button in the footer

Loading a saved conversation replaces the current chat view with the saved messages.

## User Isolation

All database queries are scoped by `user_email`. Users cannot see, search, modify, or delete conversations belonging to other users.
