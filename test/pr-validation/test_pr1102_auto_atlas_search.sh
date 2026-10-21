#!/usr/bin/env bash
# Test script for PR #1102: selected data sources imply the built-in search tool.
#
# What this validates:
# - The orchestrator resolves a turn's tool list: a selection of data sources
#   adds `atlas_search` to it, whether or not the user ticked the tool, and
#   only while the deployment offers it.
# - The turn really reaches the model with the search tool in its schema (the
#   tool manager is asked for it), and the model decides whether to call it.
# - Deselecting every source removes the implication; an explicit selection of
#   the search tool survives it.
# - The "your data sources were not searched" warning is gone: the ordinary
#   case is silent, and only a genuinely unavailable search warns.
# - The feature flag still gates the tool, and the source-level classification
#   checks still refuse what the active level does not approve.
# - The tool panels show the automatic state and refuse to switch it off.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_header() { echo ""; echo "=========================================="; echo "$1"; echo "=========================================="; }
print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "  \033[0;32mPASSED\033[0m: $2"; PASSED=$((PASSED + 1))
    else
        echo -e "  \033[0;31mFAILED\033[0m: $2"; FAILED=$((FAILED + 1))
    fi
}

echo "=== PR #1102 Validation: selected data sources imply atlas_search ==="

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"
# The settings validator refuses a placeholder capability secret, and a
# developer .env may still hold one. The checks below never use it, so give
# them a valid one rather than failing on the environment.
export CAPABILITY_TOKEN_SECRET="${CAPABILITY_TOKEN_SECRET:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"
cd "$PROJECT_ROOT"

# ==========================================
print_header "Check 1: the tool list is resolved from the selection and the sources"
# ==========================================
python3 -c "
from types import SimpleNamespace
from atlas.application.chat.utilities.effective_tools import resolve_effective_tools

config = SimpleNamespace(
    feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True)
off = SimpleNamespace(
    feature_rag_enabled=True, feature_atlas_rag_tools_enabled=False)

assert resolve_effective_tools(['calc'], ['srv:docs'], config) == ['calc', 'atlas_search']
assert resolve_effective_tools(['calc'], [], config) == ['calc']
assert resolve_effective_tools(['atlas_search'], ['srv:docs'], config) == ['atlas_search']
assert resolve_effective_tools(['atlas_rag_query'], ['srv:docs'], config) == ['atlas_rag_query']
assert resolve_effective_tools(['calc'], ['srv:docs'], off) == ['calc']
assert resolve_effective_tools(['calc'], ['srv:docs'], None) == ['calc']
# The implication can be switched off (a model without tool support, or a
# policy hook that narrowed the tool list).
assert resolve_effective_tools(['calc'], ['srv:docs'], config, imply=False) == ['calc']
print('the resolution adds the tool exactly when the sources and the flags say so')
" 2>&1 | tail -3
print_result ${PIPESTATUS[0]} "effective tool resolution"

# ==========================================
print_header "Check 2: the model is offered the search tool for a sources-only turn"
# ==========================================
python3 -c "
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.domain.sessions.models import Session
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository

seen = {}

class Authorization:
    async def filter_authorized_tools(self, selected_tools, user_email=None):
        return list(selected_tools)

tools_mode = AsyncMock(return_value={'mode': 'tools'})

orch = ChatOrchestrator(
    llm=MagicMock(),
    event_publisher=AsyncMock(),
    session_repository=InMemorySessionRepository(),
    tools_mode=SimpleNamespace(run_streaming=tools_mode),
    config_manager=SimpleNamespace(app_settings=SimpleNamespace(
        feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True)),
)
orch.tool_authorization = Authorization()

async def main():
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email='u@example.com'))
    await orch.execute(
        session_id=sid,
        content='what is in the docs?',
        model='test-model',
        selected_tools=[],
        selected_data_sources=['srv:docs'],
        files=None,
    )

asyncio.run(main())

assert tools_mode.await_count == 1, 'the turn did not run in tools mode'
assert tools_mode.await_args.kwargs['selected_tools'] == ['atlas_search'], \
    tools_mode.await_args.kwargs['selected_tools']
assert orch.event_publisher.publish_warning.await_count == 0, 'the turn warned'
print('a sources-only turn is a tools turn with atlas_search in its schema')
" 2>&1 | tail -3
print_result ${PIPESTATUS[0]} "the search tool reaches the model without a manual tick"

# ==========================================
print_header "Check 2b: a model without tool support, and a policy hook, both keep it out"
# ==========================================
python3 -c "
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from atlas.application.chat.orchestrator import ChatOrchestrator
from atlas.domain.sessions.models import Session
from atlas.hooks import HookOutcome
from atlas.infrastructure.sessions.in_memory_repository import InMemorySessionRepository
from atlas.modules.config.models import LLMConfig

class Authorization:
    async def filter_authorized_tools(self, selected_tools, user_email=None):
        return list(selected_tools)

class NarrowingHook:
    def has_hooks(self, event):
        return True
    async def run_event(self, event, payload, session_context=None):
        return HookOutcome(verdict='modify', modified=True,
                           payload={**payload, 'selected_tools': []})

def build(tools_mode, rag_mode):
    orch = ChatOrchestrator(
        llm=MagicMock(),
        event_publisher=AsyncMock(),
        session_repository=InMemorySessionRepository(),
        tools_mode=SimpleNamespace(run_streaming=tools_mode),
        rag_mode=SimpleNamespace(run_streaming=rag_mode),
        config_manager=SimpleNamespace(app_settings=SimpleNamespace(
            feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True)),
    )
    orch.tool_authorization = Authorization()
    return orch

