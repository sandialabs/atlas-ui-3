#!/usr/bin/env bash
# Test script for PR #997 (issue #994): weekly automated releases
#
# What this validates end to end:
# - The weekly workflow is scheduled, drives the bump through
#   scripts/release_bump.py, merges its own PR, and publishes a Release.
# - The monthly stabilization cron is gone (release-cut is manual-only).
# - `release_bump.py check` distinguishes a populated `[Unreleased]` from
#   an empty one, so a quiet week is a real no-op.
# - `release_bump.py apply` bumps both version sources and reshapes the
#   changelog without dropping released headings.
# - `release_bump.py notes` extracts exactly the released section.
# - `release_bump.py apply` refuses to cut an empty release.

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

echo "=== PR #997 Validation: weekly automated releases ==="

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"
cd "$PROJECT_ROOT"

SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

# ==========================================
print_header "Check 1: weekly workflow is scheduled and fully automated"
# ==========================================
WORKFLOW="$PROJECT_ROOT/.github/workflows/release-weekly.yml"
if [ -f "$WORKFLOW" ] \
    && grep -qE "cron: '0 14 \* \* 1'" "$WORKFLOW" \
    && grep -q "scripts/release_bump.py" "$WORKFLOW" \
    && grep -q "gh pr merge" "$WORKFLOW" \
    && grep -q "gh release create" "$WORKFLOW"; then
    print_result 0 "release-weekly.yml is weekly, bumps via release_bump.py, merges, and releases"
else
    print_result 1 "release-weekly.yml is weekly, bumps via release_bump.py, merges, and releases"
fi

# ==========================================
print_header "Check 2: monthly stabilization cron is removed"
# ==========================================
CUT="$PROJECT_ROOT/.github/workflows/release-cut.yml"
if [ -f "$CUT" ] && ! grep -qE '^\s*- cron:' "$CUT" && grep -q "workflow_dispatch:" "$CUT"; then
    print_result 0 "release-cut.yml is manual-only (no schedule)"
else
    print_result 1 "release-cut.yml is manual-only (no schedule)"
fi

# ==========================================
print_header "Check 2b: release workflow hardening"
# ==========================================
if grep -q 'INPUT_VERSION:' "$WORKFLOW" \
    && grep -q 'INPUT_BUMP:' "$WORKFLOW" \
    && grep -q 'INPUT_VERSION:-\|INPUT_BUMP:-' "$WORKFLOW" \
    && ! grep -qE 'input_version="\$\{\{ github\.event\.inputs' "$WORKFLOW"; then
    print_result 0 "dispatch inputs are passed through env, not interpolated into shell"
else
    print_result 1 "dispatch inputs are passed through env, not interpolated into shell"
fi
if grep -q "Require RELEASE_PAT for a real release" "$WORKFLOW" \
    && grep -q "RELEASE_PAT is required to publish" "$WORKFLOW"; then
    print_result 0 "a real release without RELEASE_PAT fails before branching"
else
    print_result 1 "a real release without RELEASE_PAT fails before branching"
fi
if grep -q -- "--match-head-commit" "$WORKFLOW" \
    && grep -q "mergeCommit" "$WORKFLOW" \
    && grep -q 'git tag -a "v${NEW_VERSION}" -m "Atlas UI 3 v${NEW_VERSION}" "$MERGE_SHA"' "$WORKFLOW"; then
    print_result 0 "merge is pinned to the checked head and the merge commit is tagged"
else
    print_result 1 "merge is pinned to the checked head and the merge commit is tagged"
fi
if grep -q "Verify the current version has a release tag" "$WORKFLOW"; then
    print_result 0 "an untagged version already on main is detected"
else
    print_result 1 "an untagged version already on main is detected"
fi
if grep -q "REQUIRED_CHECKS" "$WORKFLOW" \
    && grep -q "persist-credentials: false" "$WORKFLOW" \
    && grep -q "Alert on failure" "$WORKFLOW"; then
    print_result 0 "merge waits on a fixed check set, PAT is not persisted, and failures alert"
else
    print_result 1 "merge waits on a fixed check set, PAT is not persisted, and failures alert"
fi

# ==========================================
print_header "Check 3: change detection distinguishes empty vs populated"
# ==========================================
python3 scripts/release_bump.py check > /dev/null 2>&1
print_result $? "populated [Unreleased] reports changes (exit 0)"

mkdir -p "$SANDBOX/empty/atlas"
cp scripts/release_bump.py "$SANDBOX/empty/"
cat > "$SANDBOX/empty/CHANGELOG.md" <<'EOF'
# Changelog

