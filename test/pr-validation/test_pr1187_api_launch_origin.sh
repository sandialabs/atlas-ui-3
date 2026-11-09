#!/bin/bash
# Test script for the API launch-origin indicator (issue #1187)
#
# Test plan:
# - An Agent Runs API launch records launch_origin in the conversation's metadata.
# - Every history read (list, search, get, export) publishes it; ordinary and
#   sub-conversations publish null, and the parent link is unaffected.
# - The origin is fixed at creation: a browser turn keeps it, a later save cannot add it.
# - A real launch over HTTP hands the origin to the turn and reports it in the run status.
# - The sidebar renders the AUTO tag for saved and in-flight rows, next to the tree markers.
# - Two real replicas on shared Redis and PostgreSQL still pass the coordination harness.
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

fail() { echo "FAILED: $*"; exit 1; }

echo "== durable origin: service save, repository reads, REST launch"
out=$(cd atlas && PYTHONPATH="$PROJECT_ROOT" python -m pytest -q -p no:cacheprovider \
    tests/test_conversation_origin.py tests/test_conversation_lineage.py \
    tests/test_agent_runs_api.py 2>&1) || { echo "$out" | tail -20; fail "backend tests"; }
echo "$out" | tail -2

echo "== end to end: a saved API conversation is listed with its origin after a fresh open"
db_dir=$(mktemp -d)
trap 'rm -rf "$db_dir"' EXIT
PYTHONPATH="$PROJECT_ROOT" python - "$db_dir/origin.db" <<'PY' || fail "end-to-end origin check"
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from atlas.application.chat.runs.external_runs import API_SOURCE
from atlas.application.chat.service import ChatService
from atlas.domain.messages.models import Message, MessageRole
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine

USER = "user@test.com"
url = f"duckdb:///{sys.argv[1]}"


async def turn(service, conversation_id, **extra):
    session_id = uuid4()

    async def execute(**_):
        session = await service.session_repository.get(session_id)
        session.history.add_message(Message(role=MessageRole.USER, content="report"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content="done"))
        return {"type": "done"}

    orchestrator = MagicMock()
    orchestrator.execute = AsyncMock(side_effect=execute)
    with patch.object(service, "_get_orchestrator", return_value=orchestrator):
        await service.handle_chat_message(
            session_id=session_id, content="report", model="gpt-4", user_email=USER,
            conversation_id=conversation_id, **extra,
        )


async def main():
    init_database(url)
    service = ChatService(llm=MagicMock(), conversation_repository=ConversationRepository(get_session_factory()))
    await turn(service, "api-conv", launch_origin=API_SOURCE)
    await turn(service, "plain-conv")
    await turn(service, "api-conv")  # the user continues it in the browser

    # A new process would see only the database: drop the engine and reopen.
    reset_engine()
    init_database(url)
    rows = {r["id"]: r for r in ConversationRepository(get_session_factory()).list_conversations(USER)}
    assert rows["api-conv"]["launch_origin"] == "agent_runs_api", rows["api-conv"]
    assert rows["api-conv"]["message_count"] == 4, rows["api-conv"]
    assert rows["plain-conv"]["launch_origin"] is None, rows["plain-conv"]
    print("listed after reopen:", {k: v["launch_origin"] for k, v in rows.items()})


asyncio.run(main())
PY

echo "== sidebar rendering"
(cd frontend && npx vitest run src/test/conversation-launch-origin.test.jsx \
    src/test/sidebar-conversation-tree.test.jsx src/test/conversation-tree.test.js) || fail "vitest"

echo "== multi-replica harness (two real replicas on shared Redis and PostgreSQL)"
bash test/multireplica_tests.sh || fail "multi-replica harness"
echo "PASSED: API-launched conversations keep a durable, visible origin"
