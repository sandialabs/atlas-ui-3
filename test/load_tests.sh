#!/bin/bash
# Load and scale profiles (issue #1129): N real Atlas replicas sharing one Redis
# and one Postgres (the multi-replica harness from PR #1146), mock LLM and
# optional mock OIDC IdP, driven by async WebSocket/REST virtual users. No real
# LLM. Reports p50/p95/p99 latency, errors, active runs, connection counts and
# resource usage; see docs/developer/load-testing.md.
#
# Usage: bash test/load_tests.sh [profile] [atlas_load options]
#        bash test/load_tests.sh --list
#        bash test/load_tests.sh reconnect-storm --scale 2 --out /tmp/storm
#        bash test/load_tests.sh ws-capacity --target https://atlas.staging --service-key ...
#
# Exit status: 0 when every SLO check in the profile passed, 1 otherwise.
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$PROJECT_ROOT"

if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

# Replicas must import this checkout's atlas package, not another one an
# editable install points at.
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
python - <<'PY'
import os, sys
import atlas
root = os.environ["PYTHONPATH"].split(os.pathsep)[0]
if not os.path.realpath(atlas.__file__).startswith(os.path.realpath(root) + os.sep):
    sys.exit(f"atlas resolves to {atlas.__file__}, not this checkout ({root})")
PY

args=("$@")
if [ ${#args[@]} -gt 0 ] && [[ "${args[0]}" != -* ]]; then
    args=(--profile "${args[0]}" "${args[@]:1}")
fi
cd "$PROJECT_ROOT/test/loadtest"
exec python -m atlas_load "${args[@]}"
