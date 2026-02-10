#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
K3S_MANIFESTS="$SCRIPT_DIR"
NAMESPACE="atlas"

# ---------------------------------------------------------------------------
# Load .env for variable interpolation
# ---------------------------------------------------------------------------
load_env() {
    if [ -f "$PROJECT_ROOT/.env" ]; then
        set -a
        # shellcheck disable=SC1091
        . "$PROJECT_ROOT/.env"
        set +a
    else
        echo "Warning: $PROJECT_ROOT/.env not found"
    fi

    # Defaults for variables that may not be in .env
    export S3_BUCKET_NAME="${S3_BUCKET_NAME:-atlas-files}"
    export S3_ACCESS_KEY="${S3_ACCESS_KEY:-minioadmin}"
    export S3_SECRET_KEY="${S3_SECRET_KEY:-minioadmin}"
    export ATLAS_AUTH_SESSION_HOURS="${ATLAS_AUTH_SESSION_HOURS:-24}"
    export VITE_APP_NAME="${VITE_APP_NAME:-ATLAS}"
    export VITE_FEATURE_POWERED_BY_ATLAS="${VITE_FEATURE_POWERED_BY_ATLAS:-false}"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
kubectl_cmd() {
    sudo k3s kubectl "$@"
}

# Map friendly service names to deployment names
resolve_deployment() {
    local svc="${1:-}"
    case "$svc" in
        atlas-auth|auth)  echo "atlas-auth" ;;
        atlas-ui|ui)      echo "atlas-ui" ;;
        minio)            echo "minio" ;;
        *)                echo "$svc" ;;
    esac
}

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
cmd_build() {
    echo "Building container images with podman..."

    echo "  Building atlas-ui..."
    podman build \
        --build-arg VITE_APP_NAME="${VITE_APP_NAME:-ATLAS}" \
        --build-arg VITE_FEATURE_POWERED_BY_ATLAS="${VITE_FEATURE_POWERED_BY_ATLAS:-false}" \
        -t localhost/atlas-ui:latest \
        -f "$PROJECT_ROOT/Dockerfile" \
        "$PROJECT_ROOT"

    echo "  Building atlas-auth..."
    podman build \
        -t localhost/atlas-auth:latest \
        -f "$PROJECT_ROOT/deploy/auth-service/Dockerfile" \
        "$PROJECT_ROOT/deploy/auth-service"

    echo "Importing images into k3s containerd..."
    podman save localhost/atlas-ui:latest | sudo k3s ctr images import -
    podman save localhost/atlas-auth:latest | sudo k3s ctr images import -

    echo "Build complete. Images imported into k3s."
}

cmd_up() {
    load_env

    echo "Deploying ATLAS to k3s..."

    # Apply namespace first
    kubectl_cmd apply -f "$K3S_MANIFESTS/00-namespace.yaml"

    # Generate secrets from template using envsubst
    echo "  Generating secrets from .env..."
    envsubst < "$K3S_MANIFESTS/01-secrets.yaml" | kubectl_cmd apply -f -

    # Apply deployments and services
    kubectl_cmd apply -f "$K3S_MANIFESTS/10-atlas-auth.yaml"
    kubectl_cmd apply -f "$K3S_MANIFESTS/11-atlas-ui.yaml"
    kubectl_cmd apply -f "$K3S_MANIFESTS/12-minio.yaml"

    # Delete previous init job if it exists, then recreate
    kubectl_cmd delete job minio-init -n "$NAMESPACE" --ignore-not-found
    envsubst < "$K3S_MANIFESTS/13-minio-init-job.yaml" | kubectl_cmd apply -f -

    # Apply Traefik middleware and ingress routes
    kubectl_cmd apply -f "$K3S_MANIFESTS/20-middleware.yaml"
    kubectl_cmd apply -f "$K3S_MANIFESTS/21-ingressroute.yaml"

    # Copy Traefik config to K3s manifests dir (auto-applied by K3s)
    echo "  Configuring Traefik..."
    sudo cp "$K3S_MANIFESTS/traefik-config.yaml" /var/lib/rancher/k3s/server/manifests/traefik-config.yaml

    # Wait for rollouts
    echo "  Waiting for deployments to be ready..."
    kubectl_cmd rollout status deployment/atlas-auth -n "$NAMESPACE" --timeout=120s
    kubectl_cmd rollout status deployment/minio -n "$NAMESPACE" --timeout=120s
    kubectl_cmd rollout status deployment/atlas-ui -n "$NAMESPACE" --timeout=180s

    echo ""
    echo "ATLAS is deployed. Access at http://localhost:8080"
}

