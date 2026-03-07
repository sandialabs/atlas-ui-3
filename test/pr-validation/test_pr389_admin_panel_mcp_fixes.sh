#!/bin/bash
# PR #389 - Fix admin panel MCP server status, reload, and config path display
# Date: 2026-03-07

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

cd "$PROJECT_ROOT"
source .venv/bin/activate 2>/dev/null || true

echo "=== PR #389: Admin Panel MCP Fixes ==="
echo ""

# 1. Verify reload_servers() is NOT called anywhere in admin_routes.py
echo "--- Check 1: No reload_servers() calls in admin_routes.py ---"
if grep -q "reload_servers()" atlas/routes/admin_routes.py; then
    fail "admin_routes.py still contains reload_servers() calls"
else
    pass "No reload_servers() calls in admin_routes.py"
fi

# 2. Verify the proper reload sequence is used in add-server endpoint
echo "--- Check 2: Add-server uses proper reload sequence ---"
if grep -A5 "mcp_manager.reload_config()" atlas/routes/admin_routes.py | grep -q "initialize_clients"; then
    pass "Add-server uses reload_config + initialize_clients sequence"
else
    fail "Add-server does not use proper reload sequence"
fi

# 3. Verify mcp_config_path is included in status endpoint
echo "--- Check 3: Status endpoint includes mcp_config_path ---"
if grep -q '"mcp_config_path"' atlas/routes/admin_routes.py; then
    pass "Status endpoint returns mcp_config_path"
else
    fail "Status endpoint missing mcp_config_path"
fi

# 4. Verify active-servers endpoint includes config_path
echo "--- Check 4: Active-servers endpoint includes config_path ---"
if grep -q '"config_path"' atlas/routes/admin_routes.py; then
    pass "Active-servers endpoint returns config_path"
else
    fail "Active-servers endpoint missing config_path"
fi

# 5. Verify failed servers are excluded from connected list in status
echo "--- Check 5: Status excludes failed servers from connected ---"
if grep -q "if server_name in failed_servers:" atlas/routes/admin_routes.py; then
    pass "Status endpoint excludes failed servers from connected list"
else
    fail "Status endpoint does not filter failed servers"
fi

# 6. Verify tool/prompt counts are filtered by configured servers
echo "--- Check 6: Tool counts filtered by configured servers ---"
if grep -q "if k in configured_set" atlas/routes/admin_routes.py; then
    pass "Tool/prompt counts are filtered by configured_set"
else
    fail "Tool/prompt counts are not filtered"
fi

# 7. Verify reload_config cleans up removed servers
echo "--- Check 7: reload_config cleans up removed servers ---"
if grep -q "clients.pop(server_name" atlas/modules/mcp_tools/client.py && \
   grep -q "available_tools.pop(server_name" atlas/modules/mcp_tools/client.py && \
   grep -q "available_prompts.pop(server_name" atlas/modules/mcp_tools/client.py; then
    pass "reload_config cleans up removed servers from clients/tools/prompts"
else
    fail "reload_config does not clean up removed servers"
fi

# 8. Verify frontend shows config path
echo "--- Check 8: Frontend shows config path ---"
if grep -q "mcp_config_path" frontend/src/components/admin/MCPConfigurationCard.jsx; then
    pass "MCPConfigurationCard displays mcp_config_path"
else
    fail "MCPConfigurationCard does not show config path"
fi

# 9. Verify MCPServerManager shows config write path
echo "--- Check 9: MCPServerManager shows write path ---"
if grep -q "Writes to:" frontend/src/components/admin/MCPServerManager.jsx; then
    pass "MCPServerManager shows 'Writes to:' config path"
else
    fail "MCPServerManager does not show write path"
fi

# 10. Verify frontend shows per-server tool/prompt counts
echo "--- Check 10: Frontend shows per-server tool counts ---"
if grep -q "tool_counts" frontend/src/components/admin/MCPConfigurationCard.jsx; then
    pass "MCPConfigurationCard shows tool_counts per server"
else
    fail "MCPConfigurationCard does not show tool counts"
fi

# 11. Run backend tests
echo ""
echo "--- Check 11: Backend unit tests ---"
cd "$PROJECT_ROOT"
BACKEND_OUTPUT=$(.venv/bin/python -m pytest atlas/tests/test_admin_mcp_server_management_routes.py atlas/tests/test_mcp_hot_reload.py -q 2>&1)
if echo "$BACKEND_OUTPUT" | grep -q "passed"; then
    pass "Backend tests pass"
else
    fail "Backend tests failed"
    echo "$BACKEND_OUTPUT" | tail -5
fi

echo ""
echo "================================================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================================================"

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
