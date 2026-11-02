#!/bin/bash
# Multi-replica harness (issue #1128): two real Atlas API processes sharing one
# Redis and one Postgres, driven by pytest.
#
# Not part of test/atlas_tests.sh or CI. Run it before opening a PR that touches
# run coordination (see AGENTS.md "Horizontal Scaling") and include the summary
# in the PR's validation output.
#
# Needs podman (Redis and Postgres are started as throwaway containers), or
# ATLAS_TEST_REDIS_URL / ATLAS_TEST_POSTGRES_URL pointing at servers you run.
# ATLAS_MULTIREPLICA_KEEP=1 keeps the work dir (replica logs) afterwards.
#
# Usage: bash test/multireplica_tests.sh [extra pytest args]
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$PROJECT_ROOT"

if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
fi

if [ -z "${ATLAS_TEST_REDIS_URL:-}" ] || [ -z "${ATLAS_TEST_POSTGRES_URL:-}" ]; then
    if ! command -v podman > /dev/null; then
        echo "podman not found; install it or set ATLAS_TEST_REDIS_URL and ATLAS_TEST_POSTGRES_URL" >&2
        exit 2
    fi
fi

# The replicas must import this checkout's atlas package, not another one an
# editable install points at.
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
python - <<'PY'
import os, sys
import atlas
root = os.environ["PYTHONPATH"].split(os.pathsep)[0]
if not os.path.realpath(atlas.__file__).startswith(os.path.realpath(root) + os.sep):
    sys.exit(f"atlas resolves to {atlas.__file__}, not this checkout ({root})")
PY

exec python -m pytest test/multireplica -v -p no:cacheprovider -rxX "$@"
