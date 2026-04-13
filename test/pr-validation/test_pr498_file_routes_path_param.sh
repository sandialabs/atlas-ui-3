#!/bin/bash
# PR #498 - Use {file_key:path} converter for GET/DELETE /api/files/{file_key}
#
# Test plan:
# - Verify GET /api/files/{file_key:path} route uses the path converter
# - Verify DELETE /api/files/{file_key:path} route uses the path converter
# - Verify multi-segment S3 keys (containing '/') reach the handler intact
# - Verify /api/files/healthz is NOT shadowed by the greedy path catch-all
# - Run the dedicated regression pytest file
# - Run backend unit tests

set -uo pipefail

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
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$ATLAS_DIR:$PROJECT_ROOT:${PYTHONPATH:-}"

# -------------------------------------------------------------------
print_header "Test 1: GET route uses {file_key:path} converter"
# -------------------------------------------------------------------
if grep -E '@router\.get\("/files/\{file_key:path\}"' "$ATLAS_DIR/routes/files_routes.py" >/dev/null; then
    print_result 0 "GET /files/{file_key:path} route declared with path converter"
else
    print_result 1 "GET /files/{file_key:path} route missing path converter"
fi

# -------------------------------------------------------------------
print_header "Test 2: DELETE route uses {file_key:path} converter"
# -------------------------------------------------------------------
if grep -E '@router\.delete\("/files/\{file_key:path\}"' "$ATLAS_DIR/routes/files_routes.py" >/dev/null; then
    print_result 0 "DELETE /files/{file_key:path} route declared with path converter"
else
    print_result 1 "DELETE /files/{file_key:path} route missing path converter"
fi

# -------------------------------------------------------------------
print_header "Test 3: Specific /files routes declared before path catch-all"
# -------------------------------------------------------------------
# The catch-all path converter is greedy — specific routes must appear first.
python3 - <<'PY'
import re, sys
path = "atlas/routes/files_routes.py"
with open(path) as f:
    src = f.read()

# Find line numbers for each route decorator.
def line_of(pattern):
    m = re.search(pattern, src)
    if not m:
        return None
    return src[:m.start()].count("\n") + 1

healthz_line = line_of(r'@router\.get\("/files/healthz"')
get_catchall_line = line_of(r'@router\.get\("/files/\{file_key:path\}"')
delete_catchall_line = line_of(r'@router\.delete\("/files/\{file_key:path\}"')

missing = []
if healthz_line is None:
    missing.append("/files/healthz")
if get_catchall_line is None:
    missing.append("GET /files/{file_key:path}")
if delete_catchall_line is None:
    missing.append("DELETE /files/{file_key:path}")
if missing:
    print(f"MISSING_ROUTES: {missing}")
    sys.exit(1)

ok = (
    healthz_line < get_catchall_line
    and healthz_line < delete_catchall_line
)
if not ok:
    print(
        f"WRONG_ORDER: healthz={healthz_line} "
        f"get_catchall={get_catchall_line} delete_catchall={delete_catchall_line}"
    )
    sys.exit(1)
print(
    f"OK: healthz@{healthz_line} "
    f"get_catchall@{get_catchall_line} delete_catchall@{delete_catchall_line}"
)
PY
print_result $? "Specific /files routes declared before greedy catch-all"

# -------------------------------------------------------------------
print_header "Test 4: GET /api/files/<multi-segment-key> returns the full key"
# -------------------------------------------------------------------
cd "$ATLAS_DIR"
python3 - <<'PY'
import base64, sys
from starlette.testclient import TestClient
from main import app
from atlas.infrastructure.app_factory import app_factory

MULTI = "users/alice@example.com/generated/subdir/report.txt"

captured = {}
async def fake_get_file(user, key):
    captured["user"] = user
    captured["key"] = key
    return {
        "key": key,
        "filename": "report.txt",
        "content_base64": base64.b64encode(b"data").decode(),
        "content_type": "text/plain",
        "size": 4,
        "last_modified": "",
        "etag": "",
        "tags": {},
    }

