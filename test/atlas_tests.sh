#!/bin/bash
set -e

echo "Running Atlas (Python) Tests..."
echo "================================="

# Use PROJECT_ROOT if set by master script, otherwise detect
if [ -z "$PROJECT_ROOT" ]; then
    if [ -d "/app" ]; then
        PROJECT_ROOT="/app"
    elif [ -d "../atlas" ]; then
        # Running from test/ directory
        PROJECT_ROOT="$(pwd)/.."
    else
        # Running from project root
        PROJECT_ROOT="$(pwd)"
    fi
fi

# Set up Python environment and paths
ATLAS_DIR="$PROJECT_ROOT/atlas"

echo "Atlas directory: $ATLAS_DIR"

# Issue #1070: a PYTHONPATH exported in the invoking shell (e.g. inherited from
# a deployment context) can point subprocess-spawned tools at another checkout.
# atlas/tests/conftest.py filters it to entries inside this checkout for the
# pytest session, and the block below filters it here as well, before the
# interpreter starts; the warning is so the operator notices their shell
# carries it at all.
if [ -n "${PYTHONPATH:-}" ]; then
    echo "WARNING: PYTHONPATH is exported in the invoking shell: ${PYTHONPATH}"
    echo "This wrapper filters it to entries inside this checkout before pytest"
    echo "starts; a foreign entry can still affect other tooling in this shell."
fi

# Activate project virtual environment if available (per CLAUDE.md: use uv-managed venv)
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    echo "Activating virtual environment at $PROJECT_ROOT/.venv"
    # shellcheck disable=SC1090
    source "$PROJECT_ROOT/.venv/bin/activate"
else
    echo "WARNING: .venv not found. Atlas tests expect uv-managed venv with pytest installed."
    echo "If tests fail due to missing packages, run: uv venv && source .venv/bin/activate && uv pip install -e '.[dev]'"
fi

# Issue #1070: filter PYTHONPATH to entries inside this checkout BEFORE the
# interpreter starts, not only inside conftest. sys.path is built from
# PYTHONPATH at startup, so an entry still exported here is already importable
# inside the pytest process itself; conftest's session filter (which this does
# not replace) protects only the subprocesses pytest spawns. A bare
# `python -m pytest` run keeps conftest's subprocess protection only -- see
# docs/developer/test-isolation.md.
if [ -n "${PYTHONPATH:-}" ]; then
    FILTERED_PYTHONPATH="$(PROJECT_ROOT="$PROJECT_ROOT" python - <<'PYEOF'
import os
from pathlib import Path

root = Path(os.environ["PROJECT_ROOT"]).resolve()
kept = []
for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep):
    if not entry.strip():
        continue
    try:
        resolved = Path(entry).resolve()
    except OSError:
        continue
    if resolved == root or root in resolved.parents:
        kept.append(str(resolved))
print(os.pathsep.join(dict.fromkeys(kept)))
PYEOF
)"
    if [ -n "$FILTERED_PYTHONPATH" ]; then
        export PYTHONPATH="$FILTERED_PYTHONPATH"
    else
        unset PYTHONPATH
    fi
fi

# Change to atlas directory
cd "$ATLAS_DIR"

echo ""
echo "Running Atlas Tests..."
echo "ATLAS_DIR full path: $(pwd)"
echo "=============================="

# If legacy targeted tests exist, run them; otherwise run all tests in atlas/tests
if [ -f tests/test_config_module.py ] || [ -f tests/test_file_storage_module.py ] || [ -f tests/test_llm_module.py ]; then
    echo "Detected legacy targeted tests; running individually"
    [ -f tests/test_config_module.py ] && timeout 60 python -m pytest tests/test_config_module.py -v --tb=short || true
    [ -f tests/test_file_storage_module.py ] && timeout 60 python -m pytest tests/test_file_storage_module.py -v --tb=short || true
    [ -f tests/test_llm_module.py ] && timeout 60 python -m pytest tests/test_llm_module.py -v --tb=short || true
fi

echo "Running pytest on atlas/tests directory"
# PYTEST_ARGS lets a caller add plugins/options (e.g. "-n 2" for pytest-xdist,
# issue #1057) without a second, divergent pytest invocation.
# shellcheck disable=SC2086
timeout --foreground 300 python -u -m pytest tests -v --tb=short ${PYTEST_ARGS:-}

echo "Atlas tests completed"
