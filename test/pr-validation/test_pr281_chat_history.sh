#!/bin/bash
# Test script for PR #281: Save chat history
# Covers: DuckDB persistence, conversation CRUD, search, tags, feature flag,
#          incognito mode, API endpoints, frontend build.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT"

echo "=== PR #281: Chat History Validation ==="
echo ""

# --- 1. Module structure exists ---
echo "--- Module Structure ---"
test -f atlas/modules/chat_history/__init__.py
print_result $? "chat_history module __init__.py exists"

test -f atlas/modules/chat_history/models.py
print_result $? "chat_history models.py exists"

test -f atlas/modules/chat_history/database.py
print_result $? "chat_history database.py exists"

test -f atlas/modules/chat_history/conversation_repository.py
print_result $? "chat_history conversation_repository.py exists"

# --- 2. Alembic migration exists ---
echo ""
echo "--- Alembic Migration ---"
test -f alembic.ini
print_result $? "alembic.ini exists"

test -f alembic/env.py
print_result $? "alembic/env.py exists"

test -f alembic/versions/001_initial_chat_history_schema.py
print_result $? "Initial migration script exists"

# --- 3. Feature flag in AppSettings ---
echo ""
echo "--- Feature Flag ---"
python -c "
from atlas.modules.config.config_manager import AppSettings
s = AppSettings()
assert hasattr(s, 'feature_chat_history_enabled'), 'missing feature_chat_history_enabled'
assert s.feature_chat_history_enabled is False, 'should default to False'
assert hasattr(s, 'chat_history_db_url'), 'missing chat_history_db_url'
print('Feature flag defaults verified')
" 2>&1
print_result $? "Feature flag defaults (disabled by default)"

# --- 4. DuckDB database creation and table init ---
echo ""
echo "--- DuckDB Database Init ---"
TEST_DB="/tmp/test_pr281_chat_history_$$.db"
python -c "
from atlas.modules.chat_history import init_database, get_session_factory
from atlas.modules.chat_history.database import reset_engine
reset_engine()
engine = init_database('duckdb:///${TEST_DB}')
from sqlalchemy import inspect
inspector = inspect(engine)
tables = inspector.get_table_names()
assert 'conversations' in tables, f'missing conversations table, got {tables}'
assert 'conversation_messages' in tables, f'missing conversation_messages table, got {tables}'
assert 'tags' in tables, f'missing tags table, got {tables}'
assert 'conversation_tags' in tables, f'missing conversation_tags table, got {tables}'
print(f'Tables created: {tables}')
reset_engine()
" 2>&1
print_result $? "DuckDB tables created successfully"
rm -f "$TEST_DB" "${TEST_DB}.wal"

# --- 5. Conversation CRUD end-to-end ---
echo ""
echo "--- Conversation CRUD ---"
TEST_DB="/tmp/test_pr281_crud_$$.db"
python -c "
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine
reset_engine()
init_database('duckdb:///${TEST_DB}')
repo = ConversationRepository(get_session_factory())

# Save
conv = repo.save_conversation('c1', 'user@test.com', 'Test Chat', 'gpt-4',
    [{'role': 'user', 'content': 'Hello'}, {'role': 'assistant', 'content': 'Hi!'}])
assert conv.id == 'c1'
assert conv.message_count == 2

# List
convs = repo.list_conversations('user@test.com')
assert len(convs) == 1
assert convs[0]['title'] == 'Test Chat'

# Get with messages
full = repo.get_conversation('c1', 'user@test.com')
assert len(full['messages']) == 2
assert full['messages'][0]['content'] == 'Hello'

# Update title
repo.update_title('c1', 'Renamed Chat', 'user@test.com')
updated = repo.get_conversation('c1', 'user@test.com')
assert updated['title'] == 'Renamed Chat'

# Delete
assert repo.delete_conversation('c1', 'user@test.com') is True
assert repo.get_conversation('c1', 'user@test.com') is None

print('CRUD operations verified')
reset_engine()
" 2>&1
print_result $? "Conversation CRUD (save, list, get, update, delete)"
rm -f "$TEST_DB" "${TEST_DB}.wal"

# --- 6. Search ---
echo ""
echo "--- Search ---"
TEST_DB="/tmp/test_pr281_search_$$.db"
python -c "
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine
reset_engine()
init_database('duckdb:///${TEST_DB}')
repo = ConversationRepository(get_session_factory())

repo.save_conversation('s1', 'u@t.com', 'Python Tips', 'gpt-4',
    [{'role': 'user', 'content': 'Tell me about quantum computing'}])
repo.save_conversation('s2', 'u@t.com', 'JavaScript', 'gpt-4',
    [{'role': 'user', 'content': 'React hooks'}])

# Search by title
r1 = repo.search_conversations('u@t.com', 'Python')
assert len(r1) == 1 and r1[0]['id'] == 's1'

# Search by content
r2 = repo.search_conversations('u@t.com', 'quantum')
assert len(r2) == 1 and r2[0]['id'] == 's1'

# No results
r3 = repo.search_conversations('u@t.com', 'zzz')
assert len(r3) == 0

print('Search verified')
reset_engine()
" 2>&1
print_result $? "Search by title and message content"
rm -f "$TEST_DB" "${TEST_DB}.wal"

# --- 7. Tags ---
echo ""
echo "--- Tags ---"
TEST_DB="/tmp/test_pr281_tags_$$.db"
python -c "
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine
reset_engine()
init_database('duckdb:///${TEST_DB}')
repo = ConversationRepository(get_session_factory())

repo.save_conversation('t1', 'u@t.com', 'Work Chat', 'gpt-4',
    [{'role': 'user', 'content': 'Hello'}])
