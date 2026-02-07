#!/bin/bash
# Test script for PR #276: Direct Output for RAG Completions
#
# Test plan:
# - Verify is_completion flag exists on RAGResponse model
# - Verify AtlasRAGClient detects chat completions from response object field
# - Verify LiteLLMCaller._build_rag_completion_response helper builds correct output
# - Verify call_with_rag returns RAG completion directly when is_completion=True
# - Verify call_with_rag_and_tools returns RAG completion directly when is_completion=True
# - E2E: Start mock RAG API returning chat.completion, verify backend returns it directly
# - Run backend unit tests

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
MOCK_DIR="$PROJECT_ROOT/mocks/atlas-rag-api-mock"
SCRATCHPAD_DIR="/tmp/pr276_test_$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0
MOCK_PID=""
BACKEND_PID=""

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

print_skip() {
    echo -e "${YELLOW}SKIPPED${NC}: $1 -- $2"
    SKIPPED=$((SKIPPED + 1))
}

cleanup() {
    if [ -n "$MOCK_PID" ] && kill -0 "$MOCK_PID" 2>/dev/null; then
        kill "$MOCK_PID" 2>/dev/null
        wait "$MOCK_PID" 2>/dev/null
    fi
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill "$BACKEND_PID" 2>/dev/null
        wait "$BACKEND_PID" 2>/dev/null
    fi
    rm -rf "$SCRATCHPAD_DIR"
}
trap cleanup EXIT

mkdir -p "$SCRATCHPAD_DIR"

# Activate venv
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# ==========================================
# Check 1: RAGResponse model has is_completion field
# ==========================================
print_header "Check 1: RAGResponse model has is_completion field"

cd "$ATLAS_DIR"
python3 -c "
from modules.rag.client import RAGResponse

# Test default is_completion=False
r1 = RAGResponse(content='test')
assert r1.is_completion is False, f'Expected False, got {r1.is_completion}'

# Test is_completion=True
r2 = RAGResponse(content='test', is_completion=True)
assert r2.is_completion is True, f'Expected True, got {r2.is_completion}'

print('RAGResponse.is_completion field works correctly')
"
print_result $? "RAGResponse model has is_completion field with correct default"

# ==========================================
# Check 2: AtlasRAGClient detects chat completions
# ==========================================
print_header "Check 2: AtlasRAGClient detects chat completion from response"

cd "$ATLAS_DIR"
python3 -c "
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock
from modules.rag.atlas_rag_client import AtlasRAGClient

async def test_completion_detection():
    client = AtlasRAGClient(base_url='http://localhost:9999', bearer_token='test')

    # Mock a response that looks like a chat completion
    completion_response = {
        'id': 'chatcmpl-abc123',
        'object': 'chat.completion',
        'choices': [{
            'index': 0,
            'message': {'role': 'assistant', 'content': 'Already interpreted by LLM'},
            'finish_reason': 'stop'
        }],
        'rag_metadata': {
            'query_processing_time_ms': 100,
            'documents_found': [],
            'data_sources': ['test-source'],
            'retrieval_method': 'similarity'
        }
    }

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = completion_response
    mock_response.raise_for_status = MagicMock()

    with patch('httpx.AsyncClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_class.return_value = mock_client

        result = await client.query_rag('user@test.com', 'test-source', [{'role': 'user', 'content': 'test'}])

        assert result.is_completion is True, f'Expected is_completion=True, got {result.is_completion}'
        assert result.content == 'Already interpreted by LLM', f'Unexpected content: {result.content}'
        print(f'Completion detected: is_completion={result.is_completion}, content_length={len(result.content)}')

    # Test non-completion response (no object field)
    non_completion_response = {
        'choices': [{
            'index': 0,
            'message': {'role': 'assistant', 'content': 'Raw RAG results'},
            'finish_reason': 'stop'
        }]
    }

    mock_response2 = MagicMock()
    mock_response2.status_code = 200
    mock_response2.json.return_value = non_completion_response
    mock_response2.raise_for_status = MagicMock()

    with patch('httpx.AsyncClient') as mock_client_class:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response2)
        mock_client_class.return_value = mock_client

        result2 = await client.query_rag('user@test.com', 'test-source', [{'role': 'user', 'content': 'test'}])

        assert result2.is_completion is False, f'Expected is_completion=False, got {result2.is_completion}'
        print(f'Non-completion detected: is_completion={result2.is_completion}')

