#!/bin/bash
# PR #359 - Conversation save bug fix (#356) and download all conversations (#354)
# Validates that:
# 1. The conversation_saved WebSocket event is sent after save
# 2. The export_all_conversations repository method works correctly
# 3. The /api/conversations/export endpoint returns proper data
# 4. Frontend handler wires setActiveConversationId
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

echo "=========================================="
echo "PR #359 Validation: Conversation Save Fix + Export"
echo "=========================================="
echo ""

# Activate virtual environment (check worktree parent if not found locally)
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
elif [ -f "$(git -C "$PROJECT_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)/../.venv/bin/activate" ]; then
    source "$(git -C "$PROJECT_ROOT" rev-parse --path-format=absolute --git-common-dir)/../.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# --- Check 1: conversation_saved event is sent after save ---
echo "  Check 1: Backend sends conversation_saved event after save ..."
python3 -c "
import ast, sys
with open('$PROJECT_ROOT/atlas/application/chat/service.py') as f:
    source = f.read()
assert 'conversation_saved' in source, 'conversation_saved event type not found in service.py'
assert 'update_callback' in source, 'update_callback not used for conversation_saved'
print('    OK - conversation_saved event wired in service.py')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 2: Frontend handler handles conversation_saved ---
echo "  Check 2: Frontend WebSocket handler processes conversation_saved ..."
python3 -c "
with open('$PROJECT_ROOT/frontend/src/handlers/chat/websocketHandlers.js') as f:
    source = f.read()
assert \"case 'conversation_saved'\" in source, 'conversation_saved case not in handler'
assert 'setActiveConversationId' in source, 'setActiveConversationId not used in handler'
print('    OK - conversation_saved handler found in websocketHandlers.js')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 3: ChatContext passes setActiveConversationId to handler ---
echo "  Check 3: ChatContext passes setActiveConversationId to WebSocket handler ..."
python3 -c "
with open('$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx') as f:
    source = f.read()
# Check it's in the handler deps object
assert 'setActiveConversationId,' in source, 'setActiveConversationId not passed to handler'
print('    OK - setActiveConversationId wired in ChatContext')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 4: export_all_conversations repository method exists ---
echo "  Check 4: export_all_conversations repository method importable ..."
python3 -c "
from atlas.modules.chat_history.conversation_repository import ConversationRepository
assert hasattr(ConversationRepository, 'export_all_conversations'), 'export_all_conversations method not found'
print('    OK - export_all_conversations method exists')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 5: /api/conversations/export route is registered ---
echo "  Check 5: Export endpoint exists in conversation_routes.py ..."
python3 -c "
with open('$PROJECT_ROOT/atlas/routes/conversation_routes.py') as f:
    source = f.read()
assert '/export' in source, 'export route not found'
assert 'export_all_conversations' in source, 'export_all_conversations not called in route'
# Verify export comes before {conversation_id} to avoid route collision
export_pos = source.index('/export')
conv_id_pos = source.index('/{conversation_id}')
assert export_pos < conv_id_pos, 'export route must be defined before {conversation_id} route'
print('    OK - /api/conversations/export endpoint registered (before /{conversation_id})')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 6: Frontend downloadAll hook function ---
echo "  Check 6: downloadAll function in useConversationHistory hook ..."
python3 -c "
with open('$PROJECT_ROOT/frontend/src/hooks/useConversationHistory.js') as f:
    source = f.read()
assert 'downloadAll' in source, 'downloadAll function not found'
assert '/api/conversations/export' in source, 'export API URL not found in hook'
assert 'conversations-export-' in source, 'export filename pattern not found'
print('    OK - downloadAll hook function found')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 7: Download button in Sidebar ---
echo "  Check 7: Download All Conversations button in Sidebar ..."
python3 -c "
with open('$PROJECT_ROOT/frontend/src/components/Sidebar.jsx') as f:
    source = f.read()
assert 'Download All Conversations' in source, 'Download button text not found'
assert 'downloadAll' in source, 'downloadAll call not found in Sidebar'
print('    OK - Download button found in Sidebar')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 8: End-to-end export via repository ---
echo "  Check 8: End-to-end export functionality ..."
python3 -c "
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine
import tempfile, os

# Create temp DB
reset_engine()
tmp = tempfile.mkdtemp()
db_path = os.path.join(tmp, 'test_export.db')
db_url = f'duckdb:///{db_path}'
init_database(db_url)
factory = get_session_factory()
repo = ConversationRepository(factory)

# Save two conversations
for i in range(2):
    repo.save_conversation(
        conversation_id=f'e2e-{i}',
        user_email='tester@test.com',
        title=f'Export Test {i}',
        model='gpt-4',
        messages=[
            {'role': 'user', 'content': f'Question {i}'},
            {'role': 'assistant', 'content': f'Answer {i}'},
        ],
    )

# Export
result = repo.export_all_conversations('tester@test.com')
assert len(result) == 2, f'Expected 2 conversations, got {len(result)}'
for conv in result:
    assert len(conv['messages']) == 2, f'Expected 2 messages per conv'
    assert conv['messages'][0]['role'] == 'user'
    assert conv['messages'][1]['role'] == 'assistant'

# Isolation check
other_result = repo.export_all_conversations('other@test.com')
assert len(other_result) == 0, 'Other user should see 0 conversations'

reset_engine()
import shutil
shutil.rmtree(tmp)
print('    OK - Export returns 2 conversations with correct messages')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 9: Run backend unit tests ---
echo ""
echo "  Check 9: Running chat history unit tests ..."
cd "$PROJECT_ROOT"
python3 -m pytest atlas/tests/test_chat_history.py -v --tb=short 2>&1 | tail -20
PYTEST_EXIT=${PIPESTATUS[0]}
if [ "$PYTEST_EXIT" -eq 0 ]; then
    echo "PASS - All chat history tests passed"
    PASS=$((PASS+1))
else
    echo "FAIL - Some chat history tests failed"
    FAIL=$((FAIL+1))
fi

# --- Check 10: Run full backend tests ---
echo ""
echo "  Check 10: Running full backend test suite ..."
cd "$PROJECT_ROOT"
bash test/run_tests.sh backend 2>&1 | tail -5
BACKEND_EXIT=${PIPESTATUS[0]}
if [ "$BACKEND_EXIT" -eq 0 ]; then
    echo "PASS - Backend tests passed"
    PASS=$((PASS+1))
else
    echo "FAIL - Backend tests had failures"
    FAIL=$((FAIL+1))
fi

# --- Summary ---
echo ""
echo "=========================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=========================================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
