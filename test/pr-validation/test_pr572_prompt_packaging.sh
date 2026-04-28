#!/bin/bash
# Test script for PR #572: package top-level prompt markdown files.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0
EXPECTED_PROMPTS=(
    "agent_observe_prompt.md"
    "agent_reason_prompt.md"
    "agent_summary_prompt.md"
    "agent_system_prompt.md"
    "system_prompt.md"
    "tool_synthesis_prompt.md"
)

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
EXPECTED_PROMPTS_FILE="$TMPDIR/expected-prompts.txt"
printf '%s\n' "${EXPECTED_PROMPTS[@]}" > "$EXPECTED_PROMPTS_FILE"

rm -rf "$PROJECT_ROOT/dist"
if uv build --wheel > "$TMPDIR/build.log" 2>&1; then
    print_result 0 "Wheel builds with uv"
else
    print_result 1 "Wheel builds with uv"
fi

WHEEL=""
if [ -d "$PROJECT_ROOT/dist" ]; then
    WHEEL=$(find "$PROJECT_ROOT/dist" -maxdepth 1 -name "atlas_chat-*.whl" | head -n 1)
fi
if [ -n "$WHEEL" ]; then
    print_result 0 "Built wheel artifact exists"
else
    print_result 1 "Built wheel artifact exists"
fi

if [ -n "$WHEEL" ] && python - "$WHEEL" "$EXPECTED_PROMPTS_FILE" <<'PY'
import sys
import zipfile
from pathlib import Path

wheel_path = sys.argv[1]
expected = {f"prompts/{name}" for name in Path(sys.argv[2]).read_text().splitlines()}

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
    if uv venv "$TMPDIR/venv" > /dev/null 2>&1; then
        print_result 0 "Temporary uv venv created"
    else
        print_result 1 "Temporary uv venv created"
    fi

    if uv pip install --python "$TMPDIR/venv/bin/python" "$WHEEL" > "$TMPDIR/install.log" 2>&1; then
        print_result 0 "Wheel installs into temporary venv"
    else
        print_result 1 "Wheel installs into temporary venv"
    fi

    if "$TMPDIR/venv/bin/python" - "$EXPECTED_PROMPTS_FILE" <<'PY'
import importlib.resources
import sys
from pathlib import Path

expected = set(Path(sys.argv[1]).read_text().splitlines())

prompts_root = importlib.resources.files("prompts")
missing = sorted(name for name in expected if not (prompts_root / name).is_file())
if missing:
    raise SystemExit(f"Missing installed prompt resources: {missing}")
PY
    then
        print_result 0 "Installed wheel exposes prompt markdown package resources"
    else
        print_result 1 "Installed wheel exposes prompt markdown package resources"
    fi
fi

if ./test/run_tests.sh backend > "$TMPDIR/backend-tests.log" 2>&1; then
    print_result 0 "Backend unit tests"
else
    print_result 1 "Backend unit tests"
fi

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
