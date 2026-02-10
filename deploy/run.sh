#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$SCRIPT_DIR/docker-compose.prod.yml"

# ---------------------------------------------------------------------------
# Load .env for variable interpolation
# ---------------------------------------------------------------------------
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$PROJECT_ROOT/.env"
    set +a
fi

# ---------------------------------------------------------------------------
# Auto-detect compose command
# ---------------------------------------------------------------------------
detect_compose() {
    if command -v podman-compose &>/dev/null && podman-compose version &>/dev/null 2>&1; then
        echo "podman-compose"
    elif podman compose version &>/dev/null 2>&1; then
        echo "podman compose"
    elif docker compose version &>/dev/null 2>&1; then
        echo "docker compose"
    elif command -v docker-compose &>/dev/null; then
        echo "docker-compose"
    else
        echo ""
    fi
}

COMPOSE_CMD=$(detect_compose)
if [ -z "$COMPOSE_CMD" ]; then
    echo "Error: No container compose tool found."
    echo "Install one of: podman-compose, podman compose, docker compose, docker-compose"
    exit 1
fi

echo "Using: $COMPOSE_CMD"

compose() {
    $COMPOSE_CMD -f "$COMPOSE_FILE" "$@"
}

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
cmd_build() {
    echo "Building images..."
    compose build "$@"
}

cmd_up() {
    echo "Starting stack..."
    compose up -d "$@"
    echo ""
    echo "Stack is up. Access ATLAS at http://localhost:8080"
}

cmd_down() {
    echo "Stopping stack..."
    compose down "$@"
}

cmd_restart() {
    if [ $# -gt 0 ]; then
        echo "Restarting: $*"
        compose restart "$@"
    else
        echo "Restarting entire stack..."
        compose down
        compose up -d
    fi
}

cmd_logs() {
    compose logs "$@"
}

cmd_status() {
    compose ps "$@"
}

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
ATLAS Production Deployment

Usage: $0 <command> [args...]

Commands:
  build   [svc...]   Build (or rebuild) container images
  up      [svc...]   Start the stack in detached mode
  down    [args...]  Stop and remove containers
  restart [svc...]   Restart services (or full stack if no args)
  logs    [args...]  View container logs (e.g. -f atlas-auth)
  status  [args...]  Show container status

Examples:
  $0 build                   # Build all images
  $0 up                      # Start everything
  $0 logs -f atlas-auth      # Follow auth service logs
  $0 restart atlas-ui        # Restart just the UI
  $0 down                    # Stop everything
EOF
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
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
