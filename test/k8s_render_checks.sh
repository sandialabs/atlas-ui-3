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
exec python3 "$SCRIPT_DIR/k8s_render_checks.py"
