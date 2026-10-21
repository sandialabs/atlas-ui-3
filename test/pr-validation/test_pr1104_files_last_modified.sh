#!/bin/bash
# Test script for PR #1104: POST /api/files answered 500 (last_modified validation).
#
# Test plan:
# - The storage layer reports last_modified as an ISO-8601 string, the shape
#   the file API serves (boto3 hands back a datetime, which is what broke).
# - End-to-end HTTP against a real boto3 client and a real S3 HTTP server:
#   POST /api/files stores the object and answers 200 (not 500).
# - End-to-end HTTP: GET /api/files lists the stored file with a string
#   last_modified.
# - End-to-end HTTP: GET /api/files/{key} returns the file with a string
#   last_modified.
# - The dedicated regression pytest file passes.
# - Backend unit tests pass.

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
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

print_header "PR #1104: file last_modified contract validation"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

# The checkout under test must win over any installed copy of the package.
export PYTHONPATH="$PROJECT_ROOT:${PYTHONPATH:-}"

WORK_DIR="$(mktemp -d)"
S3_PID=""
ATLAS_PID=""

cleanup() {
    [ -n "$ATLAS_PID" ] && kill "$ATLAS_PID" 2>/dev/null
    [ -n "$S3_PID" ] && kill "$S3_PID" 2>/dev/null
    wait 2>/dev/null
    rm -rf "$WORK_DIR"
}
trap cleanup EXIT

free_port() {
    python3 - <<'PY'
import socket
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])
PY
}

wait_for_url() {
    local url="$1"
    local attempts="${2:-60}"
    for _ in $(seq 1 "$attempts"); do
        if curl -sf -o /dev/null "$url"; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# check_json_string_field <json> <field> <label>
# Passes when the field is present and is a JSON string.
check_json_string_field() {
    local json="$1"
    local field="$2"
    local label="$3"
    printf '%s' "$json" | FIELD="$field" python3 -c '
import json, os, sys
field = os.environ["FIELD"]
body = json.load(sys.stdin)
value = body.get(field)
if not isinstance(value, str):
    sys.exit(f"{field} is {type(value).__name__}, expected str")
if not value:
    sys.exit(f"{field} is empty")
print(f"{field}={value}")
'
    print_result $? "$label"
}

S3_PORT="$(free_port)"
ATLAS_PORT="$(free_port)"
S3_URL="http://127.0.0.1:${S3_PORT}"
ATLAS_URL="http://127.0.0.1:${ATLAS_PORT}"
USER_EMAIL="pr1104@example.com"

# ==========================================
# 1. Storage layer reports a string, not a datetime
# ==========================================
print_header "1. Storage layer reports last_modified as an ISO-8601 string"

cat > "$WORK_DIR/storage_layer_check.py" <<'PY'
import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

from atlas.modules.file_storage import s3_client as s3_mod

# Stub boto3 so no network call is made; only the response shape matters.
s3_mod.S3StorageClient._ensure_bucket = lambda self: None
fake_boto = MagicMock()
s3_mod.boto3.client = lambda *a, **kw: fake_boto

client = s3_mod.S3StorageClient(
    s3_endpoint="http://fake",
    s3_bucket_name="b",
    s3_access_key="a",
    s3_secret_key="s",
    s3_region="us-east-1",
    s3_timeout=1,
    s3_use_ssl=False,
)
fake_boto.put_object.return_value = {}
fake_boto.head_object.return_value = {
    "LastModified": datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc),
    "ETag": '"abc"',
}

result = asyncio.run(client.upload_file(
    user_email="alice@example.com",
    filename="a.txt",
    content_base64="aGkK",
    content_type="text/plain",
))
value = result.get("last_modified")
print(f"TYPE={type(value).__name__} VALUE={value!r}")
PY

RESULT="$(cd "$ATLAS_DIR" && python3 "$WORK_DIR/storage_layer_check.py" 2>&1 | tail -1)"
echo "  storage client said: $RESULT"
[[ "$RESULT" == TYPE=str* ]]
print_result $? "upload_file returns a string last_modified (got: $RESULT)"

# ==========================================
# 2. End-to-end: S3 server + backend, POST /api/files
# ==========================================
print_header "2. End-to-end POST /api/files against a real S3 service"

# The repository's S3 mock server speaks enough of the S3 API for boto3, and
# it reports LastModified the way S3 does -- so boto3 parses it into a
# datetime, which is exactly the shape that used to break the response.
(
    cd "$PROJECT_ROOT/mocks/s3-mock" || exit 1
    PORT="$S3_PORT" MOCK_S3_ROOT="$WORK_DIR/s3-root" python3 main.py > "$WORK_DIR/s3.log" 2>&1
) &
S3_PID=$!

if wait_for_url "$S3_URL/atlas-files" 30; then
    S3_READY=0
else
    S3_READY=1
    echo "  S3 mock server did not come up; log:"
    tail -20 "$WORK_DIR/s3.log" 2>/dev/null
fi
print_result $S3_READY "S3 service is reachable on $S3_URL"