storage = app_factory.get_file_storage()
storage.get_file = fake_get_file

client = TestClient(app)
resp = client.get(
    f"/api/files/{MULTI}",
    headers={"X-User-Email": "alice@example.com"},
)
if resp.status_code != 200:
    print(f"STATUS={resp.status_code} BODY={resp.text}")
    sys.exit(1)
if captured.get("key") != MULTI:
    print(f"HANDLER_KEY_MISMATCH: got {captured.get('key')!r} want {MULTI!r}")
    sys.exit(1)
if resp.json().get("key") != MULTI:
    print(f"RESPONSE_KEY_MISMATCH: {resp.json()}")
    sys.exit(1)
print("OK")
PY
print_result $? "GET routes multi-segment key to handler and echoes it back"

# -------------------------------------------------------------------
print_header "Test 5: DELETE /api/files/<multi-segment-key> returns the full key"
# -------------------------------------------------------------------
python3 - <<'PY'
import sys
from starlette.testclient import TestClient
from main import app
from atlas.infrastructure.app_factory import app_factory

MULTI = "users/alice@example.com/generated/subdir/report.txt"

captured = {}
async def fake_delete_file(user, key):
    captured["user"] = user
    captured["key"] = key
    return True

storage = app_factory.get_file_storage()
storage.delete_file = fake_delete_file

client = TestClient(app)
resp = client.delete(
    f"/api/files/{MULTI}",
    headers={"X-User-Email": "alice@example.com"},
)
if resp.status_code != 200:
    print(f"STATUS={resp.status_code} BODY={resp.text}")
    sys.exit(1)
if captured.get("key") != MULTI:
    print(f"HANDLER_KEY_MISMATCH: got {captured.get('key')!r} want {MULTI!r}")
    sys.exit(1)
if resp.json().get("key") != MULTI:
    print(f"RESPONSE_KEY_MISMATCH: {resp.json()}")
    sys.exit(1)
print("OK")
PY
print_result $? "DELETE routes multi-segment key to handler and echoes it back"

# -------------------------------------------------------------------
print_header "Test 6: /api/files/healthz is not shadowed by the path catch-all"
# -------------------------------------------------------------------
python3 - <<'PY'
import sys
from starlette.testclient import TestClient
from main import app

client = TestClient(app)
resp = client.get("/api/files/healthz", headers={"X-User-Email": "alice@example.com"})
if resp.status_code != 200:
    print(f"STATUS={resp.status_code} BODY={resp.text}")
    sys.exit(1)
data = resp.json()
if data.get("service") != "files-api":
    print(f"WRONG_SERVICE: {data}")
    sys.exit(1)
if "s3_config" not in data:
    print(f"MISSING_S3_CONFIG: {data}")
    sys.exit(1)
print("OK")
PY
print_result $? "/api/files/healthz routes to the health handler, not the catch-all"

# -------------------------------------------------------------------
print_header "Test 7: Dedicated regression pytest file passes"
# -------------------------------------------------------------------
cd "$ATLAS_DIR"
set -o pipefail
python -m pytest tests/test_files_multisegment_keys.py -v 2>&1 | tail -20
PYTEST_EXIT=${PIPESTATUS[0]}
set +o pipefail
print_result $PYTEST_EXIT "atlas/tests/test_files_multisegment_keys.py passes"

# -------------------------------------------------------------------
print_header "Test 8: Backend unit tests"
# -------------------------------------------------------------------
cd "$PROJECT_ROOT"
set -o pipefail
./test/run_tests.sh backend 2>&1 | tail -5
BACKEND_EXIT=${PIPESTATUS[0]}
set +o pipefail
print_result $BACKEND_EXIT "Backend unit tests"

# -------------------------------------------------------------------
print_header "Summary"
# -------------------------------------------------------------------
echo ""
echo "Passed: $PASSED"
echo "Failed: $FAILED"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}Some tests failed!${NC}"
    exit 1
else
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
fi
