#!/usr/bin/env bash
# PR #366 - Upgrade to fastmcp>=3.0.0
# Validates that the FastMCP 3.x upgrade works correctly with all MCP features.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

# Activate virtual environment
source .venv/bin/activate 2>/dev/null || true

PASS=0
FAIL=0

check() {
    local desc="$1"
    shift
    if "$@" >/dev/null 2>&1; then
        echo "  $desc ... PASS"
        ((PASS++))
    else
        echo "  $desc ... FAIL"
        ((FAIL++))
    fi
}

echo "================================================================"
echo "PR #366 Validation: FastMCP 3.x Upgrade"
echo "================================================================"

# 1. Verify fastmcp>=3.0.0 is installed
echo ""
echo "-- Checking installed fastmcp version --"
FASTMCP_VERSION=$(python -c "import fastmcp; print(fastmcp.__version__)" 2>&1)
echo "  Installed fastmcp version: $FASTMCP_VERSION"
MAJOR=$(echo "$FASTMCP_VERSION" | cut -d. -f1)
if [ "$MAJOR" -ge 3 ]; then
    echo "  FastMCP major version >= 3 ... PASS"
    ((PASS++))
else
    echo "  FastMCP major version >= 3 ... FAIL (got $MAJOR)"
    ((FAIL++))
fi

# 2. Verify pyproject.toml requires >=3.0.0
echo ""
echo "-- Checking pyproject.toml constraint --"
check "pyproject.toml requires fastmcp>=3.0.0" \
    grep -q 'fastmcp>=3.0.0' "$PROJECT_ROOT/pyproject.toml"

# 3. Verify core imports work
echo ""
echo "-- Checking core FastMCP imports --"
check "Import Client and FastMCP" \
    python -c "from fastmcp import Client, FastMCP"

check "Import StreamableHttpTransport" \
    python -c "from fastmcp.client.transports import StreamableHttpTransport"

check "Import StdioTransport" \
    python -c "from fastmcp.client.transports import StdioTransport"

check "Import ElicitResult" \
    python -c "from fastmcp.client.elicitation import ElicitResult"

check "Import ToolError" \
    python -c "from fastmcp.exceptions import ToolError"

check "Import server middleware" \
    python -c "from fastmcp.server.middleware import Middleware, MiddlewareContext"

check "Import server dependencies" \
    python -c "from fastmcp.server.dependencies import get_http_headers"

check "Import ToolResult" \
    python -c "from fastmcp.tools.tool import ToolResult"

check "Import StaticTokenVerifier" \
    python -c "from fastmcp.server.auth.providers.jwt import StaticTokenVerifier"

# 4. Verify Client has expected methods (3.x API)
echo ""
echo "-- Checking Client API compatibility --"
check "Client has list_tools method" \
    python -c "from fastmcp import Client; assert hasattr(Client, 'list_tools')"

check "Client has list_prompts method" \
    python -c "from fastmcp import Client; assert hasattr(Client, 'list_prompts')"

check "Client has call_tool method" \
    python -c "from fastmcp import Client; assert hasattr(Client, 'call_tool')"

check "Client has get_prompt method" \
    python -c "from fastmcp import Client; assert hasattr(Client, 'get_prompt')"

check "Client has set_elicitation_callback method" \
    python -c "from fastmcp import Client; assert hasattr(Client, 'set_elicitation_callback')"

# 5. Verify MCP tool manager can be imported without errors
echo ""
echo "-- Checking atlas MCP module imports --"
check "Import MCPToolManager" \
    python -c "
import os, sys
sys.path.insert(0, '$PROJECT_ROOT')
os.environ.setdefault('APP_CONFIG_DIR', '$PROJECT_ROOT/atlas/config')
from atlas.modules.mcp_tools.client import MCPToolManager
"

# 6. End-to-end: Verify CLI can initialize with MCP tool discovery
echo ""
echo "-- End-to-end: CLI tool listing with calculator MCP server --"
cd "$PROJECT_ROOT/atlas"
LIST_OUTPUT=$(PYTHONPATH="$PROJECT_ROOT" python atlas_chat_cli.py --list-tools \
    --mcp-config "$PROJECT_ROOT/atlas/config/mcp-example-configs/mcp-calculator.json" 2>&1 || true)

if echo "$LIST_OUTPUT" | grep -q "calculator"; then
    echo "  CLI --list-tools discovers calculator server ... PASS"
    ((PASS++))
else
    echo "  CLI --list-tools discovers calculator server ... FAIL"
    echo "  Output: $LIST_OUTPUT"
    ((FAIL++))
fi
cd "$PROJECT_ROOT"

# 7. Run backend unit tests
echo ""
echo "-- Running backend unit tests --"
PYTHONPATH="$PROJECT_ROOT" "$PROJECT_ROOT/test/run_tests.sh" backend 2>&1 | tail -5
BACKEND_EXIT=${PIPESTATUS[0]}
if [ "$BACKEND_EXIT" -eq 0 ]; then
    echo "  Backend tests ... PASS"
    ((PASS++))
else
    echo "  Backend tests ... FAIL"
    ((FAIL++))
fi

echo ""
echo "================================================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================================================"

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
exit 0
