#!/bin/bash
# PR #977 Validation Script: Refresh a single MCP server from the admin panel
# Validates POST /admin/mcp/refresh end to end against a running backend:
# admin gate, unknown server 404, and a real refresh of a configured server.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #977 Validation: Single-Server MCP Refresh"
echo "=========================================="

cd "$PROJECT_ROOT"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

ADMIN_USER="${ADMIN_TEST_USER:-admin@example.com}"

echo ""
echo "1. Verify refresh route and manager method exist"
echo "------------------------------------------------"
if grep -q '"/mcp/refresh"' "$PROJECT_ROOT/atlas/routes/admin_routes.py"; then
    echo "PASSED: /mcp/refresh route defined in admin_routes.py"
else
    echo "FAILED: /mcp/refresh route not found in admin_routes.py"
    exit 1
fi
if grep -q "async def refresh_server" "$PROJECT_ROOT/atlas/modules/mcp_tools/mcp_connection.py"; then
    echo "PASSED: refresh_server() defined in mcp_connection.py"
else
    echo "FAILED: refresh_server() not found in mcp_connection.py"
    exit 1
fi

echo ""
echo "2. Start backend and exercise the endpoint"
echo "---------------------------------------------"
PORT=8210
if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
    echo "FAILED: Port $PORT is already in use by another process"
    exit 1
fi
export PORT=$PORT
export ATLAS_HOST="127.0.0.1"

cd "$PROJECT_ROOT/atlas"
python main.py &
BACKEND_PID=$!
cd "$PROJECT_ROOT"

for i in $(seq 1 60); do
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo "FAILED: Backend process (PID $BACKEND_PID) exited unexpectedly"
        exit 1
    fi
    if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
        echo "  Backend ready after ${i}s"
        break
    fi
    if [ "$i" -eq 60 ]; then
        echo "FAILED: Backend failed to become ready in time"
        kill $BACKEND_PID 2>/dev/null || true
        exit 1
    fi
    sleep 1
done

cleanup() {
    kill $BACKEND_PID 2>/dev/null || true
    wait $BACKEND_PID 2>/dev/null || true
}
trap cleanup EXIT

echo ""
echo "3. Non-admin user is denied (403)"
echo "---------------------------------"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "http://127.0.0.1:$PORT/admin/mcp/refresh" \
    -H "X-User-Email: nonadmin@example.com" \
    -H "Content-Type: application/json" \
    -d '{"server_name": "whatever"}')
if [ "$STATUS" = "403" ]; then
    echo "PASSED: non-admin refresh attempt returned 403"
else
    echo "FAILED: expected 403 for non-admin, got $STATUS"
    exit 1
fi

echo ""
echo "4. Unknown server returns 404"
echo "-----------------------------"
RESP=$(curl -s -X POST "http://127.0.0.1:$PORT/admin/mcp/refresh" \
    -H "X-User-Email: $ADMIN_USER" \
    -H "Content-Type: application/json" \
    -d '{"server_name": "pr977-no-such-server"}')
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "http://127.0.0.1:$PORT/admin/mcp/refresh" \
    -H "X-User-Email: $ADMIN_USER" \
    -H "Content-Type: application/json" \
    -d '{"server_name": "pr977-no-such-server"}')
if [ "$STATUS" = "404" ]; then
    echo "PASSED: unknown server returned 404 ($RESP)"
else
    echo "FAILED: expected 404 for unknown server, got $STATUS ($RESP)"
    exit 1
fi

echo ""
echo "5. Refresh a configured server end to end"
echo "-----------------------------------------"
# Pick any server from the active admin status (the dev config has at least one).
SERVER_NAME=$(curl -s "http://127.0.0.1:$PORT/admin/mcp/status" \
    -H "X-User-Email: $ADMIN_USER" | python3 -c "
import sys, json
d = json.load(sys.stdin)
configured = d.get('configured_servers') or []
print(configured[0] if configured else '')
")
if [ -z "$SERVER_NAME" ]; then
    echo "FAILED: no configured MCP servers found in /admin/mcp/status"
    exit 1
fi
echo "  Refreshing configured server: $SERVER_NAME"

RESP=$(curl -s -X POST "http://127.0.0.1:$PORT/admin/mcp/refresh" \
    -H "X-User-Email: $ADMIN_USER" \
    -H "Content-Type: application/json" \
    -d "{\"server_name\": \"$SERVER_NAME\"}")
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    "http://127.0.0.1:$PORT/admin/mcp/refresh" \
    -H "X-User-Email: $ADMIN_USER" \
    -H "Content-Type: application/json" \
    -d "{\"server_name\": \"$SERVER_NAME\"}")

if [ "$STATUS" != "200" ]; then
    echo "FAILED: refresh of '$SERVER_NAME' returned HTTP $STATUS: $RESP"
    exit 1
fi
echo "$RESP" | python3 -c "
import sys, json
d = json.load(sys.stdin)
r = d['result']
assert r['server'] == '$SERVER_NAME', r
assert r['status'] in ('connected', 'failed', 'removed'), r
assert isinstance(r['tools'], int) and isinstance(r['prompts'], int), r
assert 'config_changed' in r and 'evicted_clients' in r, r
assert '$SERVER_NAME' in d['servers'], r
print(f\"  result: status={r['status']} tools={r['tools']} prompts={r['prompts']} config_changed={r['config_changed']}\")
"
echo "PASSED: refresh of '$SERVER_NAME' returned a valid result"

echo ""
echo "6. Dashboard lists the refresh endpoint"
echo "---------------------------------------"
if curl -s "http://127.0.0.1:$PORT/admin/" -H "X-User-Email: $ADMIN_USER" | \
    grep -q '"/admin/mcp/refresh"'; then
    echo "PASSED: /admin/mcp/refresh listed in the admin dashboard"
else
    echo "FAILED: /admin/mcp/refresh missing from admin dashboard"
    exit 1
fi

cleanup

echo ""
echo "7. Run backend unit tests"
echo "-------------------------"
cd "$PROJECT_ROOT"
bash ./test/run_tests.sh backend > /dev/null 2>&1 || bash ./test/run_tests.sh backend
echo "PASSED: Backend tests passed"

echo ""
echo "=========================================="
echo "All PR #977 validation checks PASSED"
echo "=========================================="
