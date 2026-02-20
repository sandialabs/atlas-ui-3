#!/bin/bash
# Test script for PR #347: Enable chat history with DuckDB by default
# Validates that .env.example ships with chat history enabled and DuckDB configured.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT"

echo "=== PR #347: Enable Chat History by Default ==="
echo ""

# --- 1. .env.example has chat history enabled ---
echo "--- .env.example defaults ---"
grep -q "^FEATURE_CHAT_HISTORY_ENABLED=true" .env.example
print_result $? "FEATURE_CHAT_HISTORY_ENABLED defaults to true"

grep -q "^CHAT_HISTORY_DB_URL=duckdb:///data/chat_history.db" .env.example
print_result $? "CHAT_HISTORY_DB_URL uncommented with DuckDB path"

# PostgreSQL option should still be present but commented out
grep -q "^# CHAT_HISTORY_DB_URL=postgresql://" .env.example
print_result $? "PostgreSQL option remains documented and commented out"

# --- 2. Documentation updated ---
echo ""
echo "--- Documentation ---"
grep -q "enabled by default" docs/admin/chat-history.md
print_result $? "docs/admin/chat-history.md reflects enabled-by-default"

# --- 3. CHANGELOG entry exists ---
grep -q "PR #347" CHANGELOG.md
print_result $? "CHANGELOG.md has PR #347 entry"

# --- 4. AI instruction files updated ---
grep -q "Chat history default" CLAUDE.md
print_result $? "CLAUDE.md updated with chat history default note"

grep -q "Chat history default" GEMINI.md
print_result $? "GEMINI.md updated with chat history default note"

grep -q "Chat history default" .github/copilot-instructions.md
print_result $? "copilot-instructions.md updated with chat history default note"

# --- 5. End-to-end: start backend with .env.example defaults and verify feature flag ---
echo ""
echo "--- End-to-end: feature flag check ---"
# Create a temporary .env from .env.example for testing
TEST_ENV="$PROJECT_ROOT/test/pr-validation/fixtures/pr347/.env"
mkdir -p "$(dirname "$TEST_ENV")"
cp .env.example "$TEST_ENV"

# Verify the feature flag is picked up by Python config
python -c "
import os, sys
from dotenv import load_dotenv
load_dotenv('$TEST_ENV', override=True)
val = os.getenv('FEATURE_CHAT_HISTORY_ENABLED', 'false')
assert val.lower() == 'true', f'Expected true, got {val}'
db_url = os.getenv('CHAT_HISTORY_DB_URL', '')
assert 'duckdb' in db_url, f'Expected duckdb URL, got {db_url}'
print('Feature flag and DB URL loaded correctly from .env')
" 2>&1
print_result $? "Python loads feature flag and DuckDB URL from .env.example"

# Cleanup
rm -rf "$PROJECT_ROOT/test/pr-validation/fixtures/pr347"

# --- 6. Run backend unit tests ---
echo ""
echo "--- Backend unit tests ---"
"$PROJECT_ROOT/test/run_tests.sh" backend
print_result $? "Backend tests pass"

# --- Summary ---
echo ""
echo "========================="
echo "PASSED: $PASSED"
echo "FAILED: $FAILED"
echo "========================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