asyncio.run(test_completion_detection())
print('AtlasRAGClient completion detection works correctly')
"
print_result $? "AtlasRAGClient detects chat completion from response object field"

# ==========================================
# Check 3: _build_rag_completion_response helper
# ==========================================
print_header "Check 3: _build_rag_completion_response helper method"

cd "$ATLAS_DIR"
python3 -c "
from modules.llm.litellm_caller import LiteLLMCaller
from modules.rag.client import RAGResponse, RAGMetadata, DocumentMetadata

# Create a minimal caller (no real LLM config needed for this test)
class FakeLLMConfig:
    models = {}

caller = LiteLLMCaller.__new__(LiteLLMCaller)
caller.llm_config = FakeLLMConfig()
caller._rag_service = None

# Test with metadata
metadata = RAGMetadata(
    query_processing_time_ms=150,
    total_documents_searched=3,
    documents_found=[
        DocumentMetadata(source='doc1', content_type='text', confidence_score=0.95)
    ],
    data_source_name='test-docs',
    retrieval_method='similarity'
)
rag_response = RAGResponse(content='Already interpreted answer', metadata=metadata, is_completion=True)
result = caller._build_rag_completion_response(rag_response, 'test-docs')

assert 'RAG completions endpoint' in result, f'Missing completion note in response'
assert 'Already interpreted answer' in result, f'Missing content in response'
print(f'Helper output length: {len(result)}')
print(f'Helper output preview: {result[:200]}')
print('_build_rag_completion_response works correctly')
"
print_result $? "_build_rag_completion_response builds correct output"

# ==========================================
# Check 4: call_with_rag returns completion directly
# ==========================================
print_header "Check 4: call_with_rag returns RAG completion directly"

cd "$ATLAS_DIR"
python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from modules.llm.litellm_caller import LiteLLMCaller
from modules.rag.client import RAGResponse

async def test_call_with_rag_completion():
    # Build a caller with mocked dependencies
    class FakeLLMConfig:
        models = {}

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller.llm_config = FakeLLMConfig()

    # Mock RAG service that returns a completion
    mock_rag = AsyncMock()
    mock_rag.query_rag = AsyncMock(return_value=RAGResponse(
        content='Direct completion from RAG',
        is_completion=True
    ))
    caller._rag_service = mock_rag

    # Mock call_plain to track if it gets called (it should NOT)
    caller.call_plain = AsyncMock(return_value='This should not be called')

    result = await caller.call_with_rag(
        model_name='test-model',
        messages=[{'role': 'user', 'content': 'test query'}],
        data_sources=['atlas_rag:test-source'],
        user_email='user@test.com',
        rag_service=mock_rag,
    )

    # call_plain should NOT have been called since RAG returned a completion
    caller.call_plain.assert_not_called()

    assert 'Direct completion from RAG' in result, f'RAG completion content missing from result'
    assert 'RAG completions endpoint' in result, f'Completion note missing from result'
    print(f'call_with_rag returned completion directly (length={len(result)})')
    print('call_plain was NOT called - completion bypassed LLM')

asyncio.run(test_call_with_rag_completion())
print('call_with_rag correctly returns RAG completion directly')
"
print_result $? "call_with_rag returns RAG completion directly without calling LLM"

# ==========================================
# Check 5: call_with_rag_and_tools returns completion directly
# ==========================================
print_header "Check 5: call_with_rag_and_tools returns RAG completion directly"

cd "$ATLAS_DIR"
python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from modules.llm.litellm_caller import LiteLLMCaller
from modules.rag.client import RAGResponse
from interfaces.llm import LLMResponse

