#!/bin/bash
# Exercise refresh-on-use and IdP refusal through the real OIDC endpoints.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT"
python test/pr-validation/fixtures/pr1019/session_lifetime.py
bash test/run_tests.sh backend