(
    cd "$PROJECT_ROOT" || exit 1
    ENVIRONMENT=development \
    DEBUG_MODE=true \
    USE_MOCK_S3=false \
    S3_ENDPOINT="$S3_URL" \
    S3_BUCKET_NAME=atlas-files \
    S3_ACCESS_KEY=pr1104accesskey \
    S3_SECRET_KEY=pr1104secretkey \
    S3_USE_SSL=false \
    PORT="$ATLAS_PORT" \
    APP_LOG_DIR="$WORK_DIR/logs" \
    MCP_CONFIG_FILE=atlas-pr1104-no-such-mcp.json \
    python3 "$ATLAS_DIR/main.py" > "$WORK_DIR/atlas.log" 2>&1
) &
ATLAS_PID=$!

if wait_for_url "$ATLAS_URL/api/health" 90; then
    ATLAS_READY=0
else
    ATLAS_READY=1
    echo "  backend did not come up; log:"
    tail -20 "$WORK_DIR/atlas.log" 2>/dev/null
fi
print_result $ATLAS_READY "backend is reachable on $ATLAS_URL"

UPLOAD_BODY="$(curl -s -w '\n%{http_code}' -X POST "$ATLAS_URL/api/files" \
    -H 'Content-Type: application/json' \
    -H "X-User-Email: $USER_EMAIL" \
    -d '{"filename": "a.txt", "content_base64": "aGkK", "content_type": "text/plain"}')"
UPLOAD_STATUS="$(printf '%s' "$UPLOAD_BODY" | tail -1)"
UPLOAD_JSON="$(printf '%s' "$UPLOAD_BODY" | head -n -1)"
echo "  POST /api/files -> $UPLOAD_STATUS"
echo "  $UPLOAD_JSON"

[ "$UPLOAD_STATUS" = "200" ]
print_result $? "POST /api/files answers 200 (got: $UPLOAD_STATUS)"

check_json_string_field "$UPLOAD_JSON" "last_modified" \
    "POST response carries a string last_modified"

# ==========================================
# 3. End-to-end: GET /api/files lists the stored file
# ==========================================
print_header "3. End-to-end GET /api/files"

LIST_BODY="$(curl -s -w '\n%{http_code}' "$ATLAS_URL/api/files" -H "X-User-Email: $USER_EMAIL")"
LIST_STATUS="$(printf '%s' "$LIST_BODY" | tail -1)"
LIST_JSON="$(printf '%s' "$LIST_BODY" | head -n -1)"
echo "  GET /api/files -> $LIST_STATUS"
echo "  $LIST_JSON"

[ "$LIST_STATUS" = "200" ]
print_result $? "GET /api/files answers 200 (got: $LIST_STATUS)"

printf '%s' "$LIST_JSON" | python3 -c '
import json, sys
files = json.load(sys.stdin)
assert files, "the uploaded file is not listed"
for entry in files:
    value = entry.get("last_modified")
    assert isinstance(value, str), f"listed last_modified is {type(value).__name__}"
print(f"LISTED={len(files)}")
'
print_result $? "listed file carries a string last_modified"

# ==========================================
# 4. End-to-end: GET /api/files/{key}
# ==========================================
print_header "4. End-to-end GET /api/files/{key}"

FILE_KEY="$(printf '%s' "$UPLOAD_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("key", ""))')"
GET_BODY="$(curl -s -w '\n%{http_code}' "$ATLAS_URL/api/files/$FILE_KEY" -H "X-User-Email: $USER_EMAIL")"
GET_STATUS="$(printf '%s' "$GET_BODY" | tail -1)"
GET_JSON="$(printf '%s' "$GET_BODY" | head -n -1)"
echo "  GET /api/files/$FILE_KEY -> $GET_STATUS"

[ "$GET_STATUS" = "200" ]
print_result $? "GET /api/files/{key} answers 200 (got: $GET_STATUS)"

check_json_string_field "$GET_JSON" "last_modified" \
    "GET response carries a string last_modified"

# Stop the servers before the test suites run.
cleanup
S3_PID=""
ATLAS_PID=""

# ==========================================
# 5. Dedicated regression pytest file
# ==========================================
print_header "5. Dedicated regression pytest file"

cd "$ATLAS_DIR"
set -o pipefail
env -u DEBUG_MODE -u ENVIRONMENT \
    PYTHONPATH="$PROJECT_ROOT" \
    python3 -m pytest tests/test_files_last_modified.py -v 2>&1 | tail -20
PYTEST_EXIT=${PIPESTATUS[0]}
set +o pipefail
print_result $PYTEST_EXIT "tests/test_files_last_modified.py passes"

# ==========================================
# 6. Backend unit tests
# ==========================================
print_header "6. Backend unit tests"

# The suite reads the environment, so a .env that belongs to another checkout
# of this project (python-dotenv's find_dotenv walks up from the installed
# package) can make two unrelated auth tests in tests/test_core_auth.py fail
# on an unmodified tree as well. A clean checkout -- and CI, which runs the
# suite in a container without a .env -- is unaffected.
cd "$PROJECT_ROOT"
set -o pipefail
env -u DEBUG_MODE -u ENVIRONMENT \
    PYTHONPATH="$PROJECT_ROOT" \
    ./test/run_tests.sh backend 2>&1 | tail -15
BACKEND_EXIT=${PIPESTATUS[0]}
set +o pipefail
print_result $BACKEND_EXIT "Backend unit tests"

# ==========================================
# Summary
# ==========================================
print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}SOME TESTS FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}ALL TESTS PASSED${NC}"
    exit 0
fi
