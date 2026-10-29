# Chat History Persistence

Last updated: 2026-10-27

## Overview

Atlas can persist conversation history to a database, allowing users to browse, search, reload, and manage past conversations. This feature supports both DuckDB (lightweight, local file) and PostgreSQL (production, multi-user).

The feature is **enabled by default** with DuckDB storage and controlled by a feature flag.

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
CHAT_HISTORY_DB_URL=postgresql://atlas:<POSTGRES_PASSWORD>@localhost:5432/atlas_chat_history
```

Start the PostgreSQL container. It lives in its own self-contained Compose
file (`docker-compose.postgres.yml`), so DuckDB deployments never need its
credential, and this command starts only PostgreSQL — not the containerized
application:

```bash
docker compose -f docker-compose.postgres.yml up -d postgres
```

To run the whole Compose stack with bundled PostgreSQL, include both files
(`docker compose -f docker-compose.yml -f docker-compose.postgres.yml up -d`).

The bundled PostgreSQL service has:
- User: `atlas`
- Password: required through `POSTGRES_PASSWORD` (no shipped password)
- Database: `atlas_chat_history`
- Port: `127.0.0.1:5432` (host-local only)

When that file is loaded, the stack also builds the container's
`CHAT_HISTORY_DB_URL` from `POSTGRES_PASSWORD` automatically, so the
credential is configured in one place. `agent_start.sh` starts the bundled
container automatically when `CHAT_HISTORY_DB_URL` is a `postgresql://` URL
pointing at the local machine; URLs for externally managed databases are left
alone. Supply all required Compose secrets as described in the
[installation guide](../getting-started/installation.md#option-4-docker-compose).
For production, provision a separately secured database with backups and
least-privilege credentials. Compose also persists DuckDB history in the
`atlas-data` named volume, which the container's nonroot user owns.

Atlas connects to PostgreSQL with the psycopg (v3) driver on every install path: a bare `postgresql://` URL (and the default `DB_DRIVER=postgresql`) is normalized to `postgresql+psycopg://` before the app or Alembic creates a SQLAlchemy engine, so the driver does not depend on the installed SQLAlchemy version. Install paths that previously resolved a bare `postgresql://` URL to psycopg2 — `uv sync` from a lockfile pinned to SQLAlchemy 2.0 — now use psycopg 3. URLs that already name a driver — `postgresql+psycopg2://`, `postgresql+asyncpg://`, and so on — are honored as written; both `psycopg` and `psycopg2` packages ship so explicit `postgresql+psycopg2://` URLs keep working.

#### PostgreSQL connection pool

Each Atlas process (every replica, and every uvicorn worker inside it) keeps
its own SQLAlchemy connection pool. A deployment can therefore hold up to

```text
replicas x workers per replica x (CHAT_HISTORY_DB_POOL_SIZE + CHAT_HISTORY_DB_MAX_OVERFLOW)
```

connections at once. Keep that total below the server's `max_connections`,
leaving room for migrations, admin sessions and any other clients. The
defaults (5 + 10 = 15 per process) suit a single instance; 4 replicas with 2
workers each would already need 120. Startup logs the per-process bound, for
example `Chat history PostgreSQL pool: pool_size=5 max_overflow=10 (up to 15
connections per process)`.

| Variable | Default | Meaning |
|----------|---------|---------|
| `CHAT_HISTORY_DB_POOL_SIZE` | `5` | Connections each process keeps open (at least 1). |
| `CHAT_HISTORY_DB_MAX_OVERFLOW` | `10` | Extra connections a process may open beyond the pool under load (`0` for a hard cap at the pool size). |
| `CHAT_HISTORY_DB_POOL_TIMEOUT` | `30` | Seconds a request waits for a free pooled connection before failing with an error. |
| `CHAT_HISTORY_DB_POOL_RECYCLE` | `1800` | Seconds after which a pooled connection is replaced (`-1` disables; `0` is rejected, since it would reconnect on every checkout). Keep it below any proxy, firewall or load-balancer idle timeout. |
| `CHAT_HISTORY_DB_POOL_PRE_PING` | `true` | Check each connection is alive before handing it out, so a database restart does not surface as failed requests. |
| `CHAT_HISTORY_DB_CONNECT_TIMEOUT` | `10` | Seconds to wait when opening a connection, so an unreachable database fails fast instead of hanging. A `connect_timeout` in the URL query wins. |
| `CHAT_HISTORY_DB_DISABLE_POOLING` | `false` | Open a fresh connection per use (SQLAlchemy `NullPool`). Use it when an external pooler such as PgBouncer does the pooling. |

Out-of-range values (for example a pool size of `0`) fail at startup. These
settings apply to PostgreSQL only; DuckDB ignores them. `connect_timeout` is
sent only to the libpq-based drivers (`psycopg`, `psycopg2`); other drivers
keep their own connect behavior.

`CHAT_HISTORY_DB_DISABLE_POOLING=true` ignores the pool size, overflow,
timeout and recycle settings; only pre-ping and the connect timeout still
apply. The chat-history engine also passes `prepare_threshold=None` to the
psycopg (v3) driver in that mode, so statements are never named server-side:
auto-prepared statements are what break transaction-mode PgBouncer before
1.21 (a `prepare_threshold` in the URL query wins over the default). psycopg2
has no such setting and is unaffected. Alembic migrations build their own
engine from the URL without these pool settings, so run them against the
database directly rather than through a transaction pooler.

Some chat-history routes call the database directly on the event loop, so a
request waiting for a pooled connection stalls every request on that worker
until a connection frees up or `CHAT_HISTORY_DB_POOL_TIMEOUT` expires. When you
shrink the pool, also shorten the timeout (a few seconds) so exhaustion
surfaces as errors in the logs, not as a hung worker.

With the Compose stack, `docker-compose.postgres.yml` passes these variables
from your shell or `.env` into the container. A custom override file that
points at a different database must pass them through itself, because the
base `atlas-ui` service lists its environment explicitly.

For many replicas, an external pooler keeps the server-side connection count
flat as replicas scale. With PgBouncer in `transaction` mode, either keep a
small Atlas pool (bounded by PgBouncer's `default_pool_size`) or set
`CHAT_HISTORY_DB_DISABLE_POOLING=true` and let PgBouncer pool. With pooling
disabled, the chat-history engine also keeps psycopg 3 from naming prepared
statements, so `transaction` mode works on any PgBouncer release. When the
Atlas pool stays enabled, the psycopg driver can still auto-prepare
statements: use PgBouncer 1.21 or later (`max_prepared_statements` set) or
`session` mode.

Run schema migrations once per deployment, before any replica starts (for
example a Kubernetes Job or init step that runs `alembic upgrade head`), not
from every replica. Atlas also runs `create_all` at startup, which creates only
missing tables, so after the migration it changes nothing. The reverse order
breaks: on a database whose tables were first created by an Atlas startup,
`alembic upgrade head` fails with `DuplicateTable`; run `alembic stamp head`
once on such a database to record its schema as current.
DuckDB remains the default for local development; multi-replica deployments
need PostgreSQL, since DuckDB is a single-process file database.

### Startup Behavior

When using `agent_start.sh`, the script automatically:
- Detects if chat history is enabled from `.env`
- If PostgreSQL URL is configured, starts the container if not running
- If DuckDB URL is configured, ensures the `data/` directory exists
- Tables are created automatically on first startup (via `Base.metadata.create_all`)

### Failed initialization (issue #992)

Chat history is optional: if it is enabled but cannot start (missing driver,
unreachable database, bad credentials), the server still comes up — it just
carries no history. The failure is no longer silent:

- The error is logged at ERROR, including a prominent
  `Chat history is ENABLED but UNAVAILABLE …` line.
- `GET /api/health` reports `"status": "degraded"` with
  `"checks": {"chat_history": "failed"}`. It still returns HTTP 200 so
  liveness probes do not restart replicas over a database outage; see
  [Monitoring](logging-monitoring.md). The error detail stays in the server
  logs, never in the unauthenticated response.
- The conversation, workspace, and custom-prompt APIs report
  `"Chat history failed to initialize"` instead of the misleading
  `"Chat history is not enabled"` (the latter still appears when the feature
  flag is genuinely off).
- Nothing is saved or served: the repositories stay unset, so chat turns run
  without persistence until the database is fixed and the server restarted.

### Secondary Indexes on DuckDB

The DuckDB backend runs without the secondary indexes declared in `models.py`;
they are dropped during startup. DuckDB's ART indexes can silently stop
matching rows that are present in the file, so an equality lookup such as
`WHERE user_email = ?` returns nothing while a full scan of the same table
returns the row. The failure is durable — it survives reopening the database —
and it is invisible to the application, which renders the empty result as
"no data". Symptoms are conversations that open with no messages and custom
prompts missing from the prompt list, while the conversation list still looks
complete.

Dropping the indexes rather than rebuilding them is deliberate: a rebuild
repairs the rows that exist today but leaves the same failure mode armed for
tomorrow. Local datasets are small enough that a full scan is cheap.

This applies to DuckDB only. PostgreSQL keeps its indexes, which are correct
there and matter at production scale. The behavior is selected by the dialect
of the configured URL, so any deployment left on the default DuckDB backend
(no `CHAT_HISTORY_DB_URL` set) runs without these indexes — one more reason to
point production at PostgreSQL.

A database that predates this behavior repairs itself on the next startup. To
repair a file out of band, stop the application (DuckDB holds an exclusive
lock) and run:

```bash
python scripts/repair_duckdb_indexes.py data/chat_history.db
```

Pass `--check` to inspect without modifying, or `--rebuild` to recreate the
indexes instead of leaving them off. `--rebuild` restores correct lookups for
the rows that exist today but re-arms the same failure mode, and the
application drops the rebuilt indexes again on its next startup — it is a
diagnostic option, not a repair. The script copies the database (to
`<name>.prerepair-<timestamp>.db`, alongside the original and covered by the
same `.gitignore` rules) once it has the file open, so a run that fails on
DuckDB's exclusive lock leaves no copy behind. No row data is lost in either
case — only the indexes are wrong.

### Saving does not depend on the browser

A conversation is saved from the server's own copy of the history, and the
server reloads that copy when it does not have one. This matters because a
WebSocket gets a fresh session for every connection, while the browser keeps
the conversation it is displaying across a reconnect: after a dropped
connection the next message names a conversation the server has no history for.

Two behaviors cover that:

- **Rehydration.** When a turn names a conversation the session is not already
  carrying, the server loads it from the database before running the turn. The
  model sees the earlier messages and the save that follows is complete, with
  no reload in the browser and no action from the user. Look for
  `Rehydrated conversation … the client did not restore it` in the log.
- **A write may not shrink a conversation.** Each save replaces the
  conversation's whole message set, so a caller holding a partial history would
  destroy the rest. A save carrying fewer messages than are stored is refused,
  logged at ERROR, and reported to the client as a failed save rather than
  applied. Rewind / edit-and-resubmit is the one turn allowed to shorten a
  conversation, because dropping a prompt and everything after it is its
  purpose. That exemption is earned rather than requested: it follows a
  truncation that actually removed messages, not the presence of the
  `rewind_to_user_index` field, so an out-of-range or malformed index leaves
  the guard armed. A rehydration that could not read the store revokes it for
  that turn as well -- a truncation measured against a partial session says
  nothing about what is stored.

Incognito turns are never rehydrated: they are not persisted, so there is no
stored record for them to continue. Turning saving back on part-way through a
conversation that was opened from the sidebar **branches** it: the messages
taken while incognito can never be written, so the segment after them is not a
continuation of the stored conversation. It is saved as a new conversation
instead of replacing the original, and the client adopts the new id from the
`conversation_saved` frame. Look for `the savable segment is stored as new
conversation …` in the log.

A rehydration that failed on an unreadable store is retried on the next turn
rather than leaving the session bound to a conversation it has no history for.

The two work together. If the database cannot be read, rehydration is skipped
and the turn still runs — with no earlier context — and the no-shrink guard is
what stops that turn from overwriting the stored conversation.

## Database Schema

Four tables are created:

| Table | Purpose |
|-------|---------|
| `conversations` | Conversation metadata (title, model, timestamps, message count) |
| `conversation_messages` | Individual messages with role, content, sequence order, `message_type`, and a JSON `metadata` blob (carries tool-call detail) |
| `tags` | User-defined tags for organizing conversations |
| `conversation_tags` | Many-to-many junction between conversations and tags |

**DuckDB compatibility note**: No database-level foreign key constraints are used because DuckDB does not support CASCADE or UPDATE on FK-constrained tables. Referential integrity is enforced in the application layer.

## Alembic Migrations

For production PostgreSQL deployments, use Alembic for schema migrations.
All Alembic commands must be run **from the project root** (where `alembic.ini` lives):

```bash
cd /path/to/atlas-ui-3   # project root, same directory as alembic.ini

# Set the database URL (or have it in your .env)
export CHAT_HISTORY_DB_URL=postgresql://atlas:atlas@localhost:5432/atlas_chat_history

# Alternatively, leave CHAT_HISTORY_DB_URL unset and provide DB_HOST, DB_PORT,
# DB_NAME, DB_USER, DB_PASSWORD, and optional DB_DRIVER; Alembic assembles the URL.

# Apply all migrations to bring the database up to date
alembic upgrade head

# Check which migration the database is currently on
alembic current

# Create a new migration after changing models.py (PostgreSQL only; DuckDB does not support autogenerate)
alembic revision --autogenerate -m "description"
```

**Important**: `alembic.ini` and the `alembic/` directory are both at the project root. If you run Alembic from a different directory, it will fail with "No config file 'alembic.ini' found."

**If tables already exist** (e.g., the backend ran first and `init_database()` created them via `create_all`), `alembic upgrade head` will fail with `DuplicateTable`. In that case, stamp the existing database without re-running the migration:

```bash
alembic stamp 001
```

This tells Alembic "the DB is already at revision 001" so future migrations will work correctly.

For development with DuckDB, Alembic is **not required** -- `init_database()` creates all tables automatically on startup via `Base.metadata.create_all`.

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

### Listing one page at a time

`GET /api/conversations` returns a page of the caller's history, newest
first, and says how much of it is left:

```json
{
  "conversations": [ ... ],
  "has_more": true,
  "total": 342
}
```

- `limit` (1-200, default 50) and `offset` (default 0) select the window.
- `has_more` is true while older conversations remain beyond the window.
- `total` counts the same set the page comes from -- the caller's own
  conversations, narrowed to `tag` when one is given -- so a client can tell
  "there is an older page" from "that is all of them" without fetching the
  whole history.

The page is served from the same query the first page uses, so the
per-user scope and the tag filter apply to every page; a caller cannot page
its way into another account's conversations. The order is
`updated_at` descending with the id as a tiebreak, so a page boundary
cannot hand the same row out twice or skip one.

The sidebar walks back through the history with this (see
[Browsing Older Saved Conversations](../user-guide/conversation-history/README.md)).

## Save Modes (Not Saved / Browser / Server)

Each conversation has one of three persistence states, chosen from the
**Save this chat** control in the header's conversation-setup cluster:

- **Not saved** -- the conversation transcript is not written to the browser
  or the server. Uploaded files and browser preferences can still be stored;
  the conversation itself is gone when the user leaves it.
- **Saved in this browser** -- the conversation is stored in IndexedDB on
  that device, under that account.
- **Saved to server** -- the conversation is stored in the backend database
  and appears in server-side history.

The control is a three-choice menu, not a cycling toggle, and its wording
describes how *this conversation* is persisted. It does not hide or delete
conversations that are already in history. The choice persists across
refreshes in the `chatui-save-mode` browser-local preference. Switching to
**Saved to server** after a chat has started applies from the next turn:
turns already withheld from server persistence are not uploaded
retroactively, and the save menu says so.

The selected mode is sent over WebSocket as `save_mode`; the legacy
`incognito` boolean (`save_mode !== "server"`) is still sent for older
clients. The backend tracks the session's save floor and skips persistence for
turns taken while the conversation was not being saved. This is designed for
national lab environments where users may need to discuss sensitive topics
without creating a server record.

## Frontend

The sidebar shows:
- **Conversation list** with title, preview, timestamp, message count, and tags
- **Search bar** for filtering by title or content
- **Tag filter** buttons for quick filtering
- **Load more** at the bottom of the list, which appends the next page of
  older conversations (see
  [Browsing Older Saved Conversations](../user-guide/conversation-history/README.md))
- **Delete All** button in the footer

The **Header** groups conversation setup together, with security level first:
- **Security level** -- an explicitly labelled selector. It locks once a
  conversation starts and then shows a lock icon, "Locked for this chat", and
  the next step ("Start a new chat to change security level") inline.
- **Save this chat** -- the three persistence states above, with a short
  explanation of what each one means.
- **New Chat** -- starts a fresh conversation and unlocks security level.

Optional explanations appear behind the small help button next to the
cluster and inside the save menu. Users who already know the interface can
turn on **Reduce helper text** in **Tools and Settings > General** to hide
that copy and hover guidance; labels, selected values, scope disclosures, and
warnings remain.

The transcript menu offers **Open as plain text** first, followed by
**Print / Save as PDF**. The header no longer offers a JSON transcript
download; for a machine-readable copy of stored conversations use
`GET /api/conversations/export`, or the sidebar's **Download All
Conversations** action when chat history is enabled.

Loading a saved conversation replaces the current chat view with the saved messages.

### Tool calls in saved conversations

Tool calls (MCP tool input arguments and output results) are persisted alongside
the conversation as display-only `tool_call` messages, so reloading a saved
conversation re-renders the tool input/output exactly as it appeared live.
Each `tool_call` row is stored with its detail in the message `metadata_json`
column (tool name, server, arguments, result, status) and is **excluded from the
LLM context** on a subsequent turn — these rows exist purely to re-render the
transcript and are never replayed to the model. They are also included in the
plain-text chat export. The canvas tool is intentionally not recorded
as a transcript row (it renders into the canvas panel). This applies to both
regular (tools-mode) chat and agent-mode runs: the agentic loop records the
same `tool_start`/`tool_complete`/`tool_error` events its tool calls stream to
the UI and flushes them into history after each agent step (always before the
final assistant message). Per-step flushing also means an agent turn that
errors mid-run keeps the tool calls from the steps that already completed; a
turn that fails inside its very first tool step persists no tool rows for that
step (matching tools mode, where a turn that errors before its flush persists
none).

To keep saved conversations from growing without bound, large string values in
the persisted arguments/result (for example a base64 file upload sent as a tool
input, or a very large tool output) are truncated with a `…[truncated N chars]`
marker before being written to `metadata_json`. The live UI event the user sees
is unaffected — only the stored copy is capped.

## User Isolation

All database queries are scoped by `user_email`. Users cannot see, search, modify, or delete conversations belonging to other users.
