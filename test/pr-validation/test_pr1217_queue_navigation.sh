#!/bin/bash
# Issue #1217 (client half): a reset_session / restore_conversation sent while
# the WebSocket is down is held and replayed on the next open.
#
# Checks:
#   1. The pending-navigation tests pass (queue, replay, restore on throw,
#      supersede, OPEN-but-closing socket).
#   2. The existing session-resume frontend suite still passes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT/frontend" || exit 1

fail() { echo "FAILED: $1"; exit 1; }

npx vitest run src/test/ws-pending-navigation.test.jsx src/test/ws-session-resume.test.jsx \
    || fail "frontend pending navigation"

echo "PASSED: PR 1217 queue-navigation validation"
