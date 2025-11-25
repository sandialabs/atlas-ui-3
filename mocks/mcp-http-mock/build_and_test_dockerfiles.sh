#!/bin/bash

# MCP HTTP Mock Server Docker Test Script
# This script builds all Docker images and tests them one by one

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🚀 Starting MCP HTTP Mock Server Docker Build and Test Script"
echo "================================================================"
echo ""

DOCKERFILES=("Dockerfile.ubuntu" "Dockerfile.rhel")
TEST_PREFIX="mcp-http-mock-test"
BUILD_CONTEXT="."

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

MAX_RETRIES=30
RETRY_INTERVAL=2

# Function to log messages with colors
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $1${NC}" >&2
}

warn() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING: $1${NC}"
}

info() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] INFO: $1${NC}"
}

# Function to wait for health check
wait_for_health() {
    local container_name="$1"
    local port="$2"
    local url="http://localhost:$port/mcp"
    local retry=0

    info "Waiting for $container_name to be healthy on http://localhost:$port/mcp"
    info "This may take up to ${MAX_RETRIES}0 seconds..."

    while [ $retry -lt $MAX_RETRIES ]; do
        if curl -f --max-time 5 "$url" > /dev/null 2>&1; then
            log "✅ $container_name is healthy!"
            return 0
        fi

        retry=$((retry + 1))
        info "Attempt $retry/$MAX_RETRIES - $container_name not ready yet..."
        sleep $RETRY_INTERVAL
    done

    error "❌ $container_name failed health check after ${MAX_RETRIES} attempts"
    return 1
}

# Function to build a docker image
build_image() {
    local dockerfile="$1"
    local image_name="$2"
    local base_name="${dockerfile#Dockerfile.}"
    local tag="${TEST_PREFIX}-${base_name}:latest"

    info "Building image: $tag from $dockerfile"

    if docker build -f "$dockerfile" -t "$tag" "$BUILD_CONTEXT"; then
        log "✅ Successfully built $tag"
        echo "$tag" >> "$image_name"
        return 0
    else
        error "❌ Failed to build $tag"
        return 1
    fi
}

# Function to test a docker image
test_image() {
    local image_tag="$1"
    local base_name="${image_tag#${TEST_PREFIX}-}"
    base_name="${base_name%:latest}"
    local container_name="test-${base_name}-$(date +%s)"
    local port
    local test_result=false

    # Assign unique ports for testing
    case "$base_name" in
        ubuntu) port=8005 ;;
        fedora) port=8006 ;;
        rhel) port=8007 ;;
        *) port=8008 ;; # fallback
    esac

    info "Testing image: $image_tag as $container_name on port $port"

    # Start container in detached mode
    if docker run -d --name "$container_name" -p "$port:8005" \
        -e MCP_MOCK_TOKEN_1=test-api-key-123 \
        -e MCP_MOCK_TOKEN_2=another-test-key-456 \
        -e PYTHONUNBUFFERED=1 \
        "$image_tag"; then

        log "✅ Container $container_name started successfully"

        # Wait for health check
        if wait_for_health "$container_name" "$port"; then
            # Additional test: try to access MCP endpoint with authentication
            info "Running additional MCP endpoint test..."
            if curl -H "Authorization: Bearer test-api-key-123" \
                --max-time 5 "http://localhost:$port/mcp" > /dev/null 2>&1; then
                log "✅ MCP endpoint accessible with authentication"
                test_result=true
            else
                error "❌ MCP endpoint authentication test failed"
            fi
        fi

        # Clean up container
        info "Stopping and removing container: $container_name"
        docker stop "$container_name" > /dev/null 2>&1 || warn "Failed to stop container"
        docker rm "$container_name" > /dev/null 2>&1 || warn "Failed to remove container"

    else
        error "❌ Failed to start container $container_name"
    fi

    return $test_result
}

# Function to cleanup built images
cleanup_images() {
    if [ -f "$IMAGE_LIST_FILE" ]; then
        info "Cleaning up built images..."
        while IFS= read -r image_tag; do
            if [ -n "$image_tag" ]; then
                info "Removing image: $image_tag"
                docker rmi "$image_tag" > /dev/null 2>&1 || warn "Failed to remove $image_tag"
            fi
        done < "$IMAGE_LIST_FILE"
        rm -f "$IMAGE_LIST_FILE"
        log "✅ Cleanup completed"
    fi
}

# Main script
IMAGE_LIST_FILE="$(mktemp)"
trap 'cleanup_images; exit 1' INT TERM EXIT

# Check if docker is available
if ! command -v docker &> /dev/null; then
    error "Docker is not installed or not in PATH"
    exit 1
fi

# Check if docker daemon is running
if ! docker info > /dev/null 2>&1; then
    error "Docker daemon is not running. Please start Docker first."
    exit 1
fi

echo ""
log "Phase 1: Building Docker Images"
echo "==============================="

built_images=()
for dockerfile in "${DOCKERFILES[@]}"; do
    if [ -f "$dockerfile" ]; then
        info "Found $dockerfile"
        if build_image "$dockerfile" "$IMAGE_LIST_FILE"; then
            built_images+=("$(tail -n1 "$IMAGE_LIST_FILE")")
        else
            error "Build failed for $dockerfile"
            exit 1
        fi
    else
        warn "$dockerfile not found, skipping"
    fi
done

if [ ${#built_images[@]} -eq 0 ]; then
    error "No images were successfully built"
    exit 1
fi

echo ""
log "Phase 2: Testing Docker Images"
echo "==============================="

successful_tests=0
failed_tests=0
test_results=()

for image_tag in "${built_images[@]}"; do
    base_name="${image_tag#${TEST_PREFIX}-}"
    base_name="${base_name%:latest}"

    echo ""
    info "Starting test for $base_name"
    echo "----------------------------"

    if test_image "$image_tag"; then
        successful_tests=$((successful_tests + 1))
        test_results+=("$base_name: ✅ PASSED")
    else
        failed_tests=$((failed_tests + 1))
        test_results+=("$base_name: ❌ FAILED")
    fi
done

echo ""
echo "================================"
echo "        TEST RESULTS           "
echo "================================"

for result in "${test_results[@]}"; do
    if [[ "$result" == *"✅"* ]]; then
        echo -e "${GREEN}$result${NC}"
    else
        echo -e "${RED}$result${NC}"
    fi
done

echo ""
echo "Summary: $successful_tests passed, $failed_tests failed"

if [ $failed_tests -eq 0 ]; then
    log "🎉 All Docker images built and tested successfully!"
    exit 0
else
    error "❌ $failed_tests Docker image(s) failed testing"
    exit 1
fi
