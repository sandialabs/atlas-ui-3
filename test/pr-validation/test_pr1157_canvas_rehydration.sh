#!/bin/bash
# Test script for PR #1157: canvas documents survive conversation rehydration.
# Exercises the recorder, a DuckDB save/load, and the model-visible fold,
# then the targeted unit tests and the backend suite.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

python - <<'PY'
"""Round-trip a canvas write through DuckDB and the next-turn prompt."""
import asyncio
import tempfile
from pathlib import Path

import atlas
from atlas.application.chat.utilities.conversation_loader import load_messages_into_history
from atlas.application.chat.utilities.tool_history import ToolCallRecorder
from atlas.domain.messages.models import ConversationHistory, Message, MessageRole
from atlas.modules.chat_history import ConversationRepository, get_session_factory, init_database
from atlas.modules.chat_history.database import reset_engine

root = Path(".").resolve()
atlas_file = Path(atlas.__file__).resolve()
if root not in atlas_file.parents:
    raise SystemExit(f"imported atlas from {atlas_file}, expected {root}")

script = "print('hello')\nvalue = 1\nCANVAS>>>\nprint('bye')\n" + ("# pad\n" * 2000)

async def play(recorder):
    await recorder({
        "type": "tool_start",
        "tool_call_id": "c1",
        "tool_name": "atlas_canvas",
        "server_name": "atlas",
        "arguments": {"content": script},
    })
    await recorder({
        "type": "canvas_content",
        "tool_call_id": "c1",
        "content": script,
    })
    await recorder({
        "type": "tool_complete",
        "tool_call_id": "c1",
        "tool_name": "atlas_canvas",
        "success": True,
        "result": "Canvas content displayed: " + script[:100] + "...",
    })

recorder = ToolCallRecorder(None)
asyncio.run(play(recorder))
if recorder.messages() != []:
    raise SystemExit("canvas tool was persisted as a transcript tool row")

history = ConversationHistory()
history.add_message(Message(role=MessageRole.USER, content="write a script"))
recorder.flush(history)
history.add_message(Message(role=MessageRole.ASSISTANT, content="Wrote it to the canvas."))
messages = []
for msg in history.messages:
    payload = msg.to_dict()
    payload["message_type"] = msg.metadata.get("message_type", "chat")
    messages.append(payload)
artifact = next(m for m in messages if m["message_type"] == "canvas_artifact")
if artifact["metadata"]["canvas_content"] != script:
    raise SystemExit("flushed canvas text does not match the original")

reset_engine()
with tempfile.TemporaryDirectory() as tmp:
    init_database(f"duckdb:///{tmp}/canvas.db")
    repo = ConversationRepository(get_session_factory())
    repo.save_conversation(
        conversation_id="conv-1157",
        user_email="user@test.com",
        title="canvas",
        model="test-model",
        messages=messages,
    )
    loaded = repo.get_conversation("conv-1157", "user@test.com")
reset_engine()

stored = next(m for m in loaded["messages"] if m["message_type"] == "canvas_artifact")
if stored["metadata"]["canvas_content"] != script:
    raise SystemExit("reloaded canvas text does not match the original")

restored = ConversationHistory()
load_messages_into_history(restored, loaded["messages"], "conv-1157")
llm = restored.get_messages_for_llm()
joined = "\n".join(m["content"] for m in llm)
if "print('hello')" not in joined or "print('bye')" not in joined:
    raise SystemExit("next turn cannot see the original canvas text")
if ">CANVAS>>>" not in joined:
    raise SystemExit("a canvas closer line was not guarded in the model view")
if any(m["role"] == "tool" for m in llm):
    raise SystemExit("canvas artifact was replayed as a tool turn")
print("CANVAS_ROUNDTRIP_OK")
PY
print_result $? "DuckDB canvas round-trip and next-turn fold"

python -m pytest atlas/tests/test_canvas_rehydration.py atlas/tests/test_tool_call_persistence.py -q --tb=short
print_result $? "Canvas and tool-persistence unit tests"

./test/run_tests.sh backend
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
