#!/bin/bash
# Issue #1221: child run frames keep their own identity, and a grandchild's
# approval request does not mark the middle child as waiting.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT"

python -m pytest atlas/tests/test_child_frame_identity.py atlas/tests/test_atlas_launch_tool.py \
    atlas/tests/test_execution_state.py -q

# Run-coordination code changed: the two-replica suite is part of the gate.
bash "$PROJECT_ROOT/test/multireplica_tests.sh"
echo "PASSED: child frame identity validation"
