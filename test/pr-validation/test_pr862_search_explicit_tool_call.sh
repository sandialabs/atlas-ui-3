#!/usr/bin/env bash
# Test script for PR #862: atlas_search is an explicit tool call, not silent RAG injection
#
# What this validates:
# - The RAG pre-injection path is gone from the LLM callers and the interface.
# - Agent mode and tools mode make a plain tools call even when data sources are
#   selected: the model sees exactly the messages it was given, with no
#   "Retrieved context from ..." system turn inserted ahead of it.
# - A data source selection makes `atlas_search` available (and is resolved in
#   the orchestrator, before the agent-mode guard).
# - With the built-in search tool disabled, a tools turn with sources warns the
#   user instead of silently answering without their evidence.
# - `atlas_search` still executes end to end and returns passages as a tool result.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_header() { echo ""; echo "=== $1 ==="; }
print_result() {
    if [ "$1" -eq 0 ]; then
        echo "  PASSED: $2"; PASSED=$((PASSED + 1))
    else
        echo "  FAILED: $2"; FAILED=$((FAILED + 1))
    fi
}

echo "=== PR #862 Validation: explicit atlas_search tool call ==="

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"
cd "$PROJECT_ROOT"

# ==========================================
print_header "Check 1: the silent RAG+tools path is gone"
# ==========================================
if grep -q "rag_and_tools" atlas/modules/llm/litellm_caller.py \
   || grep -q "rag_and_tools" atlas/modules/llm/litellm_streaming.py \
   || grep -q "rag_and_tools" atlas/interfaces/llm.py; then
    print_result 1 "call_with_rag_and_tools / stream_with_rag_and_tools are deleted"
else
    print_result 0 "call_with_rag_and_tools / stream_with_rag_and_tools are deleted"
fi

# ==========================================
print_header "Check 2: RAG mode (no tools) still injects -- it has no tool to call"
# ==========================================
grep -q "async def call_with_rag" atlas/modules/llm/litellm_caller.py \
    && grep -q "async def stream_with_rag" atlas/modules/llm/litellm_streaming.py
print_result $? "call_with_rag / stream_with_rag are untouched"

# ==========================================
print_header "Check 3: the agent loop makes a plain tools call with sources selected"
# ==========================================
python3 -c "
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent.protocols import AgentContext
from atlas.interfaces.llm import LLMResponse

seen = {}

class LLM:
    async def call_with_tools(self, model, messages, tools_schema, *a, **kw):
        seen['messages'] = messages
        seen['tools'] = tools_schema
        return LLMResponse(content='answered without searching')
    async def call_with_rag_and_tools(self, *a, **kw):
        raise AssertionError('the silent RAG path was used')

tm = MagicMock()
tm.get_tools_schema = MagicMock(side_effect=lambda names: [
    {'type': 'function', 'function': {'name': n, 'parameters': {}}} for n in names
])

config = SimpleNamespace(app_settings=SimpleNamespace(
    feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True))

loop = AgenticLoop(llm=LLM(), tool_manager=tm, prompt_provider=None, config_manager=config)
loop.skip_approval = True

ctx = AgentContext(session_id=uuid4(), user_email='u@example.com', files={}, history=MagicMock())

async def main():
    await loop.run(
        model='m', messages=[{'role': 'user', 'content': 'what is in the docs?'}],
        context=ctx, selected_tools=['calc'], data_sources=['srv:docs'],
        max_steps=3, temperature=0.7, event_handler=AsyncMock(),
    )

asyncio.run(main())

assert seen['messages'] == [{'role': 'user', 'content': 'what is in the docs?'}], \
    f'context was injected: {seen[\"messages\"]}'
names = [t['function']['name'] for t in seen['tools']]
assert names == ['calc', 'atlas_search'], names
print('plain tools call; atlas_search offered; nothing injected')
" 2>&1 | tail -3
print_result ${PIPESTATUS[0]} "agent mode: no pre-injection, atlas_search offered"

# ==========================================
print_header "Check 4: the implied tool is resolved before the agent-mode guard"
# ==========================================
python3 -c "
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from atlas.application.chat.orchestrator import ChatOrchestrator

def orch(tools_flag):
    o = ChatOrchestrator.__new__(ChatOrchestrator)
    o.config_manager = SimpleNamespace(app_settings=SimpleNamespace(
        feature_rag_enabled=True, feature_atlas_rag_tools_enabled=tools_flag))
    o.event_publisher = AsyncMock()
    return o

async def main():
    o = orch(True)
    resolved = await o._resolve_search_tool(None, ['srv:docs'])
    assert resolved == ['atlas_search'], resolved
    o.event_publisher.publish_warning.assert_not_awaited()
    print('sources-only agent turn keeps a tool to act on')

    # Search disabled + tools selected: warn, do not silently skip the evidence.
    o2 = orch(False)
    resolved = await o2._resolve_search_tool(['calc'], ['srv:docs'])
    assert resolved == ['calc'], resolved
    o2.event_publisher.publish_warning.assert_awaited_once()
    msg = o2.event_publisher.publish_warning.await_args.kwargs['message']
    assert 'were not searched' in msg, msg
    print('disabled search tool warns instead of answering silently')

asyncio.run(main())
" 2>&1 | tail -3
print_result ${PIPESTATUS[0]} "orchestrator resolves the implied tool and warns when it cannot"

# ==========================================
print_header "Check 5: atlas_search executes and returns passages as a tool result"
# ==========================================
python3 -c "
import asyncio, json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools import mcp_execution

client = SimpleNamespace(config_manager=SimpleNamespace(app_settings=SimpleNamespace(
    feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True)))

mgr = mcp_execution.ExecutionMixin()

async def main():
    call = ToolCall(id='c1', name='atlas_search', arguments={'query': 'fleet'})
    with patch.object(mcp_execution, '_client', return_value=client), \
         patch.object(mgr, '_execute_atlas_rag_tool',
                      AsyncMock(return_value=SimpleNamespace(
                          tool_call_id='c1',
                          content=json.dumps({'results': {'combined_answer': 'a passage'}}),
                          success=True))) as ex:
        result = await mgr.execute_tool(call)
    ex.assert_awaited_once()
    assert result.success and 'a passage' in result.content, result
    print('atlas_search dispatches to the RAG executor and returns its passages')

asyncio.run(main())
" 2>&1 | tail -3
print_result ${PIPESTATUS[0]} "atlas_search dispatches through the normal tool path"

# ==========================================
print_header "Check 6: backend unit tests"
# ==========================================
./test/run_tests.sh backend >/dev/null 2>&1
print_result $? "backend test suite passes"

# ==========================================
print_header "Summary"
# ==========================================
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"
if [ "$FAILED" -gt 0 ]; then
    echo "RESULT: FAILED"; exit 1
fi
echo "RESULT: PASSED"; exit 0
