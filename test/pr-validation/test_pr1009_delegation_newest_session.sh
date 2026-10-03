#!/bin/bash
# PR #1009 Validation Script: delegation uses the user's newest usable session
#
# Drives the real Atlas app against a real (minimal) OIDC provider started on
# localhost. The same user signs in twice; the first login's IdP session then
# ends (its refresh is refused, as after an idle timeout). A delegated token
# exchange must use the second login's token instead of failing.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #1009 Validation: delegation uses the newest usable session"
echo "=========================================="

cd "$PROJECT_ROOT"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT"

python "$SCRIPT_DIR/fixtures/pr1009/two_sessions.py"
