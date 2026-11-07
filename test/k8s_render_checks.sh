#!/bin/bash
# Offline checks of the Kubernetes/OpenShift staging manifests (issue #1129):
# renders deploy/kubernetes with `kubectl kustomize` (no cluster contacted)
# and asserts replicas, probes, limits, migrations, shared state and budgets.
# CI runs it on the host because the hermetic test image has no kubectl.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v kubectl >/dev/null 2>&1; then
    echo "FAILED: kubectl is required (it renders the kustomizations offline)"
    exit 1
fi
# PyYAML: the system python3 on CI runners ships it; otherwise borrow it
# through uv (never pip) so the check needs no project environment.
if python3 -c 'import yaml' >/dev/null 2>&1; then
    exec python3 "$SCRIPT_DIR/k8s_render_checks.py"
elif command -v uv >/dev/null 2>&1; then
    exec uv run --no-project --with pyyaml python3 "$SCRIPT_DIR/k8s_render_checks.py"
fi
echo "FAILED: python3 has no PyYAML and uv is not available to provide it"
exit 1
