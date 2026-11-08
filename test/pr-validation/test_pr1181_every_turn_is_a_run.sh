#!/bin/bash
# Test script for PR #1181: every chat turn is a run; remove the untracked slot (issue #1128)
#
# Test plan:
# - No `active_chat_task` is left in atlas/main.py.
# - Plain, incognito and local-save turns are admitted as runs
#   (run_started carries connection_bound), keyed by the conversation their
#   session holds, with no title for an off-record turn.
# - Stop (addressed and unaddressed), New Chat (reset) and a socket closing end
#   the plain turn, and reset waits for a turn Stop already cancelled.
# - An off-record run shares only owner, generation and status with Redis:
#   unit test against a fake Redis, then against two real replicas.
# - The run addressing table is green with the untracked-turn rows removed.
# - Multi-replica harness and the backend suite (via the PR #1146 script).
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

fail() { echo "FAILED: $*"; exit 1; }

echo "== the untracked slot is gone"
if grep -n "active_chat_task" atlas/main.py; then fail "active_chat_task is still referenced in atlas/main.py"; fi
echo "PASSED: no active_chat_task in atlas/main.py"

echo "== lint"
ruff check atlas/tests/test_off_record_runs.py test/multireplica/test_off_record_turns.py \
    atlas/application/chat/runs atlas/application/chat/utilities/tool_executor.py || fail "ruff"

echo "== run table, off-record guarantee, plain-turn lifecycle"
(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q -p no:cacheprovider \
    tests/test_run_addressing_table.py tests/test_off_record_runs.py \
    tests/test_parallel_run_transport.py tests/test_auto_approve_background.py \
    tests/test_websocket_disconnect_resilience.py tests/test_websocket_run_directory_outage.py \
    tests/test_mcp_release_target_manager.py tests/test_session_resume_across_replicas.py) \
    || fail "unit tests"

echo "== multi-replica harness (includes the two-replica off-record test) + backend suite"
bash test/pr-validation/test_pr1146_multireplica_harness.sh || fail "multi-replica harness"
echo "PASSED: PR #1181 every chat turn is a run"
