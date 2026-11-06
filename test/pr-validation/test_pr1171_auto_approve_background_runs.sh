#!/bin/bash
# Test script for PR #1171: server-side auto-approve for background runs
#
# Test plan:
# - A tracked run with auto-approve on executes a user-level tool and emits
#   no approval request, including when no client callback is connected.
# - Auto-approve off still asks, times out, and still accepts an explicit
#   approval response.
# - An admin-required tool still waits when the switch is on.
# - A launched child is not covered by the user's switch.
# - Only a real boolean true opts in.
# - The live switch update does not touch another user's runs or a child.
# - Existing approval cleanup and reopened-run approval tests still pass.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
elif [ -f "$PROJECT_ROOT/../atlas-ui-3/.venv/bin/activate" ]; then
    # A worktree often has no venv of its own. The sibling checkout's
    # editable install is remapped onto this tree below.
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/../atlas-ui-3/.venv/bin/activate"
fi

export PROJECT_ROOT

python - <<'PY'
import importlib
import os
import pathlib
import sys

root = pathlib.Path(os.environ["PROJECT_ROOT"]).resolve()
finder = None
for entry in list(sys.path):
    if not entry:
        continue
    directory = pathlib.Path(entry)
    if not directory.is_dir():
        continue
    for path in directory.glob("__editable___atlas_chat_*_finder.py"):
        finder = importlib.import_module(path.stem)
        break
    if finder is not None:
        break

mapping = getattr(finder, "MAPPING", None) if finder is not None else None
if mapping and "atlas" in mapping:
    current = pathlib.Path(mapping["atlas"]).resolve().parent
    if current != root:
        old, new = str(current), str(root)
        finder.MAPPING = {k: v.replace(old, new) for k, v in mapping.items()}
        finder.NAMESPACES = {
            k: [p.replace(old, new) for p in paths]
            for k, paths in finder.NAMESPACES.items()
        }
        print(f"remapped editable atlas install {old} -> {new}")

import atlas
installed = pathlib.Path(atlas.__file__).resolve()
if root not in installed.parents:
    print(f"FAILED: atlas resolved to {installed}, expected under {root}")
    sys.exit(1)
print(f"atlas: {installed}")

import pytest

code = pytest.main([
    "-q",
    "-p",
    "no:cacheprovider",
    "atlas/tests/test_auto_approve_background.py",
    "atlas/tests/test_tool_approval_cleanup.py",
    "atlas/tests/test_reopened_run_approval.py",
])
sys.exit(code)
PY
status=$?
if [ "$status" -eq 0 ]; then
    echo "PASSED: PR #1171 server-side auto-approve"
else
    echo "FAILED: PR #1171 server-side auto-approve (exit $status)"
fi
exit "$status"
