#!/usr/bin/env bash
# Test script for PR #930: atlas_search is only available when the user selected it (issue #921)
#
# What this validates:
# - The implied-search-tool helper is gone: no code path adds atlas_search to
#   the schema because data sources were selected.
# - The agent loop makes a plain tools call with exactly the user's selection,
#   even when data sources are selected (nothing injected, nothing implied).
# - An explicitly selected atlas_search (or its legacy spelling) still reaches
#   the schema.
# - The orchestrator warns when a turn carries sources nothing can read, and
#   stays quiet when the sources are read (no tools -> RAG mode, only_rag,
#   legacy tool name, no config).
# - The frontend no longer blocks agent-mode sends with no tools.

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

echo "=== PR #930 Validation: atlas_search only when selected ==="

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"
cd "$PROJECT_ROOT"

# ==========================================
print_header "Check 1: the implied-search-tool helper is gone"
# ==========================================
if [ -f atlas/application/chat/utilities/search_tool_selection.py ] \
   || grep -rq --exclude-dir=__pycache__ "with_search_tool" atlas/application/ atlas/infrastructure/; then
    print_result 1 "search_tool_selection.py deleted, no call sites remain"
else
    print_result 0 "search_tool_selection.py deleted, no call sites remain"
fi

# ==========================================
print_header "Check 2: agent loop with sources makes a plain tools call -- nothing implied, nothing injected"
# ==========================================
python3 - <<'PY' 2>&1 | tail -3
import asyncio
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

tm = MagicMock()
tm.get_tools_schema = MagicMock(side_effect=lambda names, *a, **kw: [
    {'type': 'function', 'function': {'name': n, 'parameters': {}}} for n in names
])

loop = AgenticLoop(llm=LLM(), tool_manager=tm, prompt_provider=None, config_manager=None)
loop.skip_approval = True

ctx = AgentContext(session_id=uuid4(), user_email='u@example.com', files={}, history=MagicMock())

async def main():
    await loop.run(
        model='m', messages=[{'role': 'user', 'content': 'use search find the paid leave policy'}],
        context=ctx, selected_tools=['third_party_search'], data_sources=['srv:docs'],
        max_steps=3, temperature=0.7, event_handler=AsyncMock(),
    )

asyncio.run(main())

assert seen['messages'] == [{'role': 'user', 'content': 'use search find the paid leave policy'}], \
    f'context was injected: {seen["messages"]}'
names = [t['function']['name'] for t in seen['tools']]
assert names == ['third_party_search'], f'atlas_search was implied: {names}'
print('plain tools call; only the user-selected tool was offered')
PY
print_result ${PIPESTATUS[0]} "agent loop: sources never imply atlas_search"

# ==========================================
print_header "Check 3: an explicitly selected search tool still reaches the schema (legacy spelling too)"
# ==========================================
python3 - <<'PY' 2>&1 | tail -3
import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent.protocols import AgentContext

seen = {}

class LLM:
    async def call_with_tools(self, model, messages, tools_schema, *a, **kw):
        seen['tools'] = tools_schema
        from atlas.interfaces.llm import LLMResponse
        return LLMResponse(content='done')

tm = MagicMock()
tm.get_tools_schema = MagicMock(side_effect=lambda names, *a, **kw: [
    {'type': 'function', 'function': {'name': n, 'parameters': {}}} for n in names
])

loop = AgenticLoop(llm=LLM(), tool_manager=tm, prompt_provider=None, config_manager=None)
loop.skip_approval = True
ctx = AgentContext(session_id=uuid4(), user_email='u@example.com', files={}, history=MagicMock())

async def main():
    await loop.run(
        model='m', messages=[{'role': 'user', 'content': 'hi'}],
        context=ctx, selected_tools=['atlas_rag_query'], data_sources=['srv:docs'],
        max_steps=3, temperature=0.7, event_handler=AsyncMock(),
    )

asyncio.run(main())

names = [t['function']['name'] for t in seen['tools']]
assert names == ['atlas_rag_query'], names
print('the legacy-spelled search tool passes through untouched')
PY
print_result ${PIPESTATUS[0]} "explicit selection (incl. legacy name) is honored"

# ==========================================
print_header "Check 4: the orchestrator warns exactly when sources are stranded"
# ==========================================
python3 - <<'PY' 2>&1 | tail -4
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from atlas.application.chat.orchestrator import ChatOrchestrator

def orch():
    o = ChatOrchestrator.__new__(ChatOrchestrator)
    o.config_manager = SimpleNamespace(app_settings=SimpleNamespace(
        feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True))
    o.event_publisher = AsyncMock()
    return o

async def main():
    o = orch()
    await o._check_data_sources_reachable(['calc'], ['srv:docs'])
    o.event_publisher.publish_warning.assert_awaited_once()
    msg = o.event_publisher.publish_warning.await_args.kwargs['message']
    assert 'were not searched' in msg and 'atlas_search' in msg, msg
    print('sources + tools without the search tool warn')

    o2 = orch()
    await o2._check_data_sources_reachable(None, ['srv:docs'])
    await o2._check_data_sources_reachable(['calc'], ['srv:docs'], only_rag=True)
    await o2._check_data_sources_reachable(['atlas_rag_query'], ['srv:docs'])
    o2.event_publisher.publish_warning.assert_not_awaited()
    print('RAG-mode routes (no tools, only_rag) and legacy names stay silent')

asyncio.run(main())
PY
print_result ${PIPESTATUS[0]} "reachability warning covers only the stranded case"

# ==========================================
print_header "Check 5: atlas_search still executes through the normal tool path"
# ==========================================
python3 - <<'PY' 2>&1 | tail -3
import asyncio, json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from atlas.domain.messages.models import ToolCall
from atlas.modules.mcp_tools import mcp_execution

client = SimpleNamespace(config_manager=SimpleNamespace(app_settings=SimpleNamespace(
    feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True)))

mgr = mcp_execution.ExecutionMixin()

async def main():
    call = ToolCall(id='c1', name='atlas_search', arguments={'query': 'fleet'})
    with patch.object(mcp_execution, '_client', return_value=client), \
         patch.object(mcp_execution.ExecutionMixin, '_execute_atlas_rag_tool',
                      AsyncMock(return_value=SimpleNamespace(
                          tool_call_id='c1',
                          content='{"results": {"combined_answer": "a passage"}}',
                          success=True))) as ex:
        result = await mcp_execution.ExecutionMixin().execute_tool(call)
    assert result.success and 'a passage' in result.content, result
    print('atlas_search still dispatches to the RAG executor when selected')

asyncio.run(main())
PY
print_result ${PIPESTATUS[0]} "atlas_search execution unchanged"

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