cmd_down() {
    echo "Tearing down ATLAS..."
    kubectl_cmd delete namespace "$NAMESPACE" --ignore-not-found
    echo "Namespace '$NAMESPACE' deleted."
}

cmd_restart() {
    if [ $# -gt 0 ]; then
        local deploy
        deploy=$(resolve_deployment "$1")
        echo "Restarting deployment/$deploy..."
        kubectl_cmd rollout restart "deployment/$deploy" -n "$NAMESPACE"
        kubectl_cmd rollout status "deployment/$deploy" -n "$NAMESPACE" --timeout=120s
    else
        echo "Restarting all deployments..."
        kubectl_cmd rollout restart deployment -n "$NAMESPACE"
        kubectl_cmd rollout status deployment -n "$NAMESPACE" --timeout=180s
    fi
}

cmd_logs() {
    if [ $# -gt 0 ]; then
        local deploy
        deploy=$(resolve_deployment "$1")
        shift
        kubectl_cmd logs -f "deployment/$deploy" -n "$NAMESPACE" "$@"
    else
        # Follow all pods in the namespace
        kubectl_cmd logs -f --all-containers --max-log-requests=10 -n "$NAMESPACE" -l 'app in (atlas-auth,atlas-ui,minio)'
    fi
}

cmd_status() {
    echo "=== Nodes ==="
    kubectl_cmd get nodes
    echo ""
    echo "=== Namespace: $NAMESPACE ==="
    kubectl_cmd get all -n "$NAMESPACE" 2>/dev/null || echo "(namespace not found)"
    echo ""
    echo "=== IngressRoutes ==="
    kubectl_cmd get ingressroute -n "$NAMESPACE" 2>/dev/null || echo "(none)"
    echo ""
    echo "=== Middlewares ==="
    kubectl_cmd get middleware -n "$NAMESPACE" 2>/dev/null || echo "(none)"
}

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
ATLAS K3s Deployment

Usage: $0 <command> [args...]

Commands:
  build              Build images with podman and import into k3s
  up                 Deploy all manifests to k3s
  down               Delete the atlas namespace (removes everything)
  restart [svc]      Restart deployment(s): atlas-auth, atlas-ui, minio
  logs    [svc]      Follow logs for a deployment (or all)
  status             Show cluster and namespace status

Service aliases:
  auth, atlas-auth   -> atlas-auth deployment
  ui, atlas-ui       -> atlas-ui deployment
  minio              -> minio deployment

Examples:
  $0 build                   # Build and import images
  $0 up                      # Deploy everything
  $0 logs atlas-auth         # Follow auth service logs
  $0 restart ui              # Restart the UI deployment
  $0 down                    # Tear down everything
EOF
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
load_env

case "${1:-}" in
    build)   shift; cmd_build "$@" ;;
    up)      shift; cmd_up "$@" ;;
    down)    shift; cmd_down "$@" ;;
    restart) shift; cmd_restart "$@" ;;
    logs)    shift; cmd_logs "$@" ;;
    status)  shift; cmd_status "$@" ;;
    -h|--help|help|"")
        usage ;;
    *)
        echo "Unknown command: $1"
        usage
        exit 1 ;;
esac
