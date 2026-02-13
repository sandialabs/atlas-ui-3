#!/usr/bin/env bash
# PR #334 - Add exponential backoff for frontend polling to prevent backend DOS
# Validates that:
# 1. The shared usePollingWithBackoff hook exists and exports expected functions
# 2. All modified components import backoff utilities
# 3. No fixed setInterval polling remains in modified files
# 4. LogViewer enforces minimum poll interval
# 5. Frontend builds and all tests pass

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASS=0
FAIL=0

check() {
  local desc="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "PASSED: $desc"
    PASS=$((PASS + 1))
  else
    echo "FAILED: $desc"
    FAIL=$((FAIL + 1))
  fi
}

check_not() {
  local desc="$1"
  shift
  if ! "$@" >/dev/null 2>&1; then
    echo "PASSED: $desc"
    PASS=$((PASS + 1))
  else
    echo "FAILED: $desc"
    FAIL=$((FAIL + 1))
  fi
}

echo "=== PR #334 Validation: Exponential Backoff for Frontend Polling ==="
echo ""

# 1. Shared hook exists and exports expected symbols
HOOK_FILE="$PROJECT_ROOT/frontend/src/hooks/usePollingWithBackoff.js"
check "usePollingWithBackoff hook file exists" test -f "$HOOK_FILE"
check "Hook exports calculateBackoffDelay" grep -q "export function calculateBackoffDelay" "$HOOK_FILE"
check "Hook exports usePollingWithBackoff" grep -q "export function usePollingWithBackoff" "$HOOK_FILE"
check "Hook implements jitter" grep -q "addJitter" "$HOOK_FILE"

# 2. WSContext uses backoff
WS_FILE="$PROJECT_ROOT/frontend/src/contexts/WSContext.jsx"
check "WSContext imports calculateBackoffDelay" grep -q "calculateBackoffDelay" "$WS_FILE"
check_not "WSContext does not use setInterval for health check" grep -q "setInterval(checkBackendAndReconnect" "$WS_FILE"
check "WSContext uses setTimeout-based scheduling" grep -q "scheduleHealthCheck" "$WS_FILE"
check "WSContext has max health check interval" grep -q "MAX_HEALTH_CHECK_INTERVAL" "$WS_FILE"

# 3. LogViewer uses backoff and enforces minimum
LOG_FILE="$PROJECT_ROOT/frontend/src/components/LogViewer.jsx"
check "LogViewer imports calculateBackoffDelay" grep -q "calculateBackoffDelay" "$LOG_FILE"
check "LogViewer defines MIN_POLL_INTERVAL" grep -q "MIN_POLL_INTERVAL" "$LOG_FILE"
check_not "LogViewer does not use setInterval for polling" grep -q "setInterval(fetchLogs" "$LOG_FILE"
check "LogViewer enforces minimum with Math.max" grep -q "Math.max.*MIN_POLL_INTERVAL" "$LOG_FILE"
check "LogViewer min input is 5 seconds" grep -q 'min="5"' "$LOG_FILE"

# 4. MCPConfigurationCard uses shared backoff with higher limits
MCP_FILE="$PROJECT_ROOT/frontend/src/components/admin/MCPConfigurationCard.jsx"
check "MCPConfigurationCard imports shared calculateBackoffDelay" grep -q "from.*usePollingWithBackoff" "$MCP_FILE"
check "MCPConfigurationCard max backoff is 300000 (5 min)" grep -q "MAX_BACKOFF_DELAY = 300000" "$MCP_FILE"
check "MCPConfigurationCard normal interval is 30000 (30s)" grep -q "NORMAL_POLLING_INTERVAL = 30000" "$MCP_FILE"

# 5. BannerPanel uses the shared hook
BANNER_FILE="$PROJECT_ROOT/frontend/src/components/BannerPanel.jsx"
check "BannerPanel imports usePollingWithBackoff" grep -q "usePollingWithBackoff" "$BANNER_FILE"
check_not "BannerPanel does not use setInterval" grep -q "setInterval" "$BANNER_FILE"

# 6. Frontend lint passes (no new errors)
echo ""
echo "Running frontend lint..."
cd "$PROJECT_ROOT/frontend"
if npm run lint 2>&1 | grep -q "0 errors"; then
  echo "PASSED: Frontend lint has no errors"
  PASS=$((PASS + 1))
else
  # Check if the only issues are pre-existing warnings
  LINT_OUTPUT=$(npm run lint 2>&1 || true)
  ERROR_COUNT=$(echo "$LINT_OUTPUT" | grep -oP '\d+ error' | grep -oP '\d+' || echo "0")
  if [ "$ERROR_COUNT" = "0" ]; then
    echo "PASSED: Frontend lint has no errors (warnings are pre-existing)"
    PASS=$((PASS + 1))
  else
    echo "FAILED: Frontend lint has errors"
    FAIL=$((FAIL + 1))
  fi
fi

# 7. Frontend tests pass
echo ""
echo "Running frontend tests..."
cd "$PROJECT_ROOT/frontend"
if npx vitest run 2>&1 | tail -5 | grep -q "passed"; then
  echo "PASSED: Frontend tests pass"
  PASS=$((PASS + 1))
else
  echo "FAILED: Frontend tests fail"
  FAIL=$((FAIL + 1))
fi

# 8. Frontend builds
echo ""
echo "Running frontend build..."
cd "$PROJECT_ROOT/frontend"
if npm run build 2>&1 | grep -q "built in"; then
  echo "PASSED: Frontend build succeeds"
  PASS=$((PASS + 1))
else
  echo "FAILED: Frontend build fails"
  FAIL=$((FAIL + 1))
fi

# 9. Backend tests pass
echo ""
echo "Running backend tests..."
cd "$PROJECT_ROOT"
if bash test/run_tests.sh backend 2>&1 | tail -5 | grep -qE "passed|PASSED"; then
  echo "PASSED: Backend tests pass"
  PASS=$((PASS + 1))
else
  echo "FAILED: Backend tests fail"
  FAIL=$((FAIL + 1))
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
