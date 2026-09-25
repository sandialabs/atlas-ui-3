#!/usr/bin/env bash
# Test script for PR #973: Harden 0.6.0 release follow-ups
#
# What this validates:
# - The changelog release-heading guard passes a valid diff and fails a
#   change that drops a released heading.
# - atlas-chat surfaces LLM stream failures as classified exceptions only
#   when raise_on_llm_error is opted in (CLI passes it; library callers keep
#   graceful ChatResults), including the fallback-before-raise ordering.
# - Packaged mcp.json disables the demo servers and the MCP tool manager
#   filters enabled: false entries out of servers_config so they are never
#   started or probed.
# - The release version sources agree (pyproject.toml vs atlas/version.py)
#   and uv.lock is in sync, mirroring the pypi-publish workflow checks.
# - Agent-mode streaming paths honor the same failure policy.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_header() { echo ""; echo "=== $1 ==="; }
print_result() {
    if [ "$1" -eq 0 ]; then
        echo "  PASSED: $2"; PASSED=$((PASSED + 1))
    else
        echo "  FAILED: $2"; FAILED=$((FAILED + 1))
    fi
}

echo "=== PR #973 Validation: release hardening follow-ups ==="

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"
cd "$PROJECT_ROOT"

# ==========================================
print_header "Check 1: changelog guard accepts a valid edit"
# ==========================================
python3 scripts/check_changelog_release_headings.py CHANGELOG.md CHANGELOG.md > /dev/null 2>&1
print_result $? "guard passes when no released heading is dropped"

# ==========================================
print_header "Check 2: changelog guard rejects a dropped released heading"
# ==========================================
TMP_BASE="$(mktemp)"
TMP_HEAD="$(mktemp)"
trap 'rm -f "$TMP_BASE" "$TMP_HEAD"' EXIT
cat > "$TMP_BASE" <<'EOF'
# Changelog

## [0.6.0] - 2026-09-22

### PR #963 - 2026-09-20
- Something.

## [0.5.0] - 2026-09-01

### PR #900 - 2026-08-30
- Something older.

## [Unreleased]
EOF
cat > "$TMP_HEAD" <<'EOF'
# Changelog

## [Unreleased]

### PR #973 - 2026-09-24
- New entry that accidentally drops the 0.5.0 released heading.
EOF
if python3 scripts/check_changelog_release_headings.py "$TMP_BASE" "$TMP_HEAD" > /dev/null 2>&1; then
    print_result 1 "guard fails when a released heading is dropped"
else
    print_result 0 "guard fails when a released heading is dropped"
fi

# ==========================================
print_header "Check 3: CLI failure policy is opt-in and fallback-aware"
# ==========================================
python3 - <<'PY' 2>&1 | tail -4
import asyncio
from unittest.mock import AsyncMock

from atlas.application.chat.modes.streaming_helpers import stream_and_accumulate
from atlas.domain.errors import LLMServiceError

async def main():
    calls = {}

    def stream(name):
        async def gen():
            raise RuntimeError("provider down")
            yield ""
        return gen()

    # Opt-in: classified exception surfaces.
    try:
        await stream_and_accumulate(
            token_generator=stream("raise"),
            event_publisher=AsyncMock(),
            context_label="check",
            raise_on_stream_error=True,
        )
        calls["raised"] = False
    except LLMServiceError:
        calls["raised"] = True

    # Default (library callers): graceful synthesized message.
    result = await stream_and_accumulate(
        token_generator=stream("default"),
        event_publisher=AsyncMock(),
        context_label="check",
    )
    calls["default_graceful"] = isinstance(result, str) and bool(result)

    # Opt-in with fallback: the non-streaming retry runs before raising.
    async def fallback_ok():
        return "recovered"

    recovered = await stream_and_accumulate(
        token_generator=stream("fallback"),
        event_publisher=AsyncMock(),
        fallback_fn=fallback_ok,
        context_label="check",
        raise_on_stream_error=True,
    )
    calls["fallback_before_raise"] = recovered == "recovered"

    ok = calls["raised"] and calls["default_graceful"] and calls["fallback_before_raise"]
    print("RESULT:", "PASS" if ok else f"FAIL {calls}")
    raise SystemExit(0 if ok else 1)

asyncio.run(main())
PY
print_result $? "raise_on_stream_error raises, default stays graceful, fallback retries first"

# ==========================================
print_header "Check 4: disabled demo servers are never initialized or probed"
# ==========================================
python3 - <<'PY' 2>&1 | tail -4
import json
import pathlib
import tempfile

from atlas.modules.config.config_manager import MCPConfig
from atlas.modules.mcp_tools.client import MCPToolManager

