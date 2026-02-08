#!/bin/bash
# PR #317 Validation Script: Attach conversation history to feedback (issue #307)
# Tests that feedback submission accepts conversation_history as an inline JSON field.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #317 Validation: Feedback Conversation History"
echo "=========================================="

cd "$PROJECT_ROOT"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT"
PASS_COUNT=0
FAIL_COUNT=0

pass() { echo "PASSED: $1"; PASS_COUNT=$((PASS_COUNT + 1)); }
fail() { echo "FAILED: $1"; FAIL_COUNT=$((FAIL_COUNT + 1)); }

echo ""
echo "1. Test FeedbackData model accepts conversation_history field"
echo "-------------------------------------------------------------"
python -c "
from atlas.routes.feedback_routes import FeedbackData
fb = FeedbackData(rating=1, comment='test', conversation_history='USER:\nHello\n')
assert fb.conversation_history == 'USER:\nHello\n', 'conversation_history not set'
print('conversation_history field accepted')
" && pass "FeedbackData accepts conversation_history" || fail "FeedbackData conversation_history field"

echo ""
echo "2. Test FeedbackData defaults conversation_history to empty string"
echo "------------------------------------------------------------------"
python -c "
from atlas.routes.feedback_routes import FeedbackData
fb = FeedbackData(rating=0)
assert fb.conversation_history == '', 'Default should be empty string'
print('Default conversation_history is empty string')
" && pass "FeedbackData defaults conversation_history" || fail "FeedbackData default value"

echo ""
echo "3. Test feedback submission stores conversation history inline in JSON"
echo "----------------------------------------------------------------------"
python -c "
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
import sys; sys.path.insert(0, '$PROJECT_ROOT/atlas')
from main import app

tmpdir = tempfile.mkdtemp()

def mock_get_dir():
    return Path(tmpdir)

async def mock_admin(user, group):
    return user == 'admin@test.com'

with patch('atlas.routes.feedback_routes.get_feedback_directory', mock_get_dir), \
     patch('atlas.routes.feedback_routes.is_user_in_group', mock_admin):
    client = TestClient(app)

    # Submit feedback with conversation history
    resp = client.post(
        '/api/feedback',
        json={
            'rating': -1,
            'comment': 'Model was wrong',
            'session': {},
            'conversation_history': 'USER:\nWhat is 2+2?\n\nASSISTANT:\n5\n'
        },
        headers={'X-User-Email': 'user@test.com'}
    )
    assert resp.status_code == 200, f'Submit failed: {resp.text}'

    # Check conversation_history is stored inline in the JSON
    json_files = list(Path(tmpdir).glob('feedback_*.json'))
    assert len(json_files) == 1
    with open(json_files[0]) as f:
        data = json.load(f)
    assert data['conversation_history'] == 'USER:\nWhat is 2+2?\n\nASSISTANT:\n5\n'
    print('Conversation history stored inline in JSON')

    # Verify admin GET returns it
    resp = client.get('/api/feedback', headers={'X-User-Email': 'admin@test.com'})
    assert resp.status_code == 200
    fb_list = resp.json()['feedback']
    assert fb_list[0]['conversation_history'] == 'USER:\nWhat is 2+2?\n\nASSISTANT:\n5\n'
    print('Admin GET returns conversation history')

    # Verify JSON download includes it
    resp = client.get('/api/feedback/download?format=json', headers={'X-User-Email': 'admin@test.com'})
    assert resp.status_code == 200
    dl_data = resp.json()
    assert dl_data[0]['conversation_history'] == 'USER:\nWhat is 2+2?\n\nASSISTANT:\n5\n'
    print('JSON download includes conversation history')

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
" && pass "End-to-end feedback conversation history flow" || fail "End-to-end feedback conversation history flow"

echo ""
echo "4. Test feedback without conversation history stores null"
echo "---------------------------------------------------------"
python -c "
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from starlette.testclient import TestClient
import sys; sys.path.insert(0, '$PROJECT_ROOT/atlas')
from main import app

tmpdir = tempfile.mkdtemp()

def mock_get_dir():
    return Path(tmpdir)

async def mock_admin(user, group):
    return True

with patch('atlas.routes.feedback_routes.get_feedback_directory', mock_get_dir), \
     patch('atlas.routes.feedback_routes.is_user_in_group', mock_admin):
    client = TestClient(app)
    resp = client.post(
        '/api/feedback',
        json={'rating': 1, 'comment': 'All good', 'session': {}},
        headers={'X-User-Email': 'user@test.com'}
    )
    assert resp.status_code == 200

    json_files = list(Path(tmpdir).glob('feedback_*.json'))
    with open(json_files[0]) as f:
        data = json.load(f)
    assert data['conversation_history'] is None
    print('conversation_history is null when not provided')

import shutil
shutil.rmtree(tmpdir, ignore_errors=True)
" && pass "Null conversation_history when not provided" || fail "Null conversation_history check"

echo ""
echo "5. Test frontend FeedbackButton component"
echo "-------------------------------------------"
if grep -q "useChat" "$PROJECT_ROOT/frontend/src/components/FeedbackButton.jsx"; then
    pass "FeedbackButton imports useChat"
else
    fail "FeedbackButton does not import useChat"
fi

if grep -q "includeHistory" "$PROJECT_ROOT/frontend/src/components/FeedbackButton.jsx"; then
    pass "FeedbackButton has includeHistory state"
else
    fail "FeedbackButton missing includeHistory state"
fi

if grep -q "conversation_history" "$PROJECT_ROOT/frontend/src/components/FeedbackButton.jsx"; then
    pass "FeedbackButton sends conversation_history"
else
    fail "FeedbackButton does not send conversation_history"
fi

echo ""
echo "6. Test AdminModal shows conversation history"
echo "-----------------------------------------------"
if grep -q "conversation_history" "$PROJECT_ROOT/frontend/src/components/AdminModal.jsx"; then
    pass "AdminModal checks conversation_history"
else
    fail "AdminModal missing conversation_history check"
fi

if grep -q "Conversation history attached" "$PROJECT_ROOT/frontend/src/components/AdminModal.jsx"; then
    pass "AdminModal shows conversation history label"
else
    fail "AdminModal missing conversation history label"
fi

echo ""
echo "7. Run backend unit tests"
echo "-------------------------"
./test/run_tests.sh backend && pass "All backend tests pass" || fail "Backend tests"

echo ""
echo "=========================================="
echo "Results: $PASS_COUNT passed, $FAIL_COUNT failed"
echo "=========================================="

if [ "$FAIL_COUNT" -gt 0 ]; then
    exit 1
fi
echo "All PR #317 validations passed!"