repo.save_conversation('t2', 'u@t.com', 'Personal Chat', 'gpt-4',
    [{'role': 'user', 'content': 'Hi'}])

# Add tag
tag_id = repo.add_tag('t1', 'work', 'u@t.com')
assert tag_id is not None

# Filter by tag
filtered = repo.list_conversations('u@t.com', tag_name='work')
assert len(filtered) == 1 and filtered[0]['id'] == 't1'

# Tags in conversation
full = repo.get_conversation('t1', 'u@t.com')
assert 'work' in full['tags']

# List tags
tags = repo.list_tags('u@t.com')
assert len(tags) == 1 and tags[0]['name'] == 'work'

print('Tags verified')
reset_engine()
" 2>&1
print_result $? "Tag operations (add, filter, list)"
rm -f "$TEST_DB" "${TEST_DB}.wal"

# --- 8. Multi-delete and delete all ---
echo ""
echo "--- Multi-Delete ---"
TEST_DB="/tmp/test_pr281_mdel_$$.db"
python -c "
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine
reset_engine()
init_database('duckdb:///${TEST_DB}')
repo = ConversationRepository(get_session_factory())

for i in range(5):
    repo.save_conversation(f'd{i}', 'u@t.com', f'Conv {i}', 'gpt-4',
        [{'role': 'user', 'content': f'Msg {i}'}])

# Multi-delete
count = repo.delete_conversations(['d0', 'd2', 'd4'], 'u@t.com')
assert count == 3
remaining = repo.list_conversations('u@t.com')
assert len(remaining) == 2

# Delete all
count2 = repo.delete_all_conversations('u@t.com')
assert count2 == 2
assert len(repo.list_conversations('u@t.com')) == 0

print('Multi-delete verified')
reset_engine()
" 2>&1
print_result $? "Multi-delete and delete-all"
rm -f "$TEST_DB" "${TEST_DB}.wal"

# --- 9. User isolation ---
echo ""
echo "--- User Isolation ---"
TEST_DB="/tmp/test_pr281_iso_$$.db"
python -c "
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine
reset_engine()
init_database('duckdb:///${TEST_DB}')
repo = ConversationRepository(get_session_factory())

repo.save_conversation('a1', 'alice@t.com', 'Alice Chat', 'gpt-4',
    [{'role': 'user', 'content': 'Alice msg'}])
repo.save_conversation('b1', 'bob@t.com', 'Bob Chat', 'gpt-4',
    [{'role': 'user', 'content': 'Bob msg'}])

# Alice cannot see Bob
assert repo.get_conversation('b1', 'alice@t.com') is None
assert len(repo.list_conversations('alice@t.com')) == 1
# Bob cannot delete Alice
assert repo.delete_conversation('a1', 'bob@t.com') is False

print('User isolation verified')
reset_engine()
" 2>&1
print_result $? "User isolation (cross-user access denied)"
rm -f "$TEST_DB" "${TEST_DB}.wal"

# --- 10. API routes (start server and test with curl) ---
echo ""
echo "--- API Routes ---"
TEST_DB="/tmp/test_pr281_api_$$.db"

# Use pytest for route tests since they need the full app context
PYTHONPATH="$PROJECT_ROOT" python -m pytest atlas/tests/test_chat_history.py::TestConversationRoutes -v --tb=short 2>&1 | tail -15
print_result ${PIPESTATUS[0]} "API route tests (via pytest)"
rm -f "$TEST_DB" "${TEST_DB}.wal"

# --- 11. Frontend build ---
echo ""
echo "--- Frontend Build ---"
cd "$PROJECT_ROOT/frontend"
if [ -d "node_modules" ]; then
    npx vite build > /dev/null 2>&1
    print_result $? "Frontend builds successfully"
else
    npm install > /dev/null 2>&1 && npx vite build > /dev/null 2>&1
    print_result $? "Frontend builds successfully"
fi
cd "$PROJECT_ROOT"

# --- 12. Frontend lint ---
echo ""
echo "--- Frontend Lint ---"
cd "$PROJECT_ROOT/frontend"
npx eslint src/components/Sidebar.jsx src/hooks/useConversationHistory.js src/contexts/ChatContext.jsx > /dev/null 2>&1
print_result $? "Frontend lint (no errors in modified files)"
cd "$PROJECT_ROOT"

# --- 13. Ruff lint ---
echo ""
echo "--- Python Lint ---"
python -m ruff check atlas/modules/chat_history/ atlas/routes/conversation_routes.py atlas/tests/test_chat_history.py > /dev/null 2>&1 || \
    ruff check atlas/modules/chat_history/ atlas/routes/conversation_routes.py atlas/tests/test_chat_history.py > /dev/null 2>&1
print_result $? "Ruff lint (no errors in modified files)"

# --- 14. Incognito flag in ChatService ---
echo ""
echo "--- Incognito Mode ---"
python -c "
from atlas.application.chat.service import ChatService
from unittest.mock import MagicMock
svc = ChatService(llm=MagicMock())
assert hasattr(svc, '_incognito_sessions'), 'ChatService missing _incognito_sessions'
assert hasattr(svc, 'conversation_repository'), 'ChatService missing conversation_repository'
print('Incognito/persistence attributes verified')
" 2>&1
print_result $? "ChatService has incognito tracking and conversation_repository"

# --- Final: run backend unit tests ---
echo ""
echo "--- Backend Unit Tests ---"
cd "$PROJECT_ROOT"
./test/run_tests.sh backend 2>&1 | tail -5
print_result ${PIPESTATUS[0]} "Backend unit tests"

# Summary
echo ""
echo "=============================="
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
