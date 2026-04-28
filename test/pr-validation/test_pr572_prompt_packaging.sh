#!/bin/bash
# Test script for PR #572: package top-level prompt markdown files.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

echo "Building wheel and verifying packaged prompt resources"

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

rm -rf "$PROJECT_ROOT/dist"
uv build --wheel > "$TMPDIR/build.log" 2>&1
print_result $? "Wheel builds with uv"

WHEEL=$(find "$PROJECT_ROOT/dist" -maxdepth 1 -name "atlas_chat-*.whl" | head -n 1)
if [ -n "$WHEEL" ]; then
    print_result 0 "Built wheel artifact exists"
else
    print_result 1 "Built wheel artifact exists"
fi

if [ -n "$WHEEL" ] && python - "$WHEEL" <<'PY'
import sys
import zipfile

wheel_path = sys.argv[1]
expected = {
    "prompts/agent_observe_prompt.md",
    "prompts/agent_reason_prompt.md",
    "prompts/agent_summary_prompt.md",
    "prompts/agent_system_prompt.md",
    "prompts/system_prompt.md",
    "prompts/tool_synthesis_prompt.md",
}

with zipfile.ZipFile(wheel_path) as wheel:
    names = set(wheel.namelist())

missing = sorted(expected - names)
if missing:
    raise SystemExit(f"Missing prompt files from wheel: {missing}")
PY
then
    print_result 0 "Wheel contains prompt markdown files"
else
    print_result 1 "Wheel contains prompt markdown files"
fi

if [ -n "$WHEEL" ]; then
    uv venv "$TMPDIR/venv" > /dev/null 2>&1
    print_result $? "Temporary uv venv created"

    uv pip install --python "$TMPDIR/venv/bin/python" "$WHEEL" > "$TMPDIR/install.log" 2>&1
    print_result $? "Wheel installs into temporary venv"

    "$TMPDIR/venv/bin/python" - <<'PY'
import importlib.resources

expected = {
    "agent_observe_prompt.md",
    "agent_reason_prompt.md",
    "agent_summary_prompt.md",
    "agent_system_prompt.md",
    "system_prompt.md",
    "tool_synthesis_prompt.md",
}

prompts_root = importlib.resources.files("prompts")
missing = sorted(name for name in expected if not (prompts_root / name).is_file())
if missing:
    raise SystemExit(f"Missing installed prompt resources: {missing}")
PY
    print_result $? "Installed wheel exposes prompt markdown package resources"
fi

./test/run_tests.sh backend > "$TMPDIR/backend-tests.log" 2>&1
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
