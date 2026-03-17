#!/usr/bin/env bash
# PR #434 - Runtime path writability validation
#
# Validates:
# 1. atlas-server fails fast with a clear APP_LOG_DIR error when the log path is not writable
# 2. atlas-server fails fast with a clear CHAT_HISTORY_DB_URL error when DuckDB storage is not writable
# 3. Backend unit tests pass

set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

pass() { echo "  PASSED: $1"; ((PASSED++)); }
fail() { echo "  FAILED: $1"; ((FAILED++)); }

run_expect_failure() {
    local env_file="$1"
    local output_file="$2"

    "$PROJECT_ROOT/.venv/bin/python" - "$env_file" "$output_file" <<'PY'
import subprocess
import sys
from pathlib import Path

env_file = Path(sys.argv[1])
output_file = Path(sys.argv[2])

with output_file.open("w", encoding="utf-8") as stream:
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "atlas.server_cli",
                "--env",
                str(env_file),
                "--port",
                "8099",
            ],
            cwd=Path.cwd(),
            stdout=stream,
            stderr=subprocess.STDOUT,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired:
        sys.exit(2)

if result.returncode == 0:
    sys.exit(1)
sys.exit(0)
PY
}

echo "=== PR #434: Runtime Path Writability Validation ==="
echo ""

cd "$PROJECT_ROOT"
source .venv/bin/activate

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

printf '%s\n' "DEBUG_MODE=true" "OPENAI_API_KEY=test" > "$TMP_DIR/base.env"

# --- Check 1: invalid APP_LOG_DIR fails fast ---
echo "--- Check 1: APP_LOG_DIR startup validation ---"
touch "$TMP_DIR/not-a-directory"
printf '%s\n' \
    "DEBUG_MODE=true" \
    "OPENAI_API_KEY=test" \
    "FEATURE_CHAT_HISTORY_ENABLED=false" \
    "APP_LOG_DIR=$TMP_DIR/not-a-directory" \
    > "$TMP_DIR/invalid-log.env"

if run_expect_failure "$TMP_DIR/invalid-log.env" "$TMP_DIR/invalid-log.out" && \
    grep -q "APP_LOG_DIR" "$TMP_DIR/invalid-log.out"; then
    pass "atlas-server rejects invalid APP_LOG_DIR with clear error"
else
    echo "  Output:"
    sed -n '1,40p' "$TMP_DIR/invalid-log.out"
    fail "atlas-server did not fail as expected for APP_LOG_DIR"
fi

# --- Check 2: invalid DuckDB parent fails fast ---
echo "--- Check 2: CHAT_HISTORY_DB_URL startup validation ---"
touch "$TMP_DIR/blocked-parent"
printf '%s\n' \
    "DEBUG_MODE=true" \
    "OPENAI_API_KEY=test" \
    "FEATURE_CHAT_HISTORY_ENABLED=true" \
    "CHAT_HISTORY_DB_URL=duckdb:///$TMP_DIR/blocked-parent/chat_history.db" \
    > "$TMP_DIR/invalid-duckdb.env"

if run_expect_failure "$TMP_DIR/invalid-duckdb.env" "$TMP_DIR/invalid-duckdb.out" && \
    grep -q "CHAT_HISTORY_DB_URL" "$TMP_DIR/invalid-duckdb.out"; then
    pass "atlas-server rejects invalid DuckDB storage path with clear error"
else
    echo "  Output:"
    sed -n '1,40p' "$TMP_DIR/invalid-duckdb.out"
    fail "atlas-server did not fail as expected for CHAT_HISTORY_DB_URL"
fi

# --- Check 3: backend tests ---
echo "--- Check 3: Backend unit tests ---"
if ./test/run_tests.sh backend > /dev/null 2>&1; then
    pass "Backend unit tests"
else
    fail "Backend unit tests"
fi

echo ""
echo "=== Results: $PASSED passed, $FAILED failed ==="
exit $FAILED