async def main():
    # A model that cannot call tools: the tools are stripped, so the implied
    # search tool must not be added back -- the turn keeps its sources.
    tools_mode = AsyncMock(return_value={'mode': 'tools'})
    rag_mode = AsyncMock(return_value={'mode': 'rag'})
    orch = build(tools_mode, rag_mode)
    orch.config_manager.llm_config = LLMConfig(models={
        'plain-model': {'model_name': 'p', 'model_url': 'http://p', 'supports_tools': False}})
    sid = uuid4()
    await orch.session_repository.create(Session(id=sid, user_email='u@example.com'))
    await orch.execute(session_id=sid, content='q', model='plain-model',
                       selected_tools=['calc'], selected_data_sources=['srv:docs'],
                       files=None)
    assert not tools_mode.await_count, 'a model without tool support got a tool schema'
    assert rag_mode.await_count == 1, rag_mode.await_args

    # A policy hook that narrows the tool list has the last word over it.
    tools_mode = AsyncMock(return_value={'mode': 'tools'})
    rag_mode = AsyncMock(return_value={'mode': 'rag'})
    orch = build(tools_mode, rag_mode)
    orch.__class__  # noqa: B018 - keep the reference obvious
    import atlas.application.chat.orchestrator as orch_mod
    original = orch_mod.get_hook_manager
    orch_mod.get_hook_manager = lambda: NarrowingHook()
    try:
        sid = uuid4()
        await orch.session_repository.create(Session(id=sid, user_email='u@example.com'))
        await orch.execute(session_id=sid, content='q', model='test-model',
                           selected_tools=['atlas_search', 'calc'],
                           selected_data_sources=['srv:docs'], files=None)
    finally:
        orch_mod.get_hook_manager = original
    assert not tools_mode.await_count, 'the hook-narrowed list still reached the runner'

asyncio.run(main())
print('neither a tool-less model nor a policy hook gets the implied tool behind its back')
" 2>&1 | tail -3
print_result ${PIPESTATUS[0]} "the implication yields to a tool-less model and to a policy hook"

# ==========================================
print_header "Check 3: the old warning is gone; only a real outage warns"
# ==========================================
python3 -c "
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from atlas.application.chat.orchestrator import ChatOrchestrator

def orch(tools=True):
    o = ChatOrchestrator.__new__(ChatOrchestrator)
    o.config_manager = SimpleNamespace(app_settings=SimpleNamespace(
        feature_rag_enabled=True, feature_atlas_rag_tools_enabled=tools))
    o.event_publisher = AsyncMock()
    return o

async def main():
    o = orch()
    await o._warn_if_sources_unsearchable(['calc', 'atlas_search'], ['srv:docs'])
    o.event_publisher.publish_warning.assert_not_awaited()

    o2 = orch(tools=False)
    await o2._warn_if_sources_unsearchable(['calc'], ['srv:docs'])
    o2.event_publisher.publish_warning.assert_awaited_once()
    msg = o2.event_publisher.publish_warning.await_args.kwargs['message']
    assert 'could not be searched' in msg, msg
    assert 'were not searched' not in msg, msg

asyncio.run(main())
print('the ordinary case is silent; a disabled search warns once, accurately')
" 2>&1 | tail -3
print_result ${PIPESTATUS[0]} "the stranded-sources warning only fires when search cannot run"

# ==========================================
print_header "Check 4: the search tool stays inside the classification boundary"
# ==========================================
python3 -c "
from types import SimpleNamespace
from atlas.application.chat.policies.classification_policy import find_classification_violations
from atlas.application.chat.utilities.effective_tools import resolve_effective_tools
from atlas.core.compliance import ComplianceLevelManager
from atlas.modules.config.models import LLMConfig, MCPConfig, RAGSourcesConfig
import json, tempfile, os
from pathlib import Path

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'compliance-levels.json'
    path.write_text(json.dumps({'levels': [
        {'name': 'UUR', 'aliases': ['Unclassified'], 'allowed_with': ['UUR']},
        {'name': 'ITAR', 'allowed_with': ['ITAR', 'UUR']},
    ]}))
    manager = ComplianceLevelManager(path)

config_manager = SimpleNamespace(
    llm_config=LLMConfig(models={'model-x': {'model_name': 'x', 'model_url': 'http://x',
                                              'allowed_data_classifications': ['UUR', 'ITAR']}}),
    mcp_config=MCPConfig(servers={}),
    rag_sources_config=RAGSourcesConfig(sources={
        'uur_docs': {'type': 'http', 'url': 'http://r', 'allowed_data_classifications': ['UUR']}}),
)
config = SimpleNamespace(
    feature_rag_enabled=True, feature_atlas_rag_tools_enabled=True)

tools = resolve_effective_tools(['atlas_canvas'], ['uur_docs:handbook'], config)
violations = find_classification_violations(
    manager, 'ITAR', model='model-x', config_manager=config_manager,
    tool_manager=None, selected_tools=tools,
    selected_data_sources=['uur_docs:handbook'])
assert violations == ['data source uur_docs'], violations
print('the implied tool adds no component of its own; the source is still refused')
" 2>&1 | tail -3
print_result ${PIPESTATUS[0]} "classification enforcement is unchanged"

# ==========================================
print_header "Check 5: the tool panels show the automatic state"
# ==========================================
FE_OUT="$(cd frontend && npx vitest run src/test/search-tool-availability.test.js \
    src/test/search-tool-auto-enable.test.jsx src/test/agent-mode-no-tools-banner.test.jsx 2>&1)"
FE_RC=$?
echo "$FE_OUT" | grep -E 'Test Files|Tests ' | tail -2
print_result $FE_RC "frontend unit tests for the tool panels"

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
