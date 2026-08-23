#!/bin/bash
# Test script for PR #835: re-enable a conversation's workspace on reload (#829)
#
# Covers:
#   - The active workspace id is persisted in conversation metadata on save.
#   - get_conversation returns the stored workspace_id in metadata.
#   - ChatService.handle_chat_message captures workspace_id into session
#     context and _save_conversation persists it.
#   - The REST route (GET /api/conversations/{id}) returns workspace_id in
#     metadata (exercises the route -> repository boundary).
#   - A null/missing workspace_id round-trips as null (backward compat).
#   - The backend + frontend unit tests pass and the frontend builds.

set -o pipefail

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

# `duckdb:///:memory:` is not an in-memory URL for this driver -- it creates a
# real file literally named ':memory:' in the working directory, so rows leak
# between runs and can mask a persistence bug. Use a fresh per-run temp file and
# remove it on exit.
TMP_DB_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DB_DIR"' EXIT
# Each step below exports its own TEST_DB_URL so no state carries across steps.

echo "=== PR #835: Restore Workspace on Conversation Load (#829) ==="
echo ""

# --- 1. Repository persists workspace_id in conversation metadata ---
echo "--- Repository: workspace_id in metadata ---"
TEST_DB_URL="duckdb:///$TMP_DB_DIR/step1.duckdb" python -c "
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine
import os
reset_engine()
init_database(os.environ['TEST_DB_URL'])
repo = ConversationRepository(get_session_factory())
repo.save_conversation(
    conversation_id='conv-ws',
    user_email='user@test.com',
    title='WS Conv',
    model='gpt-4',
    messages=[{'role': 'user', 'content': 'hi'}],
    metadata={'agent_mode': False, 'workspace_id': 'ws-work'},
)
result = repo.get_conversation('conv-ws', 'user@test.com')
assert result is not None, 'conversation not found'
assert result['metadata'].get('workspace_id') == 'ws-work', f\"got {result['metadata']}\"
print('workspace_id persisted and returned in metadata')
reset_engine()
" 2>&1
print_result $? "save_conversation persists workspace_id in metadata"

# --- 2. Upsert updates the workspace_id ---
echo ""
echo "--- Repository: upsert updates workspace_id ---"
TEST_DB_URL="duckdb:///$TMP_DB_DIR/step2.duckdb" python -c "
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine
import os
reset_engine()
init_database(os.environ['TEST_DB_URL'])
repo = ConversationRepository(get_session_factory())
repo.save_conversation('c1','user@test.com','t','gpt-4',[{'role':'user','content':'a'}], metadata={'agent_mode': False, 'workspace_id': 'ws-a'})
repo.save_conversation('c1','user@test.com','t','gpt-4',[{'role':'user','content':'a'},{'role':'assistant','content':'b'}], metadata={'agent_mode': False, 'workspace_id': 'ws-b'})
result = repo.get_conversation('c1', 'user@test.com')
assert result['metadata'].get('workspace_id') == 'ws-b', f\"got {result['metadata']}\"
assert len(result['messages']) == 2
print('upsert replaced workspace_id')
reset_engine()
" 2>&1
print_result $? "upsert replaces workspace_id"

# --- 3. Null workspace_id round-trips ---
echo ""
echo "--- Repository: null workspace_id round-trips ---"
TEST_DB_URL="duckdb:///$TMP_DB_DIR/step3.duckdb" python -c "
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine
import os
reset_engine()
init_database(os.environ['TEST_DB_URL'])
repo = ConversationRepository(get_session_factory())
repo.save_conversation('c2','user@test.com','t','gpt-4',[{'role':'user','content':'a'}], metadata={'agent_mode': False, 'workspace_id': None})
result = repo.get_conversation('c2', 'user@test.com')
assert result['metadata'].get('workspace_id') is None, f\"got {result['metadata']}\"
print('null workspace_id round-tripped')
reset_engine()
" 2>&1
print_result $? "null workspace_id round-trips"

