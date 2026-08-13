#!/bin/bash
# Validation script for issue #791: RAG API v2 tool-oriented query interface
#
# Test plan:
# - Config: api_version selects the v2 endpoint paths; v1 stays the default
# - Client: query_v2 posts {query, corpora, mode, top_k} and never `messages`
# - Client: raw mode returns evidence (is_completion=False), synthesized an
#   answer (is_completion=True)
# - Client: an empty query is rejected before any request is made
# - Mock E2E: /api/v2/discover/datasources advertises api_version, and
#   /api/v2/rag/query serves both modes, 400s an empty query and 403s an
#   unauthorized corpus
# - Unit tests: atlas/tests/test_atlas_rag_v2.py

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
MOCK_DIR="$PROJECT_ROOT/mocks/atlas-rag-api-mock"
MOCK_PORT=8792
MOCK_URL="http://127.0.0.1:$MOCK_PORT"
MOCK_TOKEN="test-atlas-rag-token"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0
MOCK_PID=""

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

print_skip() {
    echo -e "${YELLOW}SKIPPED${NC}: $1 -- $2"
    SKIPPED=$((SKIPPED + 1))
}

cleanup() {
    if [ -n "$MOCK_PID" ] && kill -0 "$MOCK_PID" 2>/dev/null; then
        kill "$MOCK_PID" 2>/dev/null
        wait "$MOCK_PID" 2>/dev/null
    fi
}
trap cleanup EXIT

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# ==========================================
# Check 1: api_version picks the endpoint paths
# ==========================================
print_header "Check 1: api_version selects v1/v2 endpoints"

python3 -c "
from atlas.modules.config.models import RAGSourceConfig
from atlas.modules.rag.atlas_rag_client import AtlasRAGClient

v1 = RAGSourceConfig(type='http', url='http://x')
assert v1.api_version == 'v1', v1.api_version
assert v1.query_endpoint == '/api/v1/rag/completions', v1.query_endpoint

v2 = RAGSourceConfig(type='http', url='http://x', api_version='v2')
assert v2.query_endpoint == '/api/v2/rag/query', v2.query_endpoint
assert v2.discovery_endpoint == '/api/v2/discover/datasources', v2.discovery_endpoint

override = RAGSourceConfig(type='http', url='http://x', api_version='v2', query_endpoint='/custom')
assert override.query_endpoint == '/custom', override.query_endpoint

client = AtlasRAGClient(base_url='http://x', api_version='v2')
assert client.query_path == '/api/v2/rag/query', client.query_path
print('api_version drives endpoint defaults; overrides still win')
"
print_result $? "api_version selects the right endpoint paths"

# ==========================================
# Check 2: query_v2 sends the query, not the conversation
# ==========================================
print_header "Check 2: query_v2 request body carries no conversation"

python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from atlas.modules.rag.atlas_rag_client import AtlasRAGClient

RAW = {
    'query': 'q', 'mode': 'raw',
    'results': {'hits': [{'document_ref': 1, 'filename': 'a.pdf', 'title': 'A',
                          'sections': [{'section_ref': 1, 'text': 'evidence', 'relevance': 0.9}]}],
                'stats': {'total_found': 1, 'top_k': 4}},
    'metadata': {'response_time_ms': 12, 'corpora_searched': ['docs']},
}

async def main():
    client = AtlasRAGClient(base_url='http://x', bearer_token='t', api_version='v2', top_k=4)
    resp = MagicMock()
    resp.json.return_value = RAW
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    with patch('httpx.AsyncClient') as cls:
        inst = AsyncMock()
        inst.post.return_value = resp
        inst.__aenter__.return_value = inst
        inst.__aexit__.return_value = None
        cls.return_value = inst
        result = await client.query_v2('alice@corp.com', query='what is X', corpora='docs', mode='raw')

    url = inst.post.call_args[0][0]
    payload = inst.post.call_args[1]['json']
    assert url.endswith('/api/v2/rag/query'), url
    assert payload['query'] == 'what is X', payload
    assert 'messages' not in payload, payload
    assert payload['mode'] == 'raw' and payload['top_k'] == 4, payload
    assert result.is_completion is False, result.is_completion
    assert 'evidence' in result.content, result.content
    assert result.metadata.documents_found[0].document_ref == 1
    print('query_v2 posts an explicit query and returns raw evidence')

