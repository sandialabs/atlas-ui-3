#!/bin/bash
# Issue #1225: a launched sub-conversation pauses only on admin-gated tools,
# and a message sent to a running one is refused with advice that can work.
# Drives the real launcher, the real tool executor gate and the run-control
# steer path with a stub chat service (no LLM, no network, no server).
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source .venv/bin/activate

export PYTHONPATH="$PROJECT_ROOT"
python -m pytest atlas/tests/test_child_approval_scope.py -q -p no:cacheprovider
python -m pytest atlas/tests/test_atlas_launch_tool.py atlas/tests/test_auto_approve_background.py -q -p no:cacheprovider
echo "PASS: child approval scope and busy-message validation"
