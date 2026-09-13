#!/usr/bin/env bash
# Test script for PR #933: chat transcript exports open in a new browser tab (issue #908)
#
# What this validates:
# - The transcript helper opens the export blob in a new browser tab and falls
#   back to the old file download when the popup is blocked (or window.open
#   throws), revoking the blob URL only after the tab has had time to load.
# - The header offers "Open as JSON" / "Open as Text" (no download labels) in
#   both the desktop dropdown and the mobile menu, routes the items to the
#   context openers, marks them with the new-tab hint, and exposes menu ARIA
#   semantics.
# - A production build carries the new labels and no longer carries the old
#   "Download as JSON/Text" transcript labels.
#
# The behavioral checks run the component/helper vitest suites (real renders
# and clicks in jsdom); the screenshots referenced in the PR description are
# checked in under test/pr-validation/assets/pr933-screenshots/.

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

cd "$PROJECT_ROOT/frontend"

# ==========================================
print_header "Check 1: the old transcript download labels are gone from the source"
# ==========================================
if ! grep -rq "Download as JSON\|Download as Text" src/components/ src/contexts/ 2>/dev/null; then
    print_result 0 "Header no longer offers 'Download as JSON' / 'Download as Text'"
else
    print_result 1 "Header no longer offers 'Download as JSON' / 'Download as Text'"
fi

# ==========================================
print_header "Check 2: open-in-new-tab helper contract (tab, fallback, delayed revoke)"
# ==========================================
timeout 300 npx vitest run src/test/chatExport.test.js --reporter=dot > /tmp/pr933_chatexport.log 2>&1
print_result $? "openBlobInNewTab vitest suite passes ($(grep -E '^ *Tests ' /tmp/pr933_chatexport.log | grep -oE '[0-9]+ passed' | head -1))"

# ==========================================
print_header "Check 3: header wiring -- labels, openers, new-tab hint, ARIA menu"
# ==========================================
timeout 300 npx vitest run src/test/header-transcript-open.test.jsx --reporter=dot > /tmp/pr933_header.log 2>&1
print_result $? "header transcript vitest suite passes ($(grep -E '^ *Tests ' /tmp/pr933_header.log | grep -oE '[0-9]+ passed' | head -1))"

# ==========================================
print_header "Check 4: production build ships the open-as labels and drops the old ones"
# ==========================================
npm run build > /tmp/pr933_build.log 2>&1
BUILD=$?
print_result $BUILD "frontend production build succeeds"
if [ $BUILD -eq 0 ]; then
    BUNDLE=$(ls -t dist/assets/index-*.js 2>/dev/null | head -1)
    if [ -n "$BUNDLE" ] \
        && grep -q "Open as JSON" "$BUNDLE" && grep -q "Open as Text" "$BUNDLE" \
        && ! grep -q "Download as JSON\|Download as Text" "$BUNDLE"; then
        print_result 0 "bundle contains 'Open as JSON'/'Open as Text', not the download labels"
    else
        print_result 1 "bundle contains 'Open as JSON'/'Open as Text', not the download labels"
    fi
fi

# ==========================================
print_header "Check 5: frontend test suite"
# ==========================================
timeout 900 npx vitest run --reporter=dot > /tmp/pr933_all_frontend.log 2>&1
print_result $? "full frontend vitest suite passes"

# ==========================================
print_header "Check 6: backend unit tests"
# ==========================================
# This PR is frontend-only; the backend suite is run as the convention's final
# gate. Skip it when no local venv exists (e.g. a frontend-only checkout) so
# the script still validates the PR's own surface.
if [ -x "$PROJECT_ROOT/.venv/bin/activate" ] || [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    (
        cd "$PROJECT_ROOT"
        ./test/run_tests.sh backend > /tmp/pr933_backend.log 2>&1
    )
    print_result $? "backend test suite passes"
else
    echo "  SKIPPED: no .venv in this checkout (frontend-only environment)"
fi

# ==========================================
print_header "Summary"
# ==========================================
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"
if [ "$FAILED" -gt 0 ]; then
    echo "RESULT: FAILED"; exit 1
fi
echo "RESULT: PASSED"; exit 0