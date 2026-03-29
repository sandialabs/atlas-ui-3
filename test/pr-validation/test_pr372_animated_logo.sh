#!/bin/bash
# Test script for PR #372 - Animated logo on welcome screen
#
# Covers:
# - Frontend builds successfully with VITE_FEATURE_ANIMATED_LOGO=true
# - Frontend builds successfully with VITE_FEATURE_ANIMATED_LOGO=false
# - AnimatedLogo component exists and is imported conditionally
# - Feature flag is wired in WelcomeScreen.jsx
# - Dockerfile includes the build arg
# - .env.example includes the flag
# - test_docker_env_sync exclusion list includes the flag
# - Frontend lint passes
# - Backend tests pass

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'
BOLD='\033[1m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo -e "${BOLD}==========================================${NC}"
    echo -e "${BOLD}$1${NC}"
    echo -e "${BOLD}==========================================${NC}"
}

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

# ==========================================
# CHECK 1: AnimatedLogo component exists
# ==========================================
print_header "Check 1: AnimatedLogo component exists"
if [ -f "$PROJECT_ROOT/frontend/src/components/AnimatedLogo.jsx" ]; then
    print_result 0 "AnimatedLogo.jsx exists"
else
    print_result 1 "AnimatedLogo.jsx not found"
fi

# ==========================================
# CHECK 2: WelcomeScreen conditionally imports AnimatedLogo
# ==========================================
print_header "Check 2: WelcomeScreen uses feature flag"
if grep -q "VITE_FEATURE_ANIMATED_LOGO" "$PROJECT_ROOT/frontend/src/components/WelcomeScreen.jsx"; then
    print_result 0 "WelcomeScreen.jsx references VITE_FEATURE_ANIMATED_LOGO"
else
    print_result 1 "WelcomeScreen.jsx missing VITE_FEATURE_ANIMATED_LOGO reference"
fi

# ==========================================
# CHECK 3: .env.example has the flag
# ==========================================
print_header "Check 3: .env.example includes flag"
if grep -q "VITE_FEATURE_ANIMATED_LOGO" "$PROJECT_ROOT/.env.example"; then
    print_result 0 ".env.example includes VITE_FEATURE_ANIMATED_LOGO"
else
    print_result 1 ".env.example missing VITE_FEATURE_ANIMATED_LOGO"
fi

# ==========================================
# CHECK 4: Dockerfile has the build arg
# ==========================================
print_header "Check 4: Dockerfile includes build arg"
if grep -q "ARG VITE_FEATURE_ANIMATED_LOGO" "$PROJECT_ROOT/Dockerfile"; then
    print_result 0 "Dockerfile has VITE_FEATURE_ANIMATED_LOGO ARG"
else
    print_result 1 "Dockerfile missing VITE_FEATURE_ANIMATED_LOGO ARG"
fi

# ==========================================
# CHECK 5: Docker env sync exclusion list
# ==========================================
print_header "Check 5: test_docker_env_sync.py exclusion list"
if grep -q "VITE_FEATURE_ANIMATED_LOGO" "$PROJECT_ROOT/atlas/tests/test_docker_env_sync.py"; then
    print_result 0 "test_docker_env_sync.py has VITE_FEATURE_ANIMATED_LOGO in exclusions"
else
    print_result 1 "test_docker_env_sync.py missing VITE_FEATURE_ANIMATED_LOGO exclusion"
fi

# ==========================================
# CHECK 6: vite.config.js logs the flag
# ==========================================
print_header "Check 6: vite.config.js logs the flag"
if grep -q "VITE_FEATURE_ANIMATED_LOGO" "$PROJECT_ROOT/frontend/vite.config.js"; then
    print_result 0 "vite.config.js logs VITE_FEATURE_ANIMATED_LOGO"
else
    print_result 1 "vite.config.js missing VITE_FEATURE_ANIMATED_LOGO"
fi

# ==========================================
# CHECK 7: Frontend build with flag enabled
# ==========================================
print_header "Check 7: Frontend build (animated logo ON)"
cd "$PROJECT_ROOT/frontend"
npm install --silent 2>/dev/null
VITE_FEATURE_ANIMATED_LOGO=true npm run build > /dev/null 2>&1
print_result $? "npm run build with VITE_FEATURE_ANIMATED_LOGO=true"

# ==========================================
# CHECK 8: Frontend build with flag disabled
# ==========================================
print_header "Check 8: Frontend build (animated logo OFF)"
VITE_FEATURE_ANIMATED_LOGO=false npm run build > /dev/null 2>&1
print_result $? "npm run build with VITE_FEATURE_ANIMATED_LOGO=false"

# ==========================================
# CHECK 9: Frontend lint
# ==========================================
print_header "Check 9: Frontend lint"
cd "$PROJECT_ROOT/frontend"
npm run lint > /dev/null 2>&1
print_result $? "npm run lint passes"

# ==========================================
# CHECK 10: Backend tests
# ==========================================
print_header "Check 10: Backend tests"
cd "$PROJECT_ROOT"
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend tests pass"

# ==========================================
# SUMMARY
# ==========================================
echo ""
echo -e "${BOLD}==========================================${NC}"
echo -e "${BOLD}SUMMARY${NC}"
echo -e "${BOLD}==========================================${NC}"
echo -e "${GREEN}PASSED${NC}: $PASSED"
echo -e "${RED}FAILED${NC}: $FAILED"
echo ""

if [ "$FAILED" -gt 0 ]; then
    echo -e "${RED}Some checks failed.${NC}"
    exit 1
else
    echo -e "${GREEN}All checks passed.${NC}"
    exit 0
fi