# --- 4. ChatService captures workspace_id and persists it ---
echo ""
echo "--- Service: handle_chat_message captures + persists workspace_id ---"
TEST_DB_URL="duckdb:///$TMP_DB_DIR/step4.duckdb" python -c "
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from atlas.application.chat.service import ChatService
from atlas.domain.messages.models import Message, MessageRole
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine
import os
from atlas.modules.config.config_manager import config_manager
reset_engine()
init_database(os.environ['TEST_DB_URL'])
repo = ConversationRepository(get_session_factory())
sessions = {}
async def _get(sid): return sessions.get(sid)
async def _create(s): sessions[s.id] = s
async def _update(s): sessions[s.id] = s
sr = MagicMock(); sr.get = AsyncMock(side_effect=_get); sr.create = AsyncMock(side_effect=_create); sr.update = AsyncMock(side_effect=_update)
service = ChatService(llm=MagicMock(), tool_manager=MagicMock(), connection=MagicMock(), config_manager=MagicMock(), session_repository=sr, conversation_repository=repo)
sid = uuid4()
async def fake_execute(**kw):
    sessions[sid].history.add_message(Message(role=MessageRole.ASSISTANT, content='r'))
    return {'type':'done'}
mo = MagicMock(); mo.execute = AsyncMock(side_effect=fake_execute)
with patch.object(service, '_get_orchestrator', return_value=mo):
    import asyncio
    asyncio.run(service.handle_chat_message(session_id=sid, content='hi', model='m', user_email=config_manager.app_settings.test_user, workspace_id='ws-work'))
s = sessions[sid]
assert s.context.get('workspace_id') == 'ws-work', f\"ctx={s.context.get('workspace_id')}\"
conv_id = s.context.get('conversation_id')
saved = repo.get_conversation(conv_id, config_manager.app_settings.test_user)
assert saved is not None
assert saved['metadata'].get('workspace_id') == 'ws-work', f\"meta={saved['metadata']}\"
print('service captured and persisted workspace_id')
reset_engine()
" 2>&1
print_result $? "handle_chat_message captures and persists workspace_id"

# --- 5. REST route returns workspace_id in metadata (route boundary) ---
# Runs the route-level suite on its own so this step asserts the route ->
# repository boundary itself rather than only printing. It needs the project's
# conftest isolation (FastAPI TestClient against the real route), which the bare
# `python -c` blocks above do not have, so it is run through pytest.
echo ""
echo "--- REST route: GET /api/conversations/{id} returns workspace_id ---"
python -m pytest atlas/tests/test_chat_history.py -q \
    -k "TestConversationRoutes and workspace_id" 2>&1 | tail -5
print_result ${PIPESTATUS[0]} "REST route returns workspace_id in metadata"

# --- 6. Backend unit tests for the feature ---
echo ""
echo "--- Backend unit tests ---"
python -m pytest atlas/tests/test_workspace_conversation_binding.py atlas/tests/test_chat_history.py -q 2>&1 | tail -5
print_result ${PIPESTATUS[0]} "backend unit tests pass"

# --- 7. Frontend unit tests for the feature ---
echo ""
echo "--- Frontend unit tests ---"
cd "$PROJECT_ROOT/frontend"
npx vitest run src/test/workspace-restore-on-conversation-load.test.jsx src/test/workspace-pointer.test.js 2>&1 | tail -8
print_result ${PIPESTATUS[0]} "frontend workspace tests pass"

# --- 8. Frontend build ---
echo ""
echo "--- Frontend build ---"
npm run build 2>&1 | tail -3
print_result ${PIPESTATUS[0]} "frontend builds cleanly"
cd "$PROJECT_ROOT"

# --- Summary ---
echo ""
echo "=== Summary ==="
echo "PASSED: $PASSED"
echo "FAILED: $FAILED"
if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0