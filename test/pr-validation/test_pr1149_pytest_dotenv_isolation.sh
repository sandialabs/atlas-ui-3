#!/bin/bash
# Validation script for issue #1149: import-time dotenv loading must not leak
# a checkout-local .env into the backend test suite, while runtime .env
# handling stays exactly as it was.
#
# Scenarios, all with real interpreters and no stubs:
#   1. Real backend test files run in fresh pytest sessions produce the same
#      result with no .env and with a conflicting .env placed exactly where
#      atlas/main.py's import-time load_dotenv("../.env") and AppSettings look
#      (external authorizer, custom admin group, compliance-required mode,
#      production mode, auth bypass, placeholder secret).
#   2. The same holds with the file order reversed (main imported at
#      collection before vs. after the auth tests).
#   3. Runtime is unchanged: a plain `import main` (no pytest) still loads the
#      .env into os.environ and AppSettings reads it.
#   4. PYTHON_DOTENV_DISABLED=1 (python-dotenv's own switch, which the test
#      harness sets) turns off both of those reads.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"; PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"; FAILED=$((FAILED + 1))
    fi
}
print_header() { echo ""; echo "=============================="; echo "$1"; echo "=============================="; }

cd "$PROJECT_ROOT" || exit 1
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# Fresh shell state: none of the variables the harness or a deployment might
# have exported, so the sessions below start the way a developer's would.
unset PYTHON_DOTENV_DISABLED DEBUG_MODE ADMIN_GROUP AUTH_GROUP_CHECK_URL AUTH_GROUP_CHECK_API_KEY
export PYTHONPATH="$PROJECT_ROOT"

write_conflicting_env() {
    mkdir -p "$(dirname "$1")" || exit 1
    cat > "$1" <<'EOF'
ATLAS_1149_DOTENV_SENTINEL=loaded-from-dotenv
AUTH_GROUP_CHECK_URL=http://127.0.0.1:9/never-contact-me
AUTH_GROUP_CHECK_API_KEY=conflicting-dotenv-key
ADMIN_GROUP=dotenv_admins
AUTH_STATIC_GROUPS=dotenv_group:someone@example.com
FEATURE_COMPLIANCE_LEVELS_ENABLED=true
FEATURE_COMPLIANCE_LEVEL_REQUIRED=true
COMPLIANCE_DEFAULT_LEVEL=Internal
DEBUG_MODE=false
SKIP_AUTHORIZATION_CHECKS=true
CAPABILITY_TOKEN_SECRET=placeholder
EOF
}

TESTS="$PROJECT_ROOT/atlas/tests"
FILES_FWD=(
    "$TESTS/test_core_auth.py"
    "$TESTS/test_security_admin_routes.py"
    "$TESTS/test_compliance_level_required.py"
    "$TESTS/test_env_isolation.py"
    "$TESTS/test_dotenv_isolation.py"
)
FILES_REV=()
for (( i=${#FILES_FWD[@]}-1; i>=0; i-- )); do FILES_REV+=("${FILES_FWD[i]}"); done

# Run pytest with cwd <root>/work so "../.env" resolves to <root>/.env.
run_session() {
    local root="$1"; shift
    mkdir -p "$root/work"
    (cd "$root/work" && python -m pytest -q -p no:cacheprovider "$@" 2>&1 | tail -1)
}

print_header "1. Same results with and without a conflicting .env"
CLEAN="$(run_session "$WORK/clean" "${FILES_FWD[@]}")"
write_conflicting_env "$WORK/dotenv/.env"
if ! grep -q '^ADMIN_GROUP=dotenv_admins$' "$WORK/dotenv/.env" 2>/dev/null; then
    echo "conflicting .env was not written; aborting" >&2
    exit 1
fi
DOTENV="$(run_session "$WORK/dotenv" "${FILES_FWD[@]}")"
echo "  clean : $CLEAN"
echo "  .env  : $DOTENV"
[[ "$CLEAN" == *passed* && "$CLEAN" != *failed* && "$CLEAN" != *error* ]]
print_result $? "clean session passes"
[ "$(echo "$CLEAN" | sed 's/ in .*//')" = "$(echo "$DOTENV" | sed 's/ in .*//')" ]
print_result $? "conflicting .env leaves the result unchanged"

print_header "2. Same results with the import order reversed"
DOTENV_REV="$(run_session "$WORK/dotenv" "${FILES_REV[@]}")"
echo "  .env, reversed: $DOTENV_REV"
[ "$(echo "$CLEAN" | sed 's/ in .*//')" = "$(echo "$DOTENV_REV" | sed 's/ in .*//')" ]
print_result $? "reversed order with a conflicting .env leaves the result unchanged"

# Runtime probe: import the real app the way `python main.py` does, from a
# working directory whose parent holds the .env.
mkdir -p "$WORK/runtime/work" "$WORK/runtime/data" "$WORK/runtime/logs"
cat > "$WORK/runtime/.env" <<'EOF'
ATLAS_1149_DOTENV_SENTINEL=loaded-at-runtime
ADMIN_GROUP=pr1149_runtime_admins
EOF
runtime_probe() {
    (cd "$WORK/runtime/work" && env "$@" \
        APP_LOG_DIR="$WORK/runtime/logs" \
        CHAT_HISTORY_DB_URL="duckdb:///$WORK/runtime/data/chat_history.db" \
        MCP_TOKEN_STORAGE_DIR="$WORK/runtime/data/tokens" \
        MCP_TOKEN_ENCRYPTION_KEY="pr1149-validation-key-not-a-secret" \
        MCP_CONFIG_FILE="mcp-test.json" \
        python -c '
import os, sys
sys.path.insert(0, os.path.join(os.environ["PYTHONPATH"], "atlas"))
import main  # noqa: F401
from atlas.modules.config.config_manager import config_manager
print(os.environ.get("ATLAS_1149_DOTENV_SENTINEL"), config_manager.app_settings.admin_group)
' 2>/dev/null | tail -1)
}

print_header "3. Runtime .env handling is unchanged"
OUT="$(runtime_probe)"
echo "  import main -> $OUT"
[ "$OUT" = "loaded-at-runtime pr1149_runtime_admins" ]
print_result $? "plain app import still loads ../.env into os.environ and AppSettings"

print_header "4. PYTHON_DOTENV_DISABLED turns every .env read off"
OUT="$(runtime_probe PYTHON_DOTENV_DISABLED=1)"
echo "  import main (disabled) -> $OUT"
[ "$OUT" = "None admin" ]
print_result $? "load_dotenv and the AppSettings dotenv source both honor the switch"

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
