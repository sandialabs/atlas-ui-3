#!/usr/bin/env bash
# Test script for PR #1002: changelog fragments to avoid CHANGELOG.md conflicts
#
# What this validates end to end:
# - `changes/` documents the `<id>.<type>.md` convention and all five types.
# - `scripts/changelog_fragments.py validate` accepts good fragments and
#   rejects a bad name/type or an empty body.
# - `check` reports whether fragments are present; `collect` composes them
#   into `## [Unreleased]` grouped by type and deletes the consumed files.
# - `collect` output survives `release_bump.py apply` and appears in the
#   generated release notes.
# - `guard` fails a normal PR that edits CHANGELOG.md and exempts release
#   branches.
# - The release workflows compose fragments before bumping and build-artifacts
#   validates/guards them in CI.

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

echo "=== PR #1002 Validation: changelog fragments ==="

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"
cd "$PROJECT_ROOT"

SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

FRAGMENTS="$PROJECT_ROOT/scripts/changelog_fragments.py"
RELEASE_BUMP="$PROJECT_ROOT/scripts/release_bump.py"

# ==========================================
print_header "Check 1: changes/ documents the naming convention"
# ==========================================
README_FILE="$PROJECT_ROOT/changes/README.md"
TYPES_OK=0
for t in breaking feature fix security internal; do
    grep -q "\`$t\`" "$README_FILE" 2>/dev/null || TYPES_OK=1
done
if [ -f "$README_FILE" ] \
    && grep -q '<id>.<type>.md' "$README_FILE" \
    && [ "$TYPES_OK" -eq 0 ]; then
    print_result 0 "changes/README.md documents <id>.<type>.md and all five types"
else
    print_result 1 "changes/README.md documents <id>.<type>.md and all five types"
fi

# ==========================================
print_header "Check 2: repository fragments validate"
# ==========================================
python3 "$FRAGMENTS" validate > "$SANDBOX/validate.out" 2>&1
if [ $? -eq 0 ] && grep -q "Validated" "$SANDBOX/validate.out"; then
    print_result 0 "python3 scripts/changelog_fragments.py validate (repo tree)"
else
    print_result 1 "python3 scripts/changelog_fragments.py validate (repo tree)"
    cat "$SANDBOX/validate.out"
fi

# ==========================================
print_header "Check 3: malformed fragment is rejected"
# ==========================================
BAD="$SANDBOX/bad"
mkdir -p "$BAD/changes"
printf 'Not a real type.\n' > "$BAD/changes/12.chore.md"
( cd "$BAD" && python3 "$FRAGMENTS" validate ) > "$SANDBOX/bad.out" 2>&1
if [ $? -ne 0 ]; then
    print_result 0 "invalid type is rejected by validate"
else
    print_result 1 "invalid type is rejected by validate"
fi
printf '   \n<!-- only a comment -->\n' > "$BAD/changes/12.feature.md"
rm -f "$BAD/changes/12.chore.md"
( cd "$BAD" && python3 "$FRAGMENTS" validate ) > "$SANDBOX/empty.out" 2>&1
if [ $? -ne 0 ]; then
    print_result 0 "empty fragment body is rejected by validate"
else
    print_result 1 "empty fragment body is rejected by validate"
fi

# ==========================================
print_header "Check 4: collect composes fragments, grouped by type"
# ==========================================
FLOW="$SANDBOX/flow"
mkdir -p "$FLOW/changes" "$FLOW/atlas"
cp "$FRAGMENTS" "$RELEASE_BUMP" "$FLOW/"
printf 'VERSION = "0.6.0"\n' > "$FLOW/atlas/version.py"
printf '[project]\nversion = "0.6.0"\n' > "$FLOW/pyproject.toml"
cat > "$FLOW/CHANGELOG.md" <<'EOF'
# Changelog

## [Unreleased]

### PR #1001 - 2026-10-02
- Existing direct entry.

## [0.6.0] - 2026-09-22

