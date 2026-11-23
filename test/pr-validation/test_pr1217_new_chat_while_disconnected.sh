#!/bin/bash
# Issue #1217: New Chat clicked while the WebSocket is down must not make the
# next message run in, and be saved over, the previous conversation.
#
# Checks:
#   1. A resumed session's first server-mode chat frame that names no
#      conversation starts a new one; one that names it continues it.
#   2. The existing resume and WebSocket suites still pass.
#   3. The frontend delivers a reset_session / restore_conversation sent while
#      disconnected on the next open.
#   4. The multi-replica harness (run-handling code in main.py changed) passes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT" || exit 1
# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/scripts"

fail() { echo "FAILED: $1"; exit 1; }

python -m pytest atlas/tests/test_resumed_session_new_chat.py \
    atlas/tests/test_websocket_session_resume.py -q || fail "resume rule"
python -m pytest atlas/tests -q -k "websocket" -p no:cacheprovider || fail "websocket suite"
(cd frontend && npx vitest run src/test/ws-pending-navigation.test.jsx src/test/ws-session-resume.test.jsx) \
    || fail "frontend pending navigation"
bash test/multireplica_tests.sh || fail "multi-replica harness"

echo "PASSED: PR 1217 validation"
