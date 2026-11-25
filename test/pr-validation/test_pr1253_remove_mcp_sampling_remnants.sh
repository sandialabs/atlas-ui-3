#!/bin/bash
# Validation for PR #1253: no MCP sampling artifacts remain in tracked files.
# Checks the tree for sampling identifiers and files, confirms the live MCP
# client code builds no sampling handler, and runs the MCP authorization tests.
#
# Not flagged: LLM temperature / context-compaction wording and load-test
# resource sampling (unrelated meanings), CHANGELOG.md (generated history), the
# archived FastMCP 3.x upgrade plans, and the fastmcp pin comments in
# pyproject.toml / .github/dependabot.yml (tracked in issue #905).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT" || exit 1
FAILED=0

pass() { echo "PASS $1"; }
fail() { echo "FAIL $1"; FAILED=$((FAILED+1)); }

# 1. No tracked file is named after sampling.
NAMED=$(git ls-files | grep -iE 'sampling|tool_planner' | grep -v "^test/pr-validation/test_pr1253_")
if [ -z "$NAMED" ]; then pass "no tracked file named after sampling"; else fail "sampling-named files: $NAMED"; fi

# 2. No MCP sampling identifiers anywhere outside generated/archived history.
IDENT='sampling_handler|sampling_callback|_sampling_routing|_SamplingRoutingContext|_create_sampling_handler|_use_sampling_context|CreateMessageResult|SamplingMessage|sampling/createMessage|ctx\.sample\(|sampling_demo|tool_planner|MCP sampling|server-initiated LLM'
HITS=$(git grep -nE "$IDENT" -- . \
    ':!CHANGELOG.md' ':!docs/archive' ':!pyproject.toml' ':!.github/dependabot.yml' \
    ':!changes/1253.internal.md' ':!test/pr-validation/test_pr1253_remove_mcp_sampling_remnants.sh')
if [ -z "$HITS" ]; then pass "no MCP sampling identifiers or notes"; else fail "MCP sampling references remain:"; echo "$HITS"; fi

# 3. Every remaining "sampling" mention is one of the unrelated meanings.
OTHER=$(git grep -niE 'sampling' -- . \
    ':!CHANGELOG.md' ':!docs/archive' ':!pyproject.toml' ':!.github/dependabot.yml' ':!uv.lock' \
    ':!changes/1253.internal.md' ':!test/pr-validation/test_pr1253_remove_mcp_sampling_remnants.sh' \
    | grep -viE 'deterministic|sampling temperature|resource sampling|^test/loadtest/')
if [ -z "$OTHER" ]; then pass "remaining 'sampling' wording is LLM/load-test only"; else fail "unexpected 'sampling' wording:"; echo "$OTHER"; fi

# 4. The live MCP tool manager exposes no sampling surface, and the MCP
#    authorization suite still passes.
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
if python - <<'PY'
import inspect

from atlas.modules.mcp_tools import mcp_connection, mcp_execution, mcp_routing
from atlas.modules.mcp_tools.client import MCPToolManager

names = [n for n in dir(MCPToolManager) if "sampl" in n.lower()]
names += [n for n in dir(mcp_routing) if "sampl" in n.lower()]
assert not names, names
for module in (mcp_connection, mcp_execution, mcp_routing):
    assert "sampl" not in inspect.getsource(module).lower(), module.__name__
PY
then pass "MCPToolManager and its client construction have no sampling surface"; else fail "sampling surface found in MCP client code"; fi

if (cd "$ROOT/atlas" && python -m pytest tests/test_mcp_authorization.py -q -p no:cacheprovider >/dev/null 2>&1); then
    pass "tests/test_mcp_authorization.py"
else
    fail "tests/test_mcp_authorization.py"
fi

echo
if [ "$FAILED" -eq 0 ]; then echo "ALL CHECKS PASSED"; exit 0; fi
echo "$FAILED check(s) failed"; exit 1
