# Chat History Feature - Issue #281

Last updated: 2026-02-15

## Summary

Save and reload chat history with full tool call, RAG, and agent data. Support
incognito mode, conversation search, tags, multi-select delete, and improved
export. Use DuckDB for local dev, PostgreSQL for production. Feature-flagged off
by default. Use Alembic for migrations.

## Requirements (from issue)

1. **Persist conversations** to database (DuckDB local, PostgreSQL production)
2. **Reload old conversations** with all tool, RAG, agent data intact
3. **Improve conversation export** to include tool call invocations/results and RAG data
4. **Incognito mode** - ephemeral conversations, very prominent visual indicator
5. **Alembic** for database migrations
6. **Feature-flagged** - `FEATURE_CHAT_HISTORY_ENABLED=false` by default
7. **Delete conversations** - single, multi-select, or all
8. **Search and tags** for conversations
9. **Auto-summarization/naming** with LLM (future, not critical path)

## Architecture Decisions

### Database Strategy
- **SQLAlchemy** as the ORM (supports both DuckDB and PostgreSQL)
- **duckdb-engine** for local development (no server needed)
- **psycopg2** for PostgreSQL in production
- **Alembic** for schema migrations
- Config: `CHAT_HISTORY_DB_URL` env var
  - Default: `duckdb:///data/chat_history.db` (project-relative)
  - Production: `postgresql://user:pass@host/dbname`

### Database Schema

```
conversations
  id              UUID PRIMARY KEY
  user_email      VARCHAR(255) INDEXED
  title           VARCHAR(500) NULLABLE  -- for auto-naming later
  model           VARCHAR(255)
  created_at      DATETIME
  updated_at      DATETIME
  message_count   INTEGER DEFAULT 0
  metadata        JSON  -- agent settings, compliance level, etc.

conversation_messages
  id              UUID PRIMARY KEY
  conversation_id UUID FK -> conversations.id (CASCADE DELETE)
  role            VARCHAR(20)  -- user/assistant/system/tool
  content         TEXT
  message_type    VARCHAR(50)  -- chat, tool_call, tool_result, agent_update, etc.
  timestamp       DATETIME
  sequence_number INTEGER      -- for ordering
  metadata        JSON         -- tool name, arguments, result, RAG sources, etc.

tags
  id              UUID PRIMARY KEY
  name            VARCHAR(100)
  user_email      VARCHAR(255)
  created_at      DATETIME
  UNIQUE(name, user_email)

conversation_tags
  conversation_id UUID FK -> conversations.id (CASCADE DELETE)
  tag_id          UUID FK -> tags.id (CASCADE DELETE)
  PRIMARY KEY(conversation_id, tag_id)
```

### Incognito Mode
- Per-session toggle on the frontend (prominent UI element)
- When enabled: conversation is NOT persisted to database
- The in-memory session still works as today (no behavior change)
- Visual indicator: dark overlay/badge on sidebar, header banner
- WebSocket `chat` message includes `incognito: true/false`
- Backend checks this flag before persisting

### Where Persistence Hooks In
- **On each assistant response completion**: save the full conversation
  snapshot (not streaming - only after response is finalized)
- Uses an async background task so it doesn't block WebSocket response
- The orchestrator calls `conversation_repository.save()` after each
  exchange (user message + assistant response)

### New Files

```
atlas/
  modules/
    chat_history/
      __init__.py
      database.py           -- Engine factory, session maker
      models.py             -- SQLAlchemy table models
      conversation_repository.py  -- CRUD operations
  routes/
    conversation_routes.py  -- REST API for conversations

alembic/
  alembic.ini
  env.py
  versions/
    001_initial_schema.py
```

### API Endpoints

```
GET    /api/conversations                 -- List user's conversations (paginated)
GET    /api/conversations/:id             -- Get conversation with messages
DELETE /api/conversations/:id             -- Delete single conversation
POST   /api/conversations/delete          -- Multi-delete {ids: [...]}
DELETE /api/conversations                 -- Delete all user conversations
GET    /api/conversations/search?q=...    -- Search conversations
POST   /api/conversations/:id/tags        -- Add tag to conversation
DELETE /api/conversations/:id/tags/:tagId -- Remove tag
GET    /api/tags                          -- List user's tags
```

### Frontend Changes

1. **Sidebar.jsx** - Full conversation list with:
   - Conversation items showing title/date/model/preview
   - Search input at top
   - Tag filter dropdown
   - Multi-select mode with bulk delete
   - "New Conversation" button
   - Incognito toggle (prominent)

2. **ChatContext.jsx** - New state:
   - `conversationId` - current conversation DB ID
   - `isIncognito` - incognito mode toggle
   - `loadConversation(id)` - fetch and restore from DB
   - Modified `sendChatMessage` to include incognito flag
   - Modified `clearChat` to start new conversation

3. **Export enhancement** - Include in JSON export:
   - Tool call arguments and results in message metadata
   - RAG source information
   - Agent reasoning/observation data

## Implementation Phases

### Phase 1: Database Foundation
- Add dependencies to requirements.txt
- Create SQLAlchemy models
- Set up Alembic
- Add feature flag
- Create database engine factory

### Phase 2: Backend Repository & API
- Create ConversationRepository with CRUD + search + tags
- Wire into ChatOrchestrator (save on response completion)
- Create REST API routes
- Add incognito support

### Phase 3: Frontend
- Rebuild Sidebar with conversation history
- Add conversation loading/restoring
- Add incognito toggle
- Add search, tags, multi-select delete
- Enhance export format

### Phase 4: Tests
- Backend unit tests for repository
- API route tests
- Frontend component tests
- PR validation script

### Phase 5 (Future): Auto-summarization
- LLM call to generate conversation title
- Run async after first few exchanges
- Not in scope for this PR