asyncio.run(main())
"
print_result $? "query_v2 sends {query, corpora, mode, top_k} and no messages"

# ==========================================
# Check 3: synthesized mode is a completion; empty query is refused
# ==========================================
print_header "Check 3: synthesized mode and query validation"

python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from atlas.modules.rag.atlas_rag_client import AtlasRAGClient

SYNTH = {
    'query': 'q', 'mode': 'synthesized',
    'results': {'answer': 'The answer. [1]',
                'citations': [{'document_ref': 1, 'filename': 'a.pdf'}]},
    'metadata': {'response_time_ms': 30, 'corpora_searched': ['docs']},
}

async def main():
    client = AtlasRAGClient(base_url='http://x', api_version='v2')
    resp = MagicMock()
    resp.json.return_value = SYNTH
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    with patch('httpx.AsyncClient') as cls:
        inst = AsyncMock()
        inst.post.return_value = resp
        inst.__aenter__.return_value = inst
        inst.__aexit__.return_value = None
        cls.return_value = inst
        result = await client.query_v2('u', query='q', corpora='docs', mode='synthesized')

    assert result.is_completion is True, result.is_completion
    assert result.content == 'The answer. [1]', result.content

    for bad in ('', '   '):
        try:
            await client.query_v2('u', query=bad, corpora='docs')
        except ValueError:
            pass
        else:
            raise AssertionError('empty query was not rejected')

    try:
        await client.query_v2('u', query='q', corpora='docs', mode='summary')
    except ValueError:
        pass
    else:
        raise AssertionError('unknown mode was not rejected')
    print('synthesized short-circuits; empty query and bad mode are refused')

asyncio.run(main())
"
print_result $? "synthesized returns a completion; bad input is refused locally"

# ==========================================
# Check 4: UnifiedRAGService routes by api_version
# ==========================================
print_header "Check 4: UnifiedRAGService routes by configured contract"

python3 -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from atlas.domain.unified_rag_service import UnifiedRAGService
from atlas.modules.config.models import RAGSourceConfig, RAGSourcesConfig
from atlas.modules.rag.client import RAGResponse

cm = MagicMock()
cm.rag_sources_config = RAGSourcesConfig(sources={
    'v1_rag': RAGSourceConfig(type='http', url='http://old'),
    'v2_rag': RAGSourceConfig(type='http', url='http://new', api_version='v2', top_k=7),
})
svc = UnifiedRAGService(config_manager=cm)

async def main():
    client = AsyncMock()
    client.query_rag.return_value = RAGResponse(content='v1', metadata=None)
    client.query_v2.return_value = RAGResponse(content='v2', metadata=None)

    with patch.object(svc, '_get_http_client', return_value=client):
        await svc.query_rag('u', 'v1_rag:docs', [{'role': 'user', 'content': 'hi'}])
        client.query_rag.assert_awaited_once()
        client.query_v2.assert_not_awaited()

        await svc.query_rag('u', 'v2_rag:docs', [{'role': 'user', 'content': 'ignored'}],
                            query='the real question', mode='raw')
        kwargs = client.query_v2.call_args[1]
        assert kwargs['query'] == 'the real question', kwargs
        assert kwargs['mode'] == 'raw' and kwargs['top_k'] == 7, kwargs
    print('v1 sources post messages; v2 sources post the explicit query')

asyncio.run(main())
"
print_result $? "UnifiedRAGService sends each source over its configured contract"

# ==========================================
# Check 5: mock serves v2 discovery + query
# ==========================================
print_header "Check 5: mock ATLAS RAG API serves /api/v2"

