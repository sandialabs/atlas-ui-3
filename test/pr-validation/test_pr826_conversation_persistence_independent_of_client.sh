#!/bin/bash
# Test script for PR #826: Make conversation saving independent of the client
#
# Test plan:
# - Reconnect end-to-end: a turn arriving on a *fresh* session carrying the old
#   conversation_id rehydrates from the real database, and the save that follows
#   keeps the whole thread instead of replacing it with the two new messages
# - The repository's no-shrink guard refuses a write that would shorten a stored
#   conversation, and lets a rewind through
# - Rewind: the shrink exemption is earned by an actual truncation, not by the
#   presence of the client-supplied rewind_to_user_index field
# - Restore and rehydrate share one loader, so a rehydrated conversation is
#   saved back in the same shape a restored one is
# - Unit suites for the loader, the guard, and the service hydration path,
#   including the two paths driven end-to-end through a real DuckDB-backed
#   ConversationRepository: a failed read is retried on the next turn, and
#   resuming after an incognito interlude branches into a new conversation
#   rather than replacing the one it was opened from

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

print_header "PR #826: conversation saving independent of the client"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"
cd "$PROJECT_ROOT" || exit 1

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

# ==========================================
# 1. End-to-end reconnect against a real DuckDB file
# ==========================================
print_header "1. Reconnect: fresh session, old conversation_id, real database"

# Point the global chat-history URL at the scratch file too: some components
# reached during a turn open the configured database themselves, and this
# script must not touch the operator's real one.
DB_PATH="$WORKDIR/chat_history.db" \
CHAT_HISTORY_DB_URL="duckdb:///$WORKDIR/chat_history.db" python3 - <<'PYEOF'
import asyncio
import os
import sys
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from atlas.application.chat.service import ChatService
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
from atlas.modules.chat_history.conversation_repository import ConversationRepository
from atlas.modules.chat_history.database import get_session_factory, init_database, reset_engine

USER = "reconnect@example.com"
CONV = "conv-reconnect"

reset_engine()
init_database(f"duckdb:///{os.environ['DB_PATH']}")
conversations = ConversationRepository(get_session_factory())


def make_service(session_repo):
    return ChatService(
        llm=MagicMock(),
        tool_manager=MagicMock(),
        connection=MagicMock(),
        config_manager=MagicMock(),
        session_repository=session_repo,
        conversation_repository=conversations,
    )


async def turn(service, session_id, content, **kwargs):
    """One chat turn with the LLM stubbed; everything else is production code."""
    orchestrator = MagicMock()

    async def _execute(**_ignored):
        session = await service.session_repository.get(session_id)
        session.history.add_message(Message(role=MessageRole.USER, content=content))
        session.history.add_message(
            Message(role=MessageRole.ASSISTANT, content=f"reply to {content}")
        )
        return {"type": "done"}

    orchestrator.execute = AsyncMock(side_effect=_execute)
    with patch.object(service, "_get_orchestrator", return_value=orchestrator):
        await service.handle_chat_message(
            session_id=session_id,
            content=content,
            model="test-model",
            user_email=USER,
            conversation_id=CONV,
            **kwargs,
        )


async def main():
    # --- connection 1: a long conversation, saved as it goes ---
    repo = InMemorySessionRepository()
    service = make_service(repo)
    first_session = uuid4()
    await repo.create(Session(id=first_session, user_email=USER))
    for i in range(25):
        await turn(service, first_session, f"question {i}")

    stored = conversations.get_conversation(CONV, USER)
    assert stored and len(stored["messages"]) == 50, stored and len(stored["messages"])
    original_title = stored["title"]
    print(f"  connection 1 persisted {len(stored['messages'])} messages")

    # --- the socket drops; the browser keeps its conversation_id ---
    # A reconnect mints a brand-new session_id with an empty history. Before
    # this PR the next turn saved 2 messages over the 50.
    second_session = uuid4()
    await repo.create(Session(id=second_session, user_email=USER))
    await turn(service, second_session, "after the reconnect")

    session = await repo.get(second_session)
    if len(session.history.messages) < 50:
        print(f"  FAIL: session was not rehydrated ({len(session.history.messages)} messages)")
        sys.exit(1)

    stored = conversations.get_conversation(CONV, USER)
    if len(stored["messages"]) != 52:
        print(f"  FAIL: stored conversation is {len(stored['messages'])} messages, expected 52")
        sys.exit(1)
    if stored["messages"][0]["content"] != "question 0":
        print("  FAIL: the original opening message was lost")
        sys.exit(1)
    if stored["title"] != original_title:
        # A rehydrated session is marked restored, so the title still belongs to
        # the conversation's first prompt rather than being regenerated from
        # whatever the user happened to say after reconnecting.
        print(f"  FAIL: the conversation was renamed to {stored['title']!r}")
        sys.exit(1)
    print(f"  after reconnect: {len(stored['messages'])} messages, thread intact")


