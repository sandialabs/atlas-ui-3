#!/usr/bin/env bash
# PR #351 - Make atlas-init fast with lazy package imports
#
# Validates:
# 1. atlas-init --help completes in under 2 seconds (was ~4s before fix)
# 2. Importing atlas does not eagerly load atlas.atlas_client
# 3. from atlas import AtlasClient still works via lazy __getattr__
# 4. Backend unit tests pass

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

pass() { echo "  PASSED: $1"; ((PASSED++)); }
fail() { echo "  FAILED: $1"; ((FAILED++)); }

echo "========================================"
echo "PR #351 - Fast atlas-init (lazy imports)"
echo "========================================"

# Activate virtual environment
source "$PROJECT_ROOT/.venv/bin/activate"

# -------------------------------------------------------------------
# Test 1: atlas-init --help completes quickly
# -------------------------------------------------------------------
echo ""
echo "Test 1: atlas-init --help completes in <2s"

START_TIME=$(python3 -c "import time; print(time.monotonic())")
python3 -m atlas.init_cli --help > /dev/null 2>&1
END_TIME=$(python3 -c "import time; print(time.monotonic())")

ELAPSED=$(python3 -c "start=$START_TIME; end=$END_TIME; print(f'{end - start:.2f}')")
echo "  Elapsed: ${ELAPSED}s"

if python3 -c "import sys; exit(0 if float(sys.argv[1]) < 2.0 else 1)" "$ELAPSED"; then
    pass "atlas-init --help completed in ${ELAPSED}s (<2s)"
else
    fail "atlas-init --help took ${ELAPSED}s (expected <2s)"
fi

# -------------------------------------------------------------------
# Test 2: Importing atlas does not eagerly import atlas_client
# -------------------------------------------------------------------
echo ""
echo "Test 2: import atlas does not eagerly import atlas.atlas_client"

if python3 -c "
import atlas
import sys
if 'atlas.atlas_client' in sys.modules:
    print('  atlas.atlas_client was eagerly imported')
    exit(1)
print('  atlas.atlas_client not in sys.modules (good)')
"; then
    pass "No eager import of atlas.atlas_client"
else
    fail "atlas.atlas_client was eagerly imported"
fi

# -------------------------------------------------------------------
# Test 3: from atlas import AtlasClient still works
# -------------------------------------------------------------------
echo ""
echo "Test 3: from atlas import AtlasClient works via lazy __getattr__"

if python3 -c "
from atlas import AtlasClient
print(f'  AtlasClient: {AtlasClient}')
"; then
    pass "Lazy import of AtlasClient works"
else
    fail "Lazy import of AtlasClient failed"
fi

# -------------------------------------------------------------------
# Test 4: from atlas import ChatResult still works
# -------------------------------------------------------------------
echo ""
echo "Test 4: from atlas import ChatResult works via lazy __getattr__"

if python3 -c "
from atlas import ChatResult
print(f'  ChatResult: {ChatResult}')
"; then
    pass "Lazy import of ChatResult works"
else
    fail "Lazy import of ChatResult failed"
fi

# -------------------------------------------------------------------
# Test 5: atlas.__version__ available without heavy imports
# -------------------------------------------------------------------
echo ""
echo "Test 5: atlas.__version__ available without heavy imports"

if python3 -c "
import atlas
import sys
print(f'  __version__: {atlas.__version__}')
assert 'atlas.atlas_client' not in sys.modules, 'Heavy imports triggered by version access'
"; then
    pass "__version__ accessible without heavy imports"
else
    fail "__version__ triggered heavy imports"
fi

# -------------------------------------------------------------------
# Test 6: Run backend unit tests
# -------------------------------------------------------------------
echo ""
echo "Test 6: Backend unit tests"

if "$PROJECT_ROOT/test/run_tests.sh" backend; then
    pass "Backend unit tests passed"
else
    fail "Backend unit tests failed"
fi

# -------------------------------------------------------------------
# Summary
# -------------------------------------------------------------------
echo ""
echo "========================================"
echo "Results: $PASSED passed, $FAILED failed"
echo "========================================"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
