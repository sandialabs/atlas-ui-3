#!/usr/bin/env bash
# Build the agent runner image and load it into the local k3s containerd.
# Run from the project root (atlas-ui-3/).
set -euo pipefail

IMAGE="localhost/atlas-agent-runner:dev"
TARFILE="/tmp/atlas-agent-runner.tar"

cd "$(dirname "$0")/../.."

echo "[1/3] Building $IMAGE with podman..."
podman build -t "$IMAGE" -f atlas/agent_runner_v3/Dockerfile .

echo "[2/3] Saving to $TARFILE..."
podman save -o "$TARFILE" "$IMAGE"

echo "[3/3] Importing into k3s containerd (needs sudo)..."
sudo k3s ctr images import "$TARFILE"

echo
echo "Done. The K8s Job manifest references image '$IMAGE' with"
echo "imagePullPolicy=IfNotPresent, so k3s will use the local copy."
