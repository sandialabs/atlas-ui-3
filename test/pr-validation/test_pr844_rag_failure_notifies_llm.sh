#!/bin/bash
# Validation script for issue #844: silent failure on RAG is bad UX.
#
# When a RAG query fails (e.g. "Failed to query server atlas_rag-prod: 500:
# RAG service error"), the LLM must be told the query failed and instructed
# to tell the user, instead of failing silently and answering as if nothing
# happened.
#
# Test plan:
# - _query_all_rag_sources returns (successful, exclusions, failures); a
#   non-permission error populates `failures` and does NOT raise.
# - _build_rag_failure_notice names the source and instructs the LLM to tell
#   the user; empty input -> "".
# - call_with_rag, with every source failing, injects a system message that
#   tells the LLM the retrieval failed (no silent plain-LLM fallback).
# - Same guarantee on stream_with_rag.
# - A partial failure (some sources ok, some broken) rides the failure notice
#   into the RAG context block alongside the retrieved context.
# - LLM domain errors (e.g. RateLimitError) from the inner call are still
#   re-raised, not masked by the new failure-injection path.
# - Unit tests: atlas/tests/test_rag_partial_compliance_exclusion.py

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
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# ==========================================
# Check 1: _query_all_rag_sources reports failures in a 3-tuple, no raise
# ==========================================
print_header "Check 1: _query_all_rag_sources returns failures (3-tuple), no raise"

python3 -c "
import asyncio
from unittest.mock import MagicMock
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.rag.client import RAGResponse

caller = LiteLLMCaller.__new__(LiteLLMCaller)
caller._rag_service = MagicMock()
caller._llm_config = MagicMock()
caller._model_configs = {}

service = MagicMock()
async def query_rag(user_email, source, messages):
    if source == 'broken:techdocs':
        raise RuntimeError('500: RAG service error')
    return RAGResponse(content='good context')
service.query_rag = AsyncMock(side_effect=query_rag) if False else __import__('unittest.mock', fromlist=['AsyncMock']).AsyncMock(side_effect=query_rag)

async def main():
    successful, exclusions, failures = await caller._query_all_rag_sources(
        ['allowed:policies', 'broken:techdocs'],
        service, 'user@test.com',
        [{'role': 'user', 'content': 'q'}],
    )
    assert len(successful) == 1, successful
    assert exclusions == [], exclusions
    assert len(failures) == 1, failures
    # _parse_qualified_data_source strips the server prefix -> display is the corpus
    assert 'techdocs' in failures[0], failures
    assert '500: RAG service error' not in failures[0], 'raw error must not leak to the LLM'
    print('non-permission failure is reported, not raised; raw error text is kept out')

asyncio.run(main())
"
print_result $? "_query_all_rag_sources reports failures without raising"

# ==========================================
# Check 2: _build_rag_failure_notice
# ==========================================
print_header "Check 2: _build_rag_failure_notice names the source and instructs the LLM"

python3 -c "
from atlas.modules.llm.litellm_caller import LiteLLMCaller

