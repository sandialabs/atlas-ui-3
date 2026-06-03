#!/usr/bin/env bash
# Deploy (or update) the demo HTTP MCP server into the cluster for Agent
# Portal V3 testing. Idempotent: re-run after editing server.py to roll the
# new source out.
#
#   ./deploy.sh [namespace]   (default namespace: atlas)
#
# Requires the agent-runner image to be present in the cluster first:
#   ./atlas/agent_runner_v3/build_and_load.sh
set -euo pipefail

NS="${1:-atlas}"
DIR="$(cd "$(dirname "$0")" && pwd)"

# Prefer a kubectl that can read the cluster; fall back to `k3s kubectl`.
if kubectl version >/dev/null 2>&1; then
  KUBECTL="kubectl"
else
  KUBECTL="sudo k3s kubectl"
fi

echo "[1/3] Creating/updating ConfigMap mcp-tools-src from server.py (ns=$NS)..."
$KUBECTL create configmap mcp-tools-src \
  --from-file=server.py="$DIR/server.py" \
  -n "$NS" \
  --dry-run=client -o yaml | $KUBECTL apply -f -

echo "[2/3] Applying Deployment + Service..."
$KUBECTL apply -n "$NS" -f "$DIR/k8s.yaml"

echo "[3/3] Rolling deployment to pick up latest server.py..."
$KUBECTL rollout restart deployment/mcp-tools -n "$NS"
$KUBECTL rollout status deployment/mcp-tools -n "$NS" --timeout=120s

echo
echo "Done. In-cluster MCP endpoint:"
echo "  http://mcp-tools.$NS.svc.cluster.local/mcp"
echo
echo "Add this to mcp.json (already included as 'mcp_tools_demo' for ns=atlas):"
echo '  "mcp_tools_demo": { "transport": "http", "url": "http://mcp-tools.atlas.svc.cluster.local/mcp", ... }'
