#!/bin/bash
# PR #1017: exercise secure initialization and fail-closed server startup.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source "$PROJECT_ROOT/.venv/bin/activate"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
unset ATLAS_ENV_FILE

for mode in full minimal; do
    args=(--target "$WORK_DIR/$mode" --force)
    if [ "$mode" = minimal ]; then
        args+=(--minimal)
    fi
    python -m atlas.init_cli "${args[@]}"
done

python - "$WORK_DIR" <<'PY'
import sys
from pathlib import Path

from dotenv import dotenv_values

root = Path(sys.argv[1])
secrets = []
for mode in ("full", "minimal"):
    values = dotenv_values(root / mode / ".env")
    assert values.get("DEBUG_MODE", "false").lower() != "true"
    secret = values["CAPABILITY_TOKEN_SECRET"]
    assert len(secret) >= 32
    assert secret != values["MCP_TOKEN_ENCRYPTION_KEY"]
    secrets.append(secret)
assert secrets[0] != secrets[1]
print("PASSED: full and minimal initialization generate independent safe defaults")
PY

export MCP_TOKEN_ENCRYPTION_KEY
MCP_TOKEN_ENCRYPTION_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export CAPABILITY_TOKEN_SECRET
CAPABILITY_TOKEN_SECRET="$(python -c 'import secrets; print(secrets.token_hex(32))')"
export APP_LOG_DIR="$WORK_DIR/logs"
export APP_CONFIG_DIR="$WORK_DIR/full/config"
export CHAT_HISTORY_DB_URL="duckdb:///$WORK_DIR/chat_history.db"
export MCP_TOKEN_STORAGE_DIR="$WORK_DIR/tokens"
export FEATURE_AGENT_PORTAL_ENABLED=false
export SKIP_AUTHORIZATION_CHECKS=false
export ALLOW_DEBUG_NON_LOOPBACK=false
export DEBUG_MODE=false
export FEATURE_PROXY_SECRET_ENABLED=false
export USE_MOCK_S3=true

expect_rejected() {
    local name="$1" expected="$2"
    shift 2
    local status=0
    (cd "$WORK_DIR" && timeout 30 "$@") > "$WORK_DIR/startup.log" 2>&1 || status=$?
    if [ "$status" -eq 0 ] || [ "$status" -eq 124 ] ||
       ! grep -q "$expected" "$WORK_DIR/startup.log"; then
        cat "$WORK_DIR/startup.log"
        echo "FAILED: $name"
        exit 1
    fi
    echo "PASSED: $name"
}

expect_rejected "production debug startup" "DEBUG_MODE" \
    env DEBUG_MODE=true ENVIRONMENT=production ATLAS_HOST=127.0.0.1 \
    python -m atlas.server_cli --host 127.0.0.1
expect_rejected "CLI wildcard bind overrides safe environment bind" "DEBUG_MODE" \
    env DEBUG_MODE=true ENVIRONMENT=development ATLAS_HOST=127.0.0.1 \
    python -m atlas.server_cli --host 0.0.0.0
expect_rejected "public capability placeholder" "CAPABILITY_TOKEN_SECRET" \
    env DEBUG_MODE=false CAPABILITY_TOKEN_SECRET=replace-with-openssl-rand-hex-32 \
    python -m atlas.server_cli --host 127.0.0.1
expect_rejected "short capability secret" "CAPABILITY_TOKEN_SECRET" \
    env DEBUG_MODE=false CAPABILITY_TOKEN_SECRET=short \
    python -m atlas.server_cli --host 127.0.0.1

bash "$PROJECT_ROOT/test/run_tests.sh" backend