- Older change.
EOF
printf 'Added grouped changelog fragments.\n' > "$FLOW/changes/1002.feature.md"
printf 'Fixed the guard.\n' > "$FLOW/changes/1047.fix.md"
printf 'Removed the old workflow.\n' > "$FLOW/changes/1030.breaking.md"

( cd "$FLOW" && python3 changelog_fragments.py check ) >/dev/null 2>&1
CHECK_BEFORE=$?
( cd "$FLOW" && python3 changelog_fragments.py collect ) >/dev/null 2>&1
COLLECT_RC=$?

FLOW_CHANGELOG="$FLOW/CHANGELOG.md"
if [ "$CHECK_BEFORE" -eq 0 ] && [ "$COLLECT_RC" -eq 0 ] \
    && grep -q '^### Breaking Changes$' "$FLOW_CHANGELOG" \
    && grep -q '^### Features$' "$FLOW_CHANGELOG" \
    && grep -q '^### Fixes$' "$FLOW_CHANGELOG" \
    && grep -q -- '- \*\*#1002:\*\* Added grouped changelog fragments.' "$FLOW_CHANGELOG" \
    && grep -q -- '- \*\*#1047:\*\* Fixed the guard.' "$FLOW_CHANGELOG" \
    && grep -q '### PR #1001 - 2026-10-02' "$FLOW_CHANGELOG" \
    && grep -q '## \[0.6.0\] - 2026-09-22' "$FLOW_CHANGELOG" \
    && [ ! -e "$FLOW/changes/1002.feature.md" ] \
    && [ ! -e "$FLOW/changes/1047.fix.md" ]; then
    print_result 0 "collect groups by type, preserves existing entries, deletes fragments"
else
    print_result 1 "collect groups by type, preserves existing entries, deletes fragments"
    cat "$FLOW_CHANGELOG"
fi

( cd "$FLOW" && python3 changelog_fragments.py check ) >/dev/null 2>&1
if [ $? -eq 1 ]; then
    print_result 0 "check reports no fragments after collect"
else
    print_result 1 "check reports no fragments after collect"
fi

# ==========================================
print_header "Check 5: composed fragments flow into the release notes"
# ==========================================
( cd "$FLOW" && python3 release_bump.py apply --version 0.7.0 --date 2026-10-06 ) >/dev/null 2>&1
APPLY_RC=$?
( cd "$FLOW" && python3 release_bump.py notes --version 0.7.0 > "$SANDBOX/notes.md" ) >/dev/null 2>&1
NOTES_RC=$?
if [ "$APPLY_RC" -eq 0 ] && [ "$NOTES_RC" -eq 0 ] \
    && grep -q '^## \[0.7.0\] - 2026-10-06$' "$SANDBOX/notes.md" \
    && grep -q '^### Features$' "$SANDBOX/notes.md" \
    && grep -q -- '- \*\*#1002:\*\* Added grouped changelog fragments.' "$SANDBOX/notes.md" \
    && ! grep -q '^## \[0.6.0\]' "$SANDBOX/notes.md"; then
    print_result 0 "apply reshapes the composed section; notes extract it exactly"
else
    print_result 1 "apply reshapes the composed section; notes extract it exactly"
    cat "$SANDBOX/notes.md" 2>/dev/null
fi

# ==========================================
print_header "Check 6: guard blocks direct CHANGELOG edits"
# ==========================================
printf 'CHANGELOG.md\natlas/modules/llm/client.py\n' > "$SANDBOX/changed.txt"
python3 "$FRAGMENTS" guard --changed-files "$SANDBOX/changed.txt" >/dev/null 2>&1
if [ $? -eq 1 ]; then
    print_result 0 "guard fails a normal PR that edits CHANGELOG.md"
else
    print_result 1 "guard fails a normal PR that edits CHANGELOG.md"
fi

python3 "$FRAGMENTS" guard --changed-files "$SANDBOX/changed.txt" --allow-changelog >/dev/null 2>&1
if [ $? -eq 0 ]; then
    print_result 0 "guard exempts release branches"
else
    print_result 1 "guard exempts release branches"
fi