async def test_call_with_rag_and_tools_completion():
    class FakeLLMConfig:
        models = {}

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller.llm_config = FakeLLMConfig()

    mock_rag = AsyncMock()
    mock_rag.query_rag = AsyncMock(return_value=RAGResponse(
        content='Direct completion from RAG with tools',
        is_completion=True
    ))
    caller._rag_service = mock_rag

    caller.call_with_tools = AsyncMock(return_value='This should not be called')

    result = await caller.call_with_rag_and_tools(
        model_name='test-model',
        messages=[{'role': 'user', 'content': 'test query'}],
        data_sources=['atlas_rag:test-source'],
        tools_schema=[{'type': 'function', 'function': {'name': 'test_tool'}}],
        user_email='user@test.com',
        rag_service=mock_rag,
    )

    caller.call_with_tools.assert_not_called()

    # Result is LLMResponse since call_with_rag_and_tools wraps it
    assert isinstance(result, LLMResponse), f'Expected LLMResponse, got {type(result)}'
    assert 'Direct completion from RAG with tools' in result.content
    print(f'call_with_rag_and_tools returned LLMResponse directly (length={len(result.content)})')
    print('call_with_tools was NOT called - completion bypassed LLM')

asyncio.run(test_call_with_rag_and_tools_completion())
print('call_with_rag_and_tools correctly returns RAG completion directly')
"
print_result $? "call_with_rag_and_tools returns RAG completion directly without calling LLM"

# ==========================================
# Check 5b: call_with_rag multi-source combines all as raw context
# ==========================================
print_header "Check 5b: call_with_rag multi-source combines all as raw context"

cd "$ATLAS_DIR"
python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from modules.llm.litellm_caller import LiteLLMCaller
from modules.rag.client import RAGResponse

async def test_multi_source_rag():
    class FakeLLMConfig:
        models = {}

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller.llm_config = FakeLLMConfig()

    # Mock RAG service: first source returns completion, second returns raw
    mock_rag = AsyncMock()
    call_count = 0

    async def mock_query(user, source, msgs):
        nonlocal call_count
        call_count += 1
        if 'source-a' in source:
            return RAGResponse(content='Completion from A', is_completion=True)
        return RAGResponse(content='Raw context from B', is_completion=False)

    mock_rag.query_rag = AsyncMock(side_effect=mock_query)
    caller._rag_service = mock_rag

    # call_plain SHOULD be called because multi-source always goes through LLM
    caller.call_plain = AsyncMock(return_value='LLM combined answer')

    result = await caller.call_with_rag(
        model_name='test-model',
        messages=[{'role': 'user', 'content': 'test'}],
        data_sources=['rag:source-a', 'rag:source-b'],
        user_email='user@test.com',
        rag_service=mock_rag,
    )

    # Both sources should have been queried
    assert mock_rag.query_rag.call_count == 2, f'Expected 2 RAG calls, got {mock_rag.query_rag.call_count}'

    # call_plain SHOULD have been called (multi-source never shortcuts)
    caller.call_plain.assert_called_once()

    # Verify combined context was passed to LLM
    call_args = caller.call_plain.call_args
    enriched_messages = call_args[0][1]  # second positional arg
    context_msg = [m for m in enriched_messages if m['role'] == 'system' and 'Context from' in m['content']]
    assert len(context_msg) == 1, f'Expected 1 context message, got {len(context_msg)}'
    assert 'Context from source-a' in context_msg[0]['content'], 'Missing source-a context'
    assert 'Context from source-b' in context_msg[0]['content'], 'Missing source-b context'

    print(f'Multi-source RAG: both sources queried, combined context sent to LLM')
    print(f'Result: {result}')

asyncio.run(test_multi_source_rag())
print('call_with_rag multi-source works correctly')
"
print_result $? "call_with_rag multi-source combines all sources as raw context"

# ==========================================
# Check 5c: call_with_rag_and_tools multi-source combines all as raw context
# ==========================================
print_header "Check 5c: call_with_rag_and_tools multi-source combines all as raw context"

cd "$ATLAS_DIR"
python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from modules.llm.litellm_caller import LiteLLMCaller
from modules.rag.client import RAGResponse
from interfaces.llm import LLMResponse

