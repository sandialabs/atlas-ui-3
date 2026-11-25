#!/bin/bash
# Issue #1223: WebSocket client hardening.
#
# Checks:
#   1. Frontend suites for held navigation frames, acknowledgement, expiry,
#      session end, reconnect backoff, and failed control-frame sends.
#   2. The backend echoes request_id on replies to reset_session and
#      restore_conversation.
#   3. The existing session resume and sign-in suites still pass.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

fail() { echo "FAILED: $1"; exit 1; }

cd "$PROJECT_ROOT/frontend" || exit 1
npx vitest run src/test/ws-navigation-ack.test.jsx src/test/ws-navigation-handler.test.js \
    src/test/elicitation-dialog.test.jsx src/test/ws-session-resume.test.jsx \
    src/test/ws-session-ended-signin.test.jsx \
    || fail "frontend websocket hardening"

cd "$PROJECT_ROOT/atlas" || exit 1
PY="$PROJECT_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY=python
PYTHONPATH="$PROJECT_ROOT" "$PY" -m pytest tests/test_nav_request_id_echo.py -q \
    || fail "backend request_id echo"

echo "PASSED: PR 1223 websocket client hardening validation"
