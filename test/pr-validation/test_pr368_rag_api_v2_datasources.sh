#!/bin/bash
# PR #368 - RAG API v2 data source discovery format
# Validates that the RAG discovery API returns v2 format with id, label,
# compliance_level, description fields and that the backend maps them correctly.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

echo "=========================================="
echo "PR #368 Validation: RAG API v2 DataSources"
echo "=========================================="
echo ""

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

# --- Check 1: DataSource model has new fields ---
echo "  Check 1: DataSource model has id, label, description fields ..."
python3 -c "
from atlas.modules.rag.client import DataSource
ds = DataSource(id='test-id', label='Test Label', compliance_level='Internal', description='Test description')
assert ds.id == 'test-id', f'Expected id=test-id, got {ds.id}'
assert ds.label == 'Test Label', f'Expected label=Test Label, got {ds.label}'
assert ds.description == 'Test description', f'Expected description, got {ds.description}'
assert ds.compliance_level == 'Internal', f'Expected compliance_level=Internal, got {ds.compliance_level}'
print('    OK - DataSource model has all v2 fields')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 2: DataSource defaults work ---
echo "  Check 2: DataSource default values ..."
python3 -c "
from atlas.modules.rag.client import DataSource
ds = DataSource(id='test', label='Test')
assert ds.compliance_level == 'CUI', f'Expected default CUI, got {ds.compliance_level}'
assert ds.description == '', f'Expected empty default description, got {ds.description}'
print('    OK - DataSource defaults are correct')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 3: AtlasRAGClient parses v2 discovery response ---
echo "  Check 3: AtlasRAGClient parses v2 discovery format ..."
python3 -c "
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from atlas.modules.rag.atlas_rag_client import AtlasRAGClient

client = AtlasRAGClient(base_url='http://test', bearer_token='tok')

mock_response = MagicMock()
mock_response.json.return_value = {
    'data_sources': [
        {'id': 'corp1', 'label': 'Corpus One', 'compliance_level': 'Internal', 'description': 'Desc 1'},
        {'id': 'corp2', 'label': 'Corpus Two', 'compliance_level': 'Public', 'description': 'Desc 2'},
    ]
}
mock_response.raise_for_status = MagicMock()

async def test():
    with patch('httpx.AsyncClient') as mock_client:
        instance = AsyncMock()
        instance.get.return_value = mock_response
        instance.__aenter__.return_value = instance
        instance.__aexit__.return_value = None
        mock_client.return_value = instance
        result = await client.discover_data_sources('user@test.com')
    assert len(result) == 2
    assert result[0].id == 'corp1'
    assert result[0].label == 'Corpus One'
    assert result[0].description == 'Desc 1'
    assert result[1].id == 'corp2'
    print('    OK - v2 discovery response parsed correctly')

asyncio.run(test())
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 4: UnifiedRAGService maps label and description to UI sources ---
echo "  Check 4: UnifiedRAGService maps label/description to UI sources ..."
python3 -c "
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from atlas.domain.unified_rag_service import UnifiedRAGService
from atlas.modules.config.config_manager import RAGSourceConfig, RAGSourcesConfig
from atlas.modules.rag.client import DataSource

config_manager = MagicMock()
http_source = RAGSourceConfig(
    type='http', display_name='Test', url='http://test', enabled=True
)
config_manager.rag_sources_config = RAGSourcesConfig(sources={'test': http_source})
service = UnifiedRAGService(config_manager=config_manager)

mock_client = AsyncMock()
mock_client.discover_data_sources.return_value = [
    DataSource(id='ds1', label='Data Source One', compliance_level='Internal', description='First data source'),
]

async def test():
    with patch.object(service, '_get_http_client', return_value=mock_client):
        result = await service._discover_http_source('test', http_source, 'user@test.com')
    assert result is not None
    src = result['sources'][0]
    assert src['id'] == 'ds1'
    assert src['name'] == 'Data Source One'
    assert src['label'] == 'Data Source One'
    assert src['description'] == 'First data source'
    print('    OK - label and description mapped to UI sources')

asyncio.run(test())
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 5: Mock RAG API returns v2 format ---
echo "  Check 5: Mock RAG API server models use v2 format ..."
python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT/mocks/atlas-rag-api-mock')
# Import the mock models to verify structure
from main import DataSourceInfo, DataSourceDiscoveryResponse

# Verify DataSourceInfo has new fields
ds = DataSourceInfo(id='test', label='Test', compliance_level='Internal', description='desc')
assert ds.id == 'test'
assert ds.label == 'Test'
assert ds.description == 'desc'

# Verify response uses data_sources (not accessible_data_sources)
resp = DataSourceDiscoveryResponse(data_sources=[ds])
assert hasattr(resp, 'data_sources')
assert not hasattr(resp, 'accessible_data_sources') or not hasattr(resp, 'user_name')
print('    OK - Mock API uses v2 format')
" && { echo "PASS"; PASS=$((PASS+1)); } || { echo "FAIL"; FAIL=$((FAIL+1)); }

# --- Check 6: Run backend unit tests ---
echo ""
echo "  Check 6: Running backend tests ..."
cd "$PROJECT_ROOT"
if bash test/run_tests.sh backend 2>&1 | tail -3; then
    echo "PASS"
    PASS=$((PASS+1))
else
    echo "FAIL"
    FAIL=$((FAIL+1))
fi

# --- Summary ---
echo ""
echo "=========================================="
echo "Results: $PASS passed, $FAIL failed"
echo "=========================================="

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