asyncio.run(main())
reset_engine()
PYEOF
print_result $? "A turn on a fresh session rehydrates and saves the whole thread"

# ==========================================
# 2. The repository guard, against a real database
# ==========================================
print_header "2. No-shrink guard in ConversationRepository.save_conversation"

DB_PATH="$WORKDIR/guard.db" \
CHAT_HISTORY_DB_URL="duckdb:///$WORKDIR/guard.db" python3 - <<'PYEOF'
import os
import sys

from atlas.modules.chat_history.conversation_repository import ConversationRepository
from atlas.modules.chat_history.database import get_session_factory, init_database, reset_engine

USER = "guard@example.com"
CONV = "conv-guard"

reset_engine()
init_database(f"duckdb:///{os.environ['DB_PATH']}")
repo = ConversationRepository(get_session_factory())


def messages(n, prefix="m"):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"{prefix} {i}"}
        for i in range(n)
    ]


repo.save_conversation(CONV, USER, "Original title", "model", messages(50))
assert len(repo.get_conversation(CONV, USER)["messages"]) == 50

# The regression: a partial history writing over the stored record.
result = repo.save_conversation(CONV, USER, "New title", "model", messages(2, "partial"))
if result is not None:
    print("  FAIL: a 2-message write over a 50-message conversation was accepted")
    sys.exit(1)

after = repo.get_conversation(CONV, USER)
if len(after["messages"]) != 50 or after["title"] != "Original title":
    print(f"  FAIL: conversation was damaged ({len(after['messages'])} messages, title {after['title']!r})")
    sys.exit(1)
print("  refused a shrinking write; 50 messages and the title survive")

# Rewind is the legitimate shrink.
if repo.save_conversation(CONV, USER, None, "model", messages(10, "rewound"), allow_shrink=True) is None:
    print("  FAIL: a rewind was refused")
    sys.exit(1)
if len(repo.get_conversation(CONV, USER)["messages"]) != 10:
    print("  FAIL: the rewind did not take effect")
    sys.exit(1)
print("  allowed the rewind: conversation is now 10 messages")

reset_engine()
PYEOF
print_result $? "A shorter write is refused; a rewind is allowed"

# ==========================================
# 3. The shrink exemption is earned, not requested
# ==========================================
print_header "3. rewind_to_user_index alone does not unlock the guard"

python3 - <<'PYEOF'
import asyncio
import sys
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository


async def run(rewind_index):
    repo = InMemorySessionRepository()
    session_id = uuid4()
    session = Session(id=session_id, user_email="rewind@example.com")
    for i in range(3):
        session.history.add_message(Message(role=MessageRole.USER, content=f"q{i}"))
        session.history.add_message(Message(role=MessageRole.ASSISTANT, content=f"a{i}"))
    await repo.create(session)

    mode = MagicMock()
    mode.run_streaming = AsyncMock(return_value={})
    agent = MagicMock()
    agent.run = AsyncMock(return_value={})
    orchestrator = ChatOrchestrator(
        llm=MagicMock(), event_publisher=MagicMock(), session_repository=repo,
        plain_mode=mode, rag_mode=mode, tools_mode=mode, agent_mode=agent,
    )
    await orchestrator.execute(
        session_id=session_id, content="edited", model="test-model",
        rewind_to_user_index=rewind_index,
    )
    return session.context.get("rewind_removed", False)


async def main():
    if await run(1) is not True:
        print("  FAIL: a real rewind did not record its truncation")
        sys.exit(1)
    print("  a rewind that removed messages records the exemption")

    for bad in (99, "not-an-index", None):
        if await run(bad):
            print(f"  FAIL: rewind_to_user_index={bad!r} truncated nothing yet earned the exemption")
            sys.exit(1)
    print("  out-of-range, malformed, and absent indexes earn nothing")


asyncio.run(main())
PYEOF
print_result $? "The exemption follows an actual truncation"

# ==========================================
# 4. Unit suites
# ==========================================
print_header "4. Unit coverage"

python3 -m pytest atlas/tests/test_conversation_rehydration.py -q 2>&1 | tail -5
print_result ${PIPESTATUS[0]} "Rehydration, loader, and guard unit tests"

python3 -m pytest atlas/tests/test_chat_history.py atlas/tests/test_orchestrator_rewind.py -q 2>&1 | tail -5
print_result ${PIPESTATUS[0]} "Chat history and rewind suites still pass"

# ==========================================
# 5. Full backend suite
# ==========================================
print_header "5. Backend unit tests"

./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