## [Unreleased]

## [0.6.0] - 2026-09-22

### PR #900 - 2026-09-01
- Older change.
EOF
( cd "$SANDBOX/empty" && python3 release_bump.py check > /dev/null 2>&1 )
if [ $? -ne 0 ]; then
    print_result 0 "empty [Unreleased] is detected as a no-op"
else
    print_result 1 "empty [Unreleased] is detected as a no-op"
fi

# ==========================================
print_header "Check 4: version planning follows SemVer"
# ==========================================
PLANNED_MINOR="$(python3 scripts/release_bump.py plan --bump minor)"
PLANNED_PATCH="$(python3 scripts/release_bump.py plan --bump patch)"
PLANNED_MAJOR="$(python3 scripts/release_bump.py plan --bump major)"
CURRENT="$(python3 scripts/release_bump.py current)"
IFS='.' read -r MA MI PA <<< "$CURRENT"
if [ "$PLANNED_MINOR" = "$MA.$((MI + 1)).0" ] \
    && [ "$PLANNED_PATCH" = "$MA.$MI.$((PA + 1))" ] \
    && [ "$PLANNED_MAJOR" = "$((MA + 1)).0.0" ]; then
    print_result 0 "minor/patch/major plans derive from $CURRENT"
else
    print_result 1 "minor/patch/major plans derive from $CURRENT"
fi

# ==========================================
print_header "Check 5: apply bumps both sources and reshapes CHANGELOG"
# ==========================================
mkdir -p "$SANDBOX/apply/atlas"
cp scripts/release_bump.py "$SANDBOX/apply/"
cp atlas/version.py "$SANDBOX/apply/atlas/version.py"
cp pyproject.toml "$SANDBOX/apply/pyproject.toml"
cp CHANGELOG.md "$SANDBOX/apply/CHANGELOG.md"

( cd "$SANDBOX/apply" && python3 release_bump.py apply --version 0.7.0 --date 2026-10-06 > /dev/null 2>&1 )
APPLY_RC=$?
if [ "$APPLY_RC" -eq 0 ] \
    && grep -q 'VERSION = "0.7.0"' "$SANDBOX/apply/atlas/version.py" \
    && grep -qm1 '^version = "0.7.0"' "$SANDBOX/apply/pyproject.toml"; then
    print_result 0 "apply bumps atlas/version.py and pyproject.toml to 0.7.0"
else
    print_result 1 "apply bumps atlas/version.py and pyproject.toml to 0.7.0"
fi

if grep -q '^## \[0.7.0\] - 2026-10-06$' "$SANDBOX/apply/CHANGELOG.md" \
    && grep -q '^## \[Unreleased\]$' "$SANDBOX/apply/CHANGELOG.md" \
    && python3 scripts/check_changelog_release_headings.py \
        CHANGELOG.md "$SANDBOX/apply/CHANGELOG.md" > /dev/null 2>&1; then
    print_result 0 "apply reshapes CHANGELOG and keeps every released heading"
else
    print_result 1 "apply reshapes CHANGELOG and keeps every released heading"
fi

# ==========================================
print_header "Check 6: release notes contain only the released section"
# ==========================================
( cd "$SANDBOX/apply" && python3 release_bump.py notes --version 0.7.0 > "$SANDBOX/notes.md" 2>/dev/null )
NOTES_RC=$?
if [ "$NOTES_RC" -eq 0 ] \
    && head -1 "$SANDBOX/notes.md" | grep -q '^## \[0.7.0\] - 2026-10-06$' \
    && grep -q 'release-weekly.yml' "$SANDBOX/notes.md" \
    && ! grep -q '^## \[0.6.0\]' "$SANDBOX/notes.md"; then
    print_result 0 "notes --version 0.7.0 returns only the 0.7.0 section"
else
    print_result 1 "notes --version 0.7.0 returns only the 0.7.0 section"
fi

# ==========================================
print_header "Check 7: apply refuses to cut an empty release"
# ==========================================
cp scripts/release_bump.py "$SANDBOX/empty/"
cp atlas/version.py "$SANDBOX/empty/atlas/version.py"
cp pyproject.toml "$SANDBOX/empty/pyproject.toml"
( cd "$SANDBOX/empty" && python3 release_bump.py apply --version 0.7.0 --date 2026-10-06 > /dev/null 2>&1 )
if [ $? -ne 0 ]; then
    print_result 0 "apply exits non-zero when [Unreleased] is empty"
else
    print_result 1 "apply exits non-zero when [Unreleased] is empty"
fi

# ==========================================
print_header "Final: backend unit tests"
# ==========================================
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
