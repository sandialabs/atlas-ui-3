#!/bin/bash
# Test script for PR #946: admin access via ADMIN_USERS *or* dynamic ADMIN_GROUP
# membership (issue #945).
#
# Test plan (end-to-end HTTP against the real app, with a real external
# authorization service -- the outbound membership POST is genuine network I/O
# to a local HTTP server, not a mock):
# - Dynamic only: no member enumerated anywhere, ADMIN_GROUP named. The
#   authorizer's "yes" reaches /admin/banners as 200; its "no" as 403.
# - Override: an ADMIN_USERS identity gets 200 while the authorizer says "no",
#   an unlisted identity still gets 403, and the override does not leak to a
#   non-admin group.
# - Emergency: with the authorizer's port closed (service down), the ADMIN_USERS
#   identity still gets 200 and everyone else still gets 403.
# - AUTH_STATIC_GROUPS keeps its explicit 'group:user1,user2' syntax: a bare
#   group name is skipped with a warning naming ADMIN_GROUP, and a static
#   mapping still yields to a configured authorizer.
# - Backend auth unit suite passes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr946"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0
AUTHORIZER_PID=""

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

cleanup() {
    [ -n "$AUTHORIZER_PID" ] && kill "$AUTHORIZER_PID" 2>/dev/null
}
trap cleanup EXIT

print_header "PR #946: admin via ADMIN_USERS or dynamic ADMIN_GROUP"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"
cd "$PROJECT_ROOT" || exit 1

