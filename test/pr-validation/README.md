# PR Validation Scripts

Last updated: 2026-01-29

## Purpose

This folder contains test scripts that validate the test plan for each pull request that changes backend or feature code. Every PR with backend changes **must** have a corresponding validation script here before the PR is committed, reviewed, or merged.

## Naming Convention

Scripts follow the pattern:

```
test_pr{NUMBER}_{short_description}.sh
```

- `{NUMBER}` - The GitHub PR number
- `{short_description}` - A brief snake_case summary of what the PR does

Examples:
- `test_pr271_cli_rag_features.sh` - Validates CLI RAG data source and env-file features
- `test_pr280_agent_loop_refactor.sh` - Validates agent loop refactoring
- `test_pr295_mcp_auth_filtering.sh` - Validates MCP auth filtering changes

## Script Structure

Each script should:

1. **Be self-contained** - Run from the project root with no manual setup
2. **Activate the virtual environment** - Source `.venv/bin/activate`
3. **Test the PR's test plan items** - Cover every item from the PR description's "Test plan" section
4. **Report pass/fail clearly** - Print PASSED/FAILED for each check with a summary at the end
5. **Exit with code 0 on success, non-zero on failure**
6. **Run the backend unit tests** as a final step (`./test/run_tests.sh backend`)

### Template

```bash
#!/bin/bash
# Test script for PR #XXX: <Title>
# Covers test plan items from the PR description.

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

# --- Add test plan checks here ---

# Final: run backend unit tests
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

# Summary
echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
```

## Running

```bash
# Run all PR validation scripts
bash test/run_pr_validation.sh

# Run a single PR by number
bash test/run_pr_validation.sh 271

# Run multiple PRs by number
bash test/run_pr_validation.sh 271 295

# List available scripts
bash test/run_pr_validation.sh --list
```