async def test_multi_source_rag_tools():
    class FakeLLMConfig:
        models = {}

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller.llm_config = FakeLLMConfig()

    mock_rag = AsyncMock()

    async def mock_query(user, source, msgs):
        if 'source-a' in source:
            return RAGResponse(content='Completion from A', is_completion=True)
        return RAGResponse(content='Raw context from B', is_completion=False)

    mock_rag.query_rag = AsyncMock(side_effect=mock_query)
    caller._rag_service = mock_rag

    # call_with_tools SHOULD be called for multi-source
    caller.call_with_tools = AsyncMock(return_value=LLMResponse(content='LLM combined answer'))

    result = await caller.call_with_rag_and_tools(
        model_name='test-model',
        messages=[{'role': 'user', 'content': 'test'}],
        data_sources=['rag:source-a', 'rag:source-b'],
        tools_schema=[{'type': 'function', 'function': {'name': 'test_tool'}}],
        user_email='user@test.com',
        rag_service=mock_rag,
    )

    assert mock_rag.query_rag.call_count == 2, f'Expected 2 RAG calls, got {mock_rag.query_rag.call_count}'
    caller.call_with_tools.assert_called_once()
    assert isinstance(result, LLMResponse), f'Expected LLMResponse, got {type(result)}'
    print(f'Multi-source RAG+Tools: both sources queried, combined context sent to LLM')

asyncio.run(test_multi_source_rag_tools())
print('call_with_rag_and_tools multi-source works correctly')
"
print_result $? "call_with_rag_and_tools multi-source combines all sources as raw context"

# ==========================================
# Check 6: E2E - Mock RAG API returning chat.completion
# ==========================================
print_header "Check 6: E2E - Mock RAG API returns chat.completion object"

if [ -d "$MOCK_DIR" ]; then
    cd "$MOCK_DIR"

    # Start mock RAG API
    python3 -c "
import uvicorn
from main import app
uvicorn.run(app, host='127.0.0.1', port=8022, log_level='warning')
" &
    MOCK_PID=$!
    sleep 2

    if kill -0 "$MOCK_PID" 2>/dev/null; then
        # Query the mock RAG API completions endpoint
        RESPONSE=$(curl -s -X POST "http://127.0.0.1:8022/rag/completions?as_user=test@test.com" \
            -H "Content-Type: application/json" \
            -H "Authorization: Bearer test-atlas-rag-token" \
            -d '{
                "messages": [{"role": "user", "content": "What is our remote work policy?"}],
                "stream": false,
                "model": "openai/gpt-oss-120b",
                "top_k": 4,
                "corpora": ["company-policies"]
            }')

        # Verify the response has object: chat.completion
        echo "$RESPONSE" | python3 -c "
import sys, json
data = json.load(sys.stdin)
obj_type = data.get('object', '')
has_choices = 'choices' in data and len(data['choices']) > 0
print(f'Response object type: {obj_type}')
print(f'Has choices: {has_choices}')
assert obj_type == 'chat.completion', f'Expected chat.completion, got {obj_type}'
assert has_choices, 'Response missing choices'
content = data['choices'][0]['message']['content']
print(f'Content preview: {content[:100]}...')
"
        print_result $? "Mock RAG API returns chat.completion response"

        kill "$MOCK_PID" 2>/dev/null
        wait "$MOCK_PID" 2>/dev/null
        MOCK_PID=""
    else
        print_skip "E2E mock RAG test" "Mock RAG API failed to start"
    fi
else
    print_skip "E2E mock RAG test" "Mock directory $MOCK_DIR not found"
fi

# ==========================================
# Check 7: Documentation updated
# ==========================================
print_header "Check 7: Documentation updated"

grep -q "276" "$PROJECT_ROOT/CHANGELOG.md"
print_result $? "CHANGELOG.md has entry for PR #276"

grep -q "is_completion" "$ATLAS_DIR/modules/rag/client.py"
print_result $? "RAGResponse model has is_completion field in source"

grep -q "completion" "$PROJECT_ROOT/docs/admin/external-rag-api.md"
print_result $? "external-rag-api.md documents completion behavior"

# ==========================================
# Check 8: Run backend unit tests
# ==========================================
print_header "Check 8: Backend unit tests"

cd "$PROJECT_ROOT"
./test/run_tests.sh backend
print_result $? "Backend unit tests pass"

# ==========================================
# Summary
# ==========================================
print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo -e "Skipped: ${YELLOW}$SKIPPED${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}PR #276 validation FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}PR #276 validation PASSED${NC}"
    exit 0
fi