printf 'atlas/modules/llm/client.py\nchanges/1002.feature.md\n' > "$SANDBOX/clean.txt"
python3 "$FRAGMENTS" guard --changed-files "$SANDBOX/clean.txt" >/dev/null 2>&1
if [ $? -eq 0 ]; then
    print_result 0 "guard passes when CHANGELOG.md is untouched"
else
    print_result 1 "guard passes when CHANGELOG.md is untouched"
fi

printf 'atlas/modules/llm/client.py\n' > "$SANDBOX/nofrag.txt"
python3 "$FRAGMENTS" guard --changed-files "$SANDBOX/nofrag.txt" --require-fragment >/dev/null 2>&1
if [ $? -eq 1 ]; then
    print_result 0 "guard --require-fragment fails a normal PR with no fragment"
else
    print_result 1 "guard --require-fragment fails a normal PR with no fragment"
fi

# ==========================================
print_header "Check 6b: collect targets an already-cut version section"
# ==========================================
SECTION_DIR="$SANDBOX/section"
mkdir -p "$SECTION_DIR/changes"
cp "$FRAGMENTS" "$SECTION_DIR/"
printf 'Fixed after the cut.\n' > "$SECTION_DIR/changes/1099.fix.md"
cat > "$SECTION_DIR/CHANGELOG.md" <<'EOF'
# Changelog

## [Unreleased]

## [0.7.0] - 2026-10-06

- Existing.

## [0.6.0] - 2026-09-22
EOF
( cd "$SECTION_DIR" && python3 changelog_fragments.py collect --section 0.7.0 ) >/dev/null 2>&1
SECTION_RC=$?
if [ "$SECTION_RC" -eq 0 ] \
    && awk '/^## \[0.7.0\]/{f=1} /^## \[0.6.0\]/{f=0} f && /- \*\*#1099:\*\*/{print; found=1} END{exit !found}' "$SECTION_DIR/CHANGELOG.md" >/dev/null; then
    print_result 0 "collect --section 0.7.0 lands the fix under the versioned section"
else
    print_result 1 "collect --section 0.7.0 lands the fix under the versioned section"
fi

# ==========================================
print_header "Check 7: workflows compose and guard fragments"
# ==========================================
WEEKLY="$PROJECT_ROOT/.github/workflows/release-weekly.yml"
CUT="$PROJECT_ROOT/.github/workflows/release-cut.yml"
ARTIFACTS="$PROJECT_ROOT/.github/workflows/build-artifacts.yml"
WEEKLY_OK=1
if grep -q "changelog_fragments.py collect" "$WEEKLY" \
    && grep -q "changelog_fragments.py check" "$WEEKLY" \
    && grep -qE 'git add .*changes' "$WEEKLY"; then
    WEEKLY_OK=0
fi
if [ "$WEEKLY_OK" -eq 0 ]; then
    print_result 0 "release-weekly.yml checks and composes fragments, stages changes/"
else
    print_result 1 "release-weekly.yml checks and composes fragments, stages changes/"
fi
CUT_OK=1
if grep -q "changelog_fragments.py collect" "$CUT" \
    && grep -qE 'git add .*changes' "$CUT"; then
    CUT_OK=0
fi
if [ "$CUT_OK" -eq 0 ]; then
    print_result 0 "release-cut.yml composes fragments and stages changes/"
else
    print_result 1 "release-cut.yml composes fragments and stages changes/"
fi
if grep -q "changelog_fragments.py validate" "$ARTIFACTS" \
    && grep -q "changelog_fragments.py guard" "$ARTIFACTS" \
    && grep -q -- "--require-fragment" "$ARTIFACTS" \
    && grep -q "HEAD_REPO" "$ARTIFACTS" \
    && grep -q "THIS_REPO" "$ARTIFACTS"; then
    print_result 0 "build-artifacts.yml validates and guards fragments (incl. fork-safe exemption)"
else
    print_result 1 "build-artifacts.yml validates and guards fragments (incl. fork-safe exemption)"
fi

# ==========================================
print_header "Final: backend unit tests"
# ==========================================
# The release tooling lives outside the atlas package; the backend suite still
# runs to catch accidental regressions in the diff.
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