# A real authorization service. It answers "is_member" from the VERDICT it was
# started with, so the app's outbound POST is a genuine HTTP round trip.
start_authorizer() {
    local verdict="$1"
    local port_file
    port_file="$(mktemp)"
    VERDICT="$verdict" PORT_FILE="$port_file" python3 - <<'PY' &
import json, os, socket
from http.server import BaseHTTPRequestHandler, HTTPServer

verdict = os.environ["VERDICT"] == "true"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        req = json.loads(body or b"{}")
        # Only vouch for the identity the dynamic-membership cases use, so an
        # "allow" verdict cannot accidentally paper over a denial elsewhere.
        member = verdict and req.get("user_id") == "dynamic@example.org"
        payload = json.dumps({"is_member": member}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
with open(os.environ["PORT_FILE"], "w") as fh:
    fh.write(str(server.server_address[1]))
server.serve_forever()
PY
    AUTHORIZER_PID=$!
    for _ in $(seq 1 50); do
        AUTHORIZER_PORT="$(cat "$port_file")"
        [ -n "$AUTHORIZER_PORT" ] && break
        sleep 0.1
    done
    rm -f "$port_file"
    if [ -z "$AUTHORIZER_PORT" ]; then
        echo "FATAL: local authorization service did not start"
        exit 1
    fi
    export AUTH_GROUP_CHECK_URL="http://127.0.0.1:$AUTHORIZER_PORT/check"
}

stop_authorizer() {
    [ -n "$AUTHORIZER_PID" ] && kill "$AUTHORIZER_PID" 2>/dev/null
    wait "$AUTHORIZER_PID" 2>/dev/null
    AUTHORIZER_PID=""
}

# Hit the real admin route with the real app, under the given fixture.
# Echoes "<identity>=<status> ..." for each identity passed after the fixture.
admin_statuses() {
    local fixture="$1"
    shift
    (
        set -a
        # shellcheck disable=SC1090
        source "$fixture"
        set +a
        IDENTITIES="$*" ATLAS_DIR="$ATLAS_DIR" python3 - <<'PY' 2>/dev/null | tail -1
import logging, os, sys
logging.disable(logging.CRITICAL)

# atlas/main.py needs to be importable as "main" (same shim as PR #512's script).
sys.path.insert(0, os.environ["ATLAS_DIR"])

from main import app
from starlette.testclient import TestClient

client = TestClient(app)
out = []
for identity in os.environ["IDENTITIES"].split():
    resp = client.get("/admin/banners", headers={"X-User-Email": identity})
    out.append(f"{identity}={resp.status_code}")
print(" ".join(out))
PY
    )
}

# ==========================================
print_header "1. Dynamic ADMIN_GROUP membership, nothing enumerated"
# ==========================================
start_authorizer true
RESULT="$(admin_statuses "$FIXTURES_DIR/.env.dynamic" dynamic@example.org)"
echo "  $RESULT"
[ "$RESULT" = "dynamic@example.org=200" ]
print_result $? "Authorizer's 'yes' for ADMIN_GROUP grants admin with no ADMIN_USERS/AUTH_STATIC_GROUPS"
stop_authorizer

start_authorizer false
RESULT="$(admin_statuses "$FIXTURES_DIR/.env.dynamic" dynamic@example.org)"
echo "  $RESULT"
[ "$RESULT" = "dynamic@example.org=403" ]
print_result $? "Authorizer's 'no' still denies (the dynamic check is real, not assumed)"
stop_authorizer

# ==========================================
print_header "2. ADMIN_USERS overrides a denying authorizer"
# ==========================================
start_authorizer false
RESULT="$(admin_statuses "$FIXTURES_DIR/.env.override" alice@example.org mallory@example.org)"
echo "  $RESULT"
[ "$RESULT" = "alice@example.org=200 mallory@example.org=403" ]
print_result $? "Listed admin granted, unlisted identity still denied"
stop_authorizer

# ==========================================
print_header "3. ADMIN_USERS survives an authorizer that is down"
# ==========================================
# Start and immediately stop the service: AUTH_GROUP_CHECK_URL points at a
# closed port, so every membership POST fails at the transport layer.
start_authorizer false
stop_authorizer
RESULT="$(admin_statuses "$FIXTURES_DIR/.env.override" alice@example.org mallory@example.org)"
echo "  $RESULT"
[ "$RESULT" = "alice@example.org=200 mallory@example.org=403" ]
print_result $? "Emergency allowlist works while the authorization service is unreachable"
unset AUTH_GROUP_CHECK_URL

# ==========================================
print_header "4. The override is scoped to ADMIN_GROUP only"
# ==========================================
start_authorizer false
RESULT=$(
    set -a
    # shellcheck disable=SC1090
    source "$FIXTURES_DIR/.env.override"
    set +a
    python3 - <<'PY' 2>/dev/null | tail -1
import asyncio, logging
logging.disable(logging.CRITICAL)
from atlas.core.auth import is_user_in_group

admin = asyncio.run(is_user_in_group("alice@example.org", "atlas_admins"))
other = asyncio.run(is_user_in_group("alice@example.org", "mcp_advanced"))
print(f"admin={admin} mcp_advanced={other}")
PY
)
echo "  $RESULT"
[ "$RESULT" = "admin=True mcp_advanced=False" ]
print_result $? "ADMIN_USERS grants ADMIN_GROUP and nothing else"
stop_authorizer
unset AUTH_GROUP_CHECK_URL

# ==========================================
print_header "5. AUTH_STATIC_GROUPS still requires 'group:user1,user2'"
# ==========================================
RESULT=$(python3 - <<'PY' 2>&1 | tail -2
import logging
logging.basicConfig(level=logging.WARNING)
from atlas.modules.config.settings import parse_static_groups

table = parse_static_groups("atlas_admins", admin_users="", admin_group="atlas_admins")
print(f"table={table}")
PY
)
echo "$RESULT" | grep -q "Ignoring malformed AUTH_STATIC_GROUPS entry"
print_result $? "A bare group name is still skipped with a warning"
echo "$RESULT" | grep -q "ADMIN_GROUP instead"
print_result $? "The warning points operators at ADMIN_GROUP for the dynamic case"
echo "$RESULT" | grep -q "table={}"
print_result $? "The bare name does not become a group"

# ==========================================
print_header "6. AUTH_STATIC_GROUPS still yields to the authorizer"
# ==========================================
start_authorizer false
RESULT=$(
    AUTH_GROUP_CHECK_API_KEY=validation-key \
    DEBUG_MODE=false FEATURE_AGENT_PORTAL_ENABLED=false \
    ADMIN_GROUP=atlas_admins ADMIN_USERS= \
    AUTH_STATIC_GROUPS=atlas_admins:carol@example.org \
    python3 - <<'PY' 2>/dev/null | tail -1
import asyncio, logging
logging.disable(logging.CRITICAL)
from atlas.core.auth import is_user_in_group

print(asyncio.run(is_user_in_group("carol@example.org", "atlas_admins")))
PY
)
echo "  static-mapped admin under a denying authorizer: $RESULT"
[ "$RESULT" = "False" ]
print_result $? "A static mapping does not add a grant behind the authorizer's back"
stop_authorizer
unset AUTH_GROUP_CHECK_URL

# ==========================================
print_header "7. Backend auth unit suite"
# ==========================================
# Capture pytest's own status, not tail's: `pytest | tail` would report PASSED
# even if every test failed.
PYTEST_OUT="$(python -m pytest atlas/tests/test_core_auth.py -q 2>&1)"
PYTEST_STATUS=$?
echo "$PYTEST_OUT" | tail -2
print_result "$PYTEST_STATUS" "atlas/tests/test_core_auth.py passes"

print_header "Summary"
echo -e "${GREEN}Passed: $PASSED${NC}"
echo -e "${RED}Failed: $FAILED${NC}"
[ "$FAILED" -eq 0 ]