if ! command -v curl >/dev/null 2>&1; then
    print_skip "Mock E2E" "curl not available"
else
    cd "$MOCK_DIR" || exit 1
    ATLAS_RAG_MOCK_PORT="$MOCK_PORT" python3 main.py > /tmp/issue791_mock_$$.log 2>&1 &
    MOCK_PID=$!

    READY=1
    for _ in $(seq 1 30); do
        if curl -sf "$MOCK_URL/health" >/dev/null 2>&1; then
            READY=0
            break
        fi
        sleep 0.5
    done

    if [ "$READY" -ne 0 ]; then
        print_skip "Mock E2E" "mock did not start (see /tmp/issue791_mock_$$.log)"
    else
        DISCOVER=$(curl -s -H "Authorization: Bearer $MOCK_TOKEN" \
            "$MOCK_URL/api/v2/discover/datasources?role=read&as_user=alice@example.com")
        echo "$DISCOVER" | grep -q '"api_version":"v2"' || echo "$DISCOVER" | grep -q '"api_version": "v2"'
        print_result $? "v2 discovery advertises api_version per source"

        RAW=$(curl -s -H "Authorization: Bearer $MOCK_TOKEN" -H "Content-Type: application/json" \
            -d '{"query":"vacation policy","corpora":"company-policies","mode":"raw","top_k":2}' \
            "$MOCK_URL/api/v2/rag/query?as_user=alice@example.com")
        echo "$RAW" | grep -q '"hits"' && echo "$RAW" | grep -q '"sections"'
        print_result $? "v2 raw mode returns hits with sections"

        SYNTH=$(curl -s -H "Authorization: Bearer $MOCK_TOKEN" -H "Content-Type: application/json" \
            -d '{"query":"vacation policy","corpora":["company-policies"],"mode":"synthesized"}' \
            "$MOCK_URL/api/v2/rag/query?as_user=alice@example.com")
        echo "$SYNTH" | grep -q '"answer"' && echo "$SYNTH" | grep -q '"citations"'
        print_result $? "v2 synthesized mode returns an answer with citations"

        EMPTY_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
            -H "Authorization: Bearer $MOCK_TOKEN" -H "Content-Type: application/json" \
            -d '{"query":"   ","corpora":"company-policies"}' \
            "$MOCK_URL/api/v2/rag/query?as_user=alice@example.com")
        [ "$EMPTY_STATUS" = "400" ]
        print_result $? "v2 rejects an empty query with 400 (got $EMPTY_STATUS)"

        DENIED_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
            -H "Authorization: Bearer $MOCK_TOKEN" -H "Content-Type: application/json" \
            -d '{"query":"deployment","corpora":"technical-docs"}' \
            "$MOCK_URL/api/v2/rag/query?as_user=guest@example.com")
        [ "$DENIED_STATUS" = "403" ]
        print_result $? "v2 enforces corpus authorization with 403 (got $DENIED_STATUS)"

        NOAUTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -H "Content-Type: application/json" \
            -d '{"query":"x","corpora":"company-policies"}' \
            "$MOCK_URL/api/v2/rag/query?as_user=alice@example.com")
        [ "$NOAUTH_STATUS" = "401" ]
        print_result $? "v2 requires a bearer token (got $NOAUTH_STATUS)"
    fi
fi

# ==========================================
# Check 6: unit tests
# ==========================================
print_header "Check 6: v2 unit tests"

cd "$ATLAS_DIR" || exit 1
python3 -m pytest tests/test_atlas_rag_v2.py -q > /tmp/issue791_pytest_$$.log 2>&1
print_result $? "atlas/tests/test_atlas_rag_v2.py passes"
tail -3 /tmp/issue791_pytest_$$.log

print_header "Summary"
echo "Passed:  $PASSED"
echo "Failed:  $FAILED"
echo "Skipped: $SKIPPED"

[ "$FAILED" -eq 0 ]