# The packaged defaults must mark both demo servers disabled -- read the
# shipped file directly so the assertion is not environment-dependent.
packaged = json.loads(
    (pathlib.Path("atlas/config/mcp.json")).read_text(encoding="utf-8")
)
mcp_config = MCPConfig(**{"servers": packaged})
disabled = [
    name for name, server in mcp_config.servers.items()
    if server.enabled is False
]
assert "pptx_generator" in disabled and "session_state_demo" in disabled, disabled

# A manager built from a config carrying enabled:false entries must keep
# them out of servers_config (initialize_clients iterates exactly that).
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
    json.dump(packaged, fh)
    path = fh.name
manager = MCPToolManager(config_path=path)
leftover = [name for name in ("pptx_generator", "session_state_demo")
            if name in manager.servers_config]
assert not leftover, leftover
print("RESULT: PASS")
raise SystemExit(0)
PY
print_result $? "enabled:false entries are filtered out of servers_config"

# ==========================================
print_header "Check 5: agent streaming paths honor the failure policy"
# ==========================================
python3 - <<'PY' 2>&1 | tail -4
import asyncio
from unittest.mock import AsyncMock, MagicMock

from atlas.application.chat.agent.streaming_final_answer import stream_final_answer
from atlas.domain.errors import DomainError, LLMServiceError

async def main():
    llm = MagicMock()

    async def broken_stream(*args, **kwargs):
        raise RuntimeError("AuthenticationError: nope")
        yield ""

    async def broken_call(*args, **kwargs):
        raise RuntimeError("fallback down too")

    llm.stream_plain = broken_stream
    llm.call_plain = broken_call

    try:
        await stream_final_answer(
            llm, AsyncMock(), "m", [], 0.7, None,
            raise_on_stream_error=True,
        )
        print("RESULT: FAIL (no raise)")
        raise SystemExit(1)
    except (LLMServiceError, DomainError):
        print("RESULT: PASS")
        raise SystemExit(0)

asyncio.run(main())
PY
print_result $? "stream_final_answer raises when both stream and retry fail"

# ==========================================
print_header "Check 6: version sources agree and the lockfile is current"
# ==========================================
python3 - <<'PY' 2>&1 | tail -3
import re
import pathlib
import tomllib

pyproject = tomllib.loads(pathlib.Path("pyproject.toml").read_text())["project"]["version"]
version_py = re.search(
    r'^VERSION\s*=\s*"([^"]+)"',
    pathlib.Path("atlas/version.py").read_text(),
    re.MULTILINE,
).group(1)
assert pyproject == version_py, f"pyproject={pyproject} version.py={version_py}"
print(f"RESULT: PASS ({pyproject})")
raise SystemExit(0)
PY
print_result $? "pyproject.toml and atlas/version.py agree"

if command -v uv > /dev/null 2>&1; then
    uv lock --check > /dev/null 2>&1
    print_result $? "uv.lock is in sync with pyproject.toml"
else
    echo "  SKIPPED: uv not installed"
fi

# ==========================================
print_header "Check 7: atlas-chat exits non-zero at the process boundary"
# ==========================================
# The fixture llmconfig points at an unreachable endpoint, so the stream and
# the fallback retry both fail immediately. Both the streaming and --json
# invocations must exit 1, and --json must leave stdout empty.
FIXTURE_CONFIG="$PROJECT_ROOT/test/pr-validation/fixtures/pr973"
timeout 120 python3 atlas/atlas_chat_cli.py "hi" --model unreachable-fixture-model \
    --config-dir "$FIXTURE_CONFIG" > /tmp/pr973_cli_stdout.txt 2>/tmp/pr973_cli_stderr.txt
CLI_EXIT=$?
if [ "$CLI_EXIT" -ne 0 ]; then
    print_result 0 "streaming run exits non-zero on LLM failure (exit=$CLI_EXIT)"
else
    print_result 1 "streaming run exits non-zero on LLM failure (exit=$CLI_EXIT)"
fi
if tail -1 /tmp/pr973_cli_stderr.txt | grep -q "Error: The LLM service encountered an error"; then
    print_result 0 "streaming failure surfaces the classified error, not a crash"
else
    print_result 1 "streaming failure surfaces the classified error, not a crash"
fi

timeout 120 python3 atlas/atlas_chat_cli.py "hi" --model unreachable-fixture-model \
    --config-dir "$FIXTURE_CONFIG" --json > /tmp/pr973_cli_json_stdout.txt 2>/dev/null
CLI_JSON_EXIT=$?
if [ "$CLI_JSON_EXIT" -ne 0 ]; then
    print_result 0 "--json run exits non-zero on LLM failure (exit=$CLI_JSON_EXIT)"
else
    print_result 1 "--json run exits non-zero on LLM failure (exit=$CLI_JSON_EXIT)"
fi
if [ ! -s /tmp/pr973_cli_json_stdout.txt ]; then
    print_result 0 "--json failure prints nothing to stdout"
else
    print_result 1 "--json failure prints nothing to stdout"
fi

# ==========================================
print_header "Final: backend unit tests"
# ==========================================
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
