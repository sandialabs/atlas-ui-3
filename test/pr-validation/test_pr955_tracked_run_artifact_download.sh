#!/bin/bash
# Test script for PR #955: keep run artifacts downloadable when every turn is a
# tracked run.
#
# Covers the PR's test plan:
#   1. A connection whose every turn is a tracked run gets a connection session.
#   2. A tracked run's artifacts are downloadable after the run session is reaped.
#   3. Two tracked runs in two conversations on one connection both come home.
#   4. Navigating away (New Chat / restore) still keeps a run's files out.
#   5. The turns this applies to are the ones the run eligibility rules admit.
#   6. Backend unit suite.
#
# The chat turn itself needs a model, so this drives the same helpers the
# WebSocket endpoint calls -- against a real ChatService, a real FileManager and
# the real session repository -- and then issues the real download the browser
# issues. No part of the path under test is stubbed.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

cd "$PROJECT_ROOT" || exit 1
source .venv/bin/activate

print_header "1-4. Tracked-run artifact download, end to end"

PYTHONPATH="$PROJECT_ROOT/atlas:$PROJECT_ROOT" python - <<'PY'
import asyncio
import base64
import sys
import uuid

from main import (
    _download_session_candidates,
    _merge_run_session_files,
    _release_finished_run,
    _resolve_download,
    _seed_run_session_files,
    forget_run_conversations,
)
from atlas.application.chat.service import ChatService
from atlas.application.chat.utilities import file_processor
from atlas.application.chat.runs import RunRegistry
from atlas.modules.file_storage.manager import FileManager
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient

USER = "pr955@example.com"


class _FakeLLM:
    async def call_plain(self, model_name, messages, temperature=0.7, **kwargs):
        return "ok"


class _ToolResult:
    def __init__(self, artifacts):
        self.artifacts = artifacts
        self.display_config = {}
        self.result = {}


async def _produce(service, session_id, name, body):
    """Run the real artifact ingest against the run's own session."""
    session = await service.session_repository.get(session_id)
    context = {
        "session_id": str(session_id),
        "user_email": USER,
        "files": dict(session.context.get("files", {})),
    }
    context = await file_processor.process_tool_artifacts(
        session_context=context,
        tool_result=_ToolResult([{
            "name": name,
            "b64": base64.b64encode(body).decode(),
            "mime": "application/octet-stream",
        }]),
        file_manager=service.file_manager,
        update_callback=None,
    )
    session.context.update({k: v for k, v in context.items() if k != "session_id"})


async def _download(service, registry, connection_session_id, name, conversation_id):
    data = {"conversation_id": conversation_id}
    return await _resolve_download(
        service,
        _download_session_candidates(registry, connection_session_id, USER, data),
        name,
        USER,
        None,
    )


async def main():
    failures = []

    def check(ok, label):
        print(("  ok   " if ok else "  FAIL ") + label)
        if not ok:
            failures.append(label)

    service = ChatService(
        llm=_FakeLLM(), file_manager=FileManager(s3_client=MockS3StorageClient())
    )
    registry = RunRegistry()
    conn_id = uuid.uuid4()

    # --- 1. the connection session does not exist yet, as on a socket whose
    # every turn is a tracked run.
    check(
        await service.session_repository.get(conn_id) is None,
        "connection session starts out absent",
    )

    run_a = registry.start(conversation_id="conv-a", user_email=USER)
    await _seed_run_session_files(
        service, conn_id, run_a.session_id, USER, conversation_id="conv-a"
    )
    check(
        await service.session_repository.get(conn_id) is not None,
        "starting a tracked run creates the connection session",
    )

    # --- 2. the run produces a file, then the run ends and its session goes.
    await _produce(service, run_a.session_id, "deck.pptx", b"PPTX-A")
    await _release_finished_run(
        service, registry, run_a.run_id, run_a.session_id, "conv-a", USER,
        connection_session_id=conn_id,
    )
    registry.remove(run_a.run_id)
    check(
        await service.session_repository.get(run_a.session_id) is None,
        "the run session is reaped when the run ends",
    )

    reply = await _download(service, registry, conn_id, "deck.pptx", "conv-a")
    check(
        not reply.get("error")
        and base64.b64decode(reply["content_base64"]) == b"PPTX-A",
        "the artifact downloads after its run session is reaped",
    )

    # --- 3. a second run, in a second conversation, on the same connection.
    run_b = registry.start(conversation_id="conv-b", user_email=USER)
    await _seed_run_session_files(
        service, conn_id, run_b.session_id, USER, conversation_id="conv-b"
    )
    await _produce(service, run_b.session_id, "chart.png", b"PNG-B")
    await _release_finished_run(
        service, registry, run_b.run_id, run_b.session_id, "conv-b", USER,
        connection_session_id=conn_id,
    )
    registry.remove(run_b.run_id)

    reply = await _download(service, registry, conn_id, "chart.png", "conv-b")
    check(
        not reply.get("error")
        and base64.b64decode(reply["content_base64"]) == b"PNG-B",
        "the second conversation's artifact downloads too",
    )
    reply = await _download(service, registry, conn_id, "deck.pptx", "conv-a")
    check(
        not reply.get("error"),
        "the first conversation's artifact was not displaced by the second",
    )

    # --- 4. navigating away must still keep a run's files out.
    run_c = registry.start(conversation_id="conv-c", user_email=USER)
    await _seed_run_session_files(
        service, conn_id, run_c.session_id, USER, conversation_id="conv-c"
    )
    await _produce(service, run_c.session_id, "secret.txt", b"C")
    connection = await service.session_repository.get(conn_id)
    forget_run_conversations(connection)
    connection.context["conversation_id"] = "somewhere-else"
    await _merge_run_session_files(service, run_c.session_id, conn_id, "conv-c")
    check(
        "secret.txt" not in connection.context.get("files", {}),
        "a run whose conversation the connection left does not merge",
    )

    if failures:
        print("FAILURES: " + "; ".join(failures))
        sys.exit(1)
    sys.exit(0)


asyncio.run(main())
PY
print_result $? "Tracked-run artifacts stay downloadable; conversations stay isolated"

print_header "5. Only eligible turns run in the background"

PYTHONPATH="$PROJECT_ROOT/atlas:$PROJECT_ROOT" python - <<'PY'
import sys
from atlas.application.chat.runs.eligibility import turn_is_eligible_for_background_run

base = dict(
    chat_history_enabled=True,
    save_mode="server",
    agent_mode=True,
    selected_tools=["calculator"],
    conversation_id="c1",
)
cases = [
    (base, True, "history on, server save, agent mode, tools"),
    ({**base, "chat_history_enabled": False}, False, "chat history off"),
    ({**base, "save_mode": "local"}, False, "local save mode"),
    ({**base, "agent_mode": False}, False, "not agent mode"),
    ({**base, "selected_tools": []}, False, "no tools"),
]
bad = [d for kw, want, d in cases if turn_is_eligible_for_background_run(**kw) is not want]
for d in bad:
    print("  FAIL " + d)
sys.exit(1 if bad else 0)
PY
print_result $? "Background-run eligibility gates match the documented rules"

print_header "6. Backend unit tests"

PYTEST_OUT="$(./test/run_tests.sh backend 2>&1)"
PYTEST_STATUS=$?
echo "$PYTEST_OUT" | tail -2
print_result "$PYTEST_STATUS" "Backend unit suite"

print_header "Summary"
echo -e "${GREEN}Passed: $PASSED${NC}"
echo -e "${RED}Failed: $FAILED${NC}"
[ "$FAILED" -eq 0 ]
