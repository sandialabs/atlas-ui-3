#!/bin/bash
# Issue #1217 (server half): a chat on a resumed session whose New Chat was lost
# while disconnected must start a new conversation, not run in or overwrite
# the old one.
#
# Checks:
#   1. The lifecycle rows (end to end through /ws, the real turn and save path).
#   2. The rebind helper and the existing resume handshake still pass.
#   3. The same sequence across two replicas (the multi-replica harness).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT" || exit 1
# shellcheck disable=SC1091
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/scripts"

fail() { echo "FAILED: $1"; exit 1; }

python -m pytest atlas/tests/test_resumed_session_new_chat.py \
    atlas/tests/test_resumed_session_fresh_history.py \
    atlas/tests/test_websocket_session_resume.py -q || fail "resume guard"
# Only this change's cases; the whole harness is a separate required run.
bash test/multireplica_tests.sh -k resumed_session || fail "multi-replica harness"

echo "PASSED: PR 1217 resumed-session guard validation"