notice = LiteLLMCaller._build_rag_failure_notice(
    [\"The data source 'atlas_rag-prod' could not be queried because the RAG service returned an error.\"]
)
assert 'atlas_rag-prod' in notice, notice
assert 'could NOT be queried' in notice, notice
assert 'tell the user' in notice.lower(), notice

assert LiteLLMCaller._build_rag_failure_notice([]) == ''
print('failure notice names the source, instructs the LLM, and is empty when nothing failed')
"
print_result $? "_build_rag_failure_notice shape"

# ==========================================
# Check 3: call_with_rag total failure informs the LLM (not silent)
# ==========================================
print_header "Check 3: call_with_rag tells the LLM when all sources fail"

python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.rag.client import RAGResponse

caller = LiteLLMCaller.__new__(LiteLLMCaller)
caller._rag_service = MagicMock()
caller._llm_config = MagicMock()
caller._model_configs = {}

service = MagicMock()
async def query_rag(user_email, source, messages):
    raise RuntimeError('500: RAG service error')
service.query_rag = AsyncMock(side_effect=query_rag)

captured = {}
async def fake_call_plain(model_name, messages, **kwargs):
    captured['messages'] = messages
    return 'I could not reach your data sources.'
caller.call_plain = AsyncMock(side_effect=fake_call_plain)

async def main():
    result = await caller.call_with_rag(
        'test-model',
        [{'role': 'user', 'content': 'q'}],
        ['broken:techdocs'],
        'user@test.com', rag_service=service,
    )
    caller.call_plain.assert_awaited_once()
    sys_blocks = [m['content'] for m in captured['messages'] if m['role'] == 'system']
    assert sys_blocks, 'expected a system message informing the LLM'
    assert any('every query failed' in b and 'techdocs' in b for b in sys_blocks), sys_blocks
    assert result == 'I could not reach your data sources.'
    print('total RAG failure injects a tell-the-LLM system message; no silent plain fallback')

asyncio.run(main())
"
print_result $? "call_with_rag informs the LLM on total failure"

# ==========================================
# Check 4: RETIRED BY PR #862
#
# This exercised ``call_with_rag_and_tools``, which #862 deleted. In tools and
# agent mode retrieval is now an explicit ``atlas_search`` call, so a total RAG
# failure surfaces as that tool call's failed result -- visible to both the
# model and the user -- rather than as an injected system message. Checks 3, 5
# and 6 still cover the guarantee on the RAG-mode paths that remain.
# ==========================================

# ==========================================
# Check 5: stream_with_rag total failure informs the LLM
# ==========================================
print_header "Check 5: stream_with_rag tells the LLM when all sources fail"

python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.rag.client import RAGResponse

caller = LiteLLMCaller.__new__(LiteLLMCaller)
caller._rag_service = MagicMock()
caller._llm_config = MagicMock()
caller._model_configs = {}

service = MagicMock()
async def query_rag(user_email, source, messages):
    raise RuntimeError('500: RAG service error')
service.query_rag = AsyncMock(side_effect=query_rag)

captured = {}
async def fake_stream_plain(model_name, messages, temperature=0.7, user_email=None):
    captured['messages'] = messages
    yield 'I could not reach your data sources.'
caller.stream_plain = fake_stream_plain

async def main():
    chunks = []
    async for chunk in caller.stream_with_rag(
        'test-model',
        [{'role': 'user', 'content': 'q'}],
        ['broken:techdocs'],
        'user@test.com', rag_service=service,
    ):
        chunks.append(chunk)
    sys_blocks = [m['content'] for m in captured['messages'] if m['role'] == 'system']
    assert sys_blocks and any('every query failed' in b for b in sys_blocks), sys_blocks
    assert chunks == ['I could not reach your data sources.'], chunks
    print('stream_with_rag total failure injects a tell-the-LLM system message')

asyncio.run(main())
"
print_result $? "stream_with_rag informs the LLM on total failure"

# ==========================================
# Check 6: partial failure rides the notice into the RAG context block
# ==========================================
print_header "Check 6: partial failure notice rides into the RAG context block"

python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.modules.rag.client import RAGResponse

caller = LiteLLMCaller.__new__(LiteLLMCaller)
caller._rag_service = MagicMock()
caller._llm_config = MagicMock()
caller._model_configs = {}

service = MagicMock()
async def query_rag(user_email, source, messages):
    if source == 'broken:techdocs':
        raise RuntimeError('500: RAG service error')
    return RAGResponse(content='good context')
service.query_rag = AsyncMock(side_effect=query_rag)

captured = {}
async def fake_call_plain(model_name, messages, **kwargs):
    captured['messages'] = messages
    return 'answer'
caller.call_plain = AsyncMock(side_effect=fake_call_plain)

async def main():
    await caller.call_with_rag(
        'test-model',
        [{'role': 'user', 'content': 'q'}],
        ['allowed:policies', 'broken:techdocs'],
        'user@test.com', rag_service=service,
    )
    sys_blocks = [m['content'] for m in captured['messages'] if m['role'] == 'system']
    assert any('good context' in b for b in sys_blocks), 'retrieved context still present'
    assert any('techdocs' in b and 'could NOT be queried' in b for b in sys_blocks), sys_blocks
    print('partial failure: context present AND failure notice names the broken source')

asyncio.run(main())
"
print_result $? "partial failure notice reaches the RAG system message"

# ==========================================
# Check 7: LLM domain errors are still re-raised (not masked)
# ==========================================
print_header "Check 7: total-failure path does not mask LLM domain errors"

python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from atlas.modules.llm.litellm_caller import LiteLLMCaller
from atlas.domain.errors import RateLimitError
from atlas.modules.rag.client import RAGResponse

caller = LiteLLMCaller.__new__(LiteLLMCaller)
caller._rag_service = MagicMock()
caller._llm_config = MagicMock()
caller._model_configs = {}

service = MagicMock()
async def query_rag(user_email, source, messages):
    raise RuntimeError('500: RAG service error')
service.query_rag = AsyncMock(side_effect=query_rag)

caller.call_plain = AsyncMock(side_effect=RateLimitError('rate limited'))

async def main():
    try:
        await caller.call_with_rag(
            'test-model',
            [{'role': 'user', 'content': 'q'}],
            ['broken:techdocs'],
            'user@test.com', rag_service=service,
        )
    except RateLimitError:
        print('RateLimitError from inner call_plain still propagates (not masked)')
        return
    raise AssertionError('RateLimitError was masked by the failure-injection path')

asyncio.run(main())
"
print_result $? "LLM domain errors still propagate through the failure path"

# ==========================================
# Check 8: unit tests
# ==========================================
print_header "Check 8: unit tests"

cd "$ATLAS_DIR" || exit 1
python3 -m pytest tests/test_rag_partial_compliance_exclusion.py \
    tests/test_rag_context_insert_position.py \
    tests/test_rag_tools_is_completion.py \
    tests/test_tool_error_attribution.py \
    -q > /tmp/pr844_pytest_$$.log 2>&1
print_result $? "affected unit test files pass"
tail -5 /tmp/pr844_pytest_$$.log

print_header "Summary"
echo "Passed:  $PASSED"
echo "Failed:  $FAILED"

[ "$FAILED" -eq 0 ]