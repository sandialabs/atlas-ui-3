#!/bin/bash
# Test script for PR #652: Rewind / edit a previous prompt (issue #142)
#
# Test plan:
# - ConversationHistory.truncate_at_user_index drops the Nth user message and
#   everything after it, counting user messages only (ignoring tool/system rows)
# - ChatOrchestrator.execute(rewind_to_user_index=N) truncates before appending
#   the new prompt (overwrite-in-place); out-of-range index is a safe no-op
# - End-to-end: a realistic multi-turn conversation rewound to an earlier prompt
#   collapses to a single linear thread with the edited prompt in place
# - The rewind field is wired through the DTO, WebSocket handler, and frontend
# - Frontend edit affordance behaves (pencil -> editor -> onRewind)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"

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

print_header "PR #652: Rewind / edit a previous prompt"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"

# ==========================================
# 1. Domain truncation unit suite
# ==========================================
print_header "1. ConversationHistory.truncate_at_user_index"

cd "$ATLAS_DIR" && python3 -m pytest \
    tests/test_conversation_history_rewind.py \
    -v --tb=short 2>&1
print_result $? "Domain truncation tests pass"

# ==========================================
# 2. Orchestrator rewind suite
# ==========================================
print_header "2. ChatOrchestrator rewind routing"

cd "$ATLAS_DIR" && python3 -m pytest \
    tests/test_orchestrator_rewind.py \
    -v --tb=short 2>&1
print_result $? "Orchestrator rewind tests pass"

# ==========================================
# 3. End-to-end: multi-turn conversation rewound in place
# ==========================================
print_header "3. End-to-end rewind through the real orchestrator"

cd "$PROJECT_ROOT" && python3 - <<'PYEOF'
import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository


def build_orchestrator(repo):
    # Mode runners stand in for the real LLM call; everything else (history,
    # truncation, session repo) is the production code path.
    plain = MagicMock(); plain.run_streaming = AsyncMock(return_value={"mode": "plain"})
    rag = MagicMock(); rag.run_streaming = AsyncMock(return_value={"mode": "rag"})
    tools = MagicMock(); tools.run_streaming = AsyncMock(return_value={"mode": "tools"})
    agent = MagicMock(); agent.run = AsyncMock(return_value={"mode": "agent"})
    return ChatOrchestrator(
        llm=MagicMock(), event_publisher=MagicMock(), session_repository=repo,
        plain_mode=plain, rag_mode=rag, tools_mode=tools, agent_mode=agent,
    )


async def main():
    repo = InMemorySessionRepository()
    sid = uuid.uuid4()
    session = Session(id=sid, user_email="test@example.com")
    await repo.create(session)
    orch = build_orchestrator(repo)

    # Three real user turns; simulate the assistant reply the LLM would add.
    for i, q in enumerate(["what is python", "and ruby", "and go"]):
        await orch.execute(session_id=sid, content=q, model="m")
        session.history.add_message(
            Message(role=MessageRole.ASSISTANT, content=f"answer {i}")
        )

    before = [(m.role.value, m.content) for m in session.history.messages]
    assert len(before) == 6, before
    user_count = sum(1 for m in session.history.messages if m.role == MessageRole.USER)
    assert user_count == 3, before

    # User rewinds to the 2nd prompt (index 1) and edits it.
    await orch.execute(
        session_id=sid, content="and rust instead", model="m",
        rewind_to_user_index=1,
    )

    after = [(m.role.value, m.content) for m in session.history.messages]
    expected = [
        ("user", "what is python"),
        ("assistant", "answer 0"),
        ("user", "and rust instead"),
    ]
    assert after == expected, after
    print("Transcript after rewind:")
    for role, content in after:
        print(f"  {role}: {content}")
    print("END-TO-END OK")


asyncio.run(main())
PYEOF
print_result $? "Multi-turn conversation rewinds to an earlier edited prompt (overwrite-in-place)"

# ==========================================
# 4. Source-level wiring checks
# ==========================================
print_header "4. Rewind field is wired front-to-back"

grep -nE "rewind_to_user_index: Optional\[int\] = None" \
    "$ATLAS_DIR/domain/chat/dtos.py" >/dev/null
print_result $? "ChatRequest carries rewind_to_user_index"

grep -nE "def truncate_at_user_index" \
    "$ATLAS_DIR/domain/messages/models.py" >/dev/null
print_result $? "ConversationHistory.truncate_at_user_index exists"

grep -nE "session\.history\.truncate_at_user_index\(rewind_to_user_index\)" \
    "$ATLAS_DIR/application/chat/orchestrator.py" >/dev/null
print_result $? "Orchestrator truncates on rewind before appending the new prompt"

grep -nE "rewind_to_user_index=data\.get\(\"rewind_to_user_index\"\)" \
    "$ATLAS_DIR/main.py" >/dev/null
print_result $? "WebSocket chat handler reads rewind_to_user_index"

grep -nE "rewindAndResubmit" \
    "$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx" >/dev/null
print_result $? "Frontend exposes rewindAndResubmit"

grep -nE "rewind_to_user_index: rewindToUserIndex" \
    "$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx" >/dev/null
print_result $? "Frontend sends rewind_to_user_index in the chat payload"

# ==========================================
# 5. Frontend edit-affordance test
# ==========================================
print_header "5. Frontend edit affordance"

if [ -d "$PROJECT_ROOT/frontend/node_modules" ]; then
    cd "$PROJECT_ROOT/frontend" && npx vitest run src/test/message-rewind-edit.test.jsx 2>&1
    print_result $? "Message edit/rewind component test passes"
else
    echo "SKIP: frontend/node_modules not installed (run 'npm install' in frontend/)"
fi

# ==========================================
# Summary
# ==========================================
print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}SOME TESTS FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}ALL TESTS PASSED${NC}"
    exit 0
fi
