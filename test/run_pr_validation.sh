#!/bin/bash
# PR Validation Test Runner
# Runs one or all PR validation scripts from test/pr-validation/.
#
# Usage:
#   bash test/run_pr_validation.sh             # Run ALL scripts
#   bash test/run_pr_validation.sh 271          # Run only PR #271
#   bash test/run_pr_validation.sh 271 295      # Run PR #271 and #295
#   bash test/run_pr_validation.sh --list       # List available scripts

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/pr-validation" && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'

TOTAL_PASSED=0
TOTAL_FAILED=0
TOTAL_SCRIPTS=0

run_script() {
    local script="$1"
    local name
    name="$(basename "$script")"
    TOTAL_SCRIPTS=$((TOTAL_SCRIPTS + 1))

    echo ""
    echo -e "${BOLD}=========================================="
    echo "  Running: $name"
    echo -e "==========================================${NC}"

    bash "$script"
    local exit_code=$?

    if [ $exit_code -eq 0 ]; then
        echo -e "${GREEN}SCRIPT PASSED${NC}: $name"
        TOTAL_PASSED=$((TOTAL_PASSED + 1))
    else
        echo -e "${RED}SCRIPT FAILED${NC}: $name (exit code $exit_code)"
        TOTAL_FAILED=$((TOTAL_FAILED + 1))
    fi

    return $exit_code
}

# --list: show available scripts and exit
if [ "$1" = "--list" ] || [ "$1" = "-l" ]; then
    echo "Available PR validation scripts:"
    for script in "$SCRIPT_DIR"/test_pr*.sh; do
        [ -f "$script" ] || continue
        name="$(basename "$script")"
        pr_num="$(echo "$name" | grep -oP 'pr\K[0-9]+')"
        desc="$(head -2 "$script" | grep -oP '(?<=# Test script for PR #)[0-9]+: .+' || echo "")"
        if [ -n "$desc" ]; then
            echo "  PR #$pr_num  $desc"
        else
            echo "  PR #$pr_num  $name"
        fi
    done
    exit 0
fi

# Collect scripts to run
scripts_to_run=()

if [ $# -eq 0 ]; then
    # No args: run all scripts
    for script in "$SCRIPT_DIR"/test_pr*.sh; do
        [ -f "$script" ] && scripts_to_run+=("$script")
    done
    if [ ${#scripts_to_run[@]} -eq 0 ]; then
        echo "No PR validation scripts found in $SCRIPT_DIR"
        exit 0
    fi
    echo -e "${BOLD}Running all ${#scripts_to_run[@]} PR validation script(s)${NC}"
else
    # Args are PR numbers: find matching scripts
    for pr_num in "$@"; do
        matched=0
        for script in "$SCRIPT_DIR"/test_pr${pr_num}_*.sh; do
            if [ -f "$script" ]; then
                scripts_to_run+=("$script")
                matched=1
            fi
        done
        if [ $matched -eq 0 ]; then
            echo -e "${RED}No validation script found for PR #${pr_num}${NC}"
            echo "  Expected pattern: test_pr${pr_num}_*.sh in $SCRIPT_DIR"
            TOTAL_FAILED=$((TOTAL_FAILED + 1))
            TOTAL_SCRIPTS=$((TOTAL_SCRIPTS + 1))
        fi
    done
fi

# Run collected scripts
for script in "${scripts_to_run[@]}"; do
    run_script "$script"
done

# Summary
echo ""
echo -e "${BOLD}=========================================="
echo "  PR Validation Summary"
echo -e "==========================================${NC}"
echo -e "  Scripts run:    $TOTAL_SCRIPTS"
echo -e "  Passed:         ${GREEN}$TOTAL_PASSED${NC}"
echo -e "  Failed:         ${RED}$TOTAL_FAILED${NC}"
echo ""

if [ $TOTAL_FAILED -eq 0 ]; then
    echo -e "${GREEN}All PR validation scripts passed.${NC}"
    exit 0
else
    echo -e "${RED}$TOTAL_FAILED script(s) failed.${NC}"
    exit 1
fi
