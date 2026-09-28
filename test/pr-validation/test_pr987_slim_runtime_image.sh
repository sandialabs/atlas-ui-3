#!/bin/bash
# PR #987 Validation Script: Slim down Dockerfile.runtimeonly (issue #986)
# Builds the runtime-only image and checks that the final stage is the plain
# Chainguard python image (no shell), that /app is owned by nonroot without a
# separate chown layer, and that the container serves /api/health and the UI.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "PR #987 Validation: Slim Runtime-only Image"
echo "=========================================="

cd "$PROJECT_ROOT"

# Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

CLI="${CONTAINER_CLI:-}"
if [ -z "$CLI" ]; then
    if command -v docker > /dev/null 2>&1; then CLI=docker
    elif command -v podman > /dev/null 2>&1; then CLI=podman
    else
        echo "FAILED: docker or podman is required to build the image"
        exit 1
    fi
fi
IMAGE="atlas-ui-3-runtime-pr987:test"
CONTAINER="atlas-pr987-validation"
PORT=8211

cleanup() {
    "$CLI" rm -f "$CONTAINER" > /dev/null 2>&1 || true
}
trap cleanup EXIT

echo ""
echo "1. Check the Dockerfile structure"
echo "---------------------------------"
FINAL_FROM="$(grep '^FROM ' Dockerfile.runtimeonly | tail -1)"
if [ "$FINAL_FROM" = "FROM cgr.dev/chainguard/python:latest" ]; then
    echo "PASSED: final stage is the plain python image"
else
    echo "FAILED: final stage is '$FINAL_FROM'"
    exit 1
fi
if grep -qE '^RUN .*chown -R' Dockerfile.runtimeonly; then
    echo "FAILED: Dockerfile.runtimeonly still has a chown -R layer"
    exit 1
else
    echo "PASSED: no chown -R layer"
fi

echo ""
echo "2. Build the image with $CLI"
echo "-----------------------------"
"$CLI" build -f Dockerfile.runtimeonly -t "$IMAGE" . > /tmp/pr987-build.log 2>&1 || {
    tail -30 /tmp/pr987-build.log
    echo "FAILED: image build (full log in /tmp/pr987-build.log)"
    exit 1
}
echo "PASSED: image built"
"$CLI" image ls "$IMAGE"

echo ""
echo "3. Check the final image contents"
echo "---------------------------------"
if "$CLI" run --rm --entrypoint /bin/sh "$IMAGE" -c true > /dev/null 2>&1; then
    echo "FAILED: /bin/sh exists in the final image"
    exit 1
else
    echo "PASSED: no shell in the final image"
fi
CHECK="$("$CLI" run --rm "$IMAGE" python -c 'import os, shutil, sys; print(os.getuid(), os.stat("/app/.venv").st_uid, sys.prefix, shutil.which("gcc") is None)')"
if [ "$CHECK" = "65532 65532 /app/.venv True" ]; then
    echo "PASSED: runs as 65532, /app/.venv owned by 65532, venv on PATH, no gcc"
else
    echo "FAILED: unexpected uid/owner/tools: $CHECK"
    exit 1
fi

echo ""
echo "4. Start the container and exercise it"
echo "--------------------------------------"
KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
"$CLI" run -d --name "$CONTAINER" -p "127.0.0.1:$PORT:8000" \
    -e MCP_TOKEN_ENCRYPTION_KEY="$KEY" -e DEBUG_MODE=true "$IMAGE" > /dev/null
HEALTHY=0
for i in $(seq 1 60); do
    if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
        HEALTHY=1
        break
    fi
    sleep 1
done
if [ $HEALTHY -eq 1 ]; then
    echo "PASSED: /api/health returned 200"
else
    "$CLI" logs "$CONTAINER" 2>&1 | tail -30
    echo "FAILED: /api/health did not return 200 within 60s"
    exit 1
fi
if curl -sf "http://127.0.0.1:$PORT/" | grep -q '<div id="root">'; then
    echo "PASSED: / serves the frontend"
else
    echo "FAILED: / did not serve the frontend"
    exit 1
fi
if "$CLI" logs "$CONTAINER" 2>&1 | grep -q 'MCP connected: calculator'; then
    echo "PASSED: bundled stdio MCP server (calculator) connected"
else
    echo "FAILED: calculator MCP server did not connect"
    exit 1
fi
cleanup

echo ""
echo "5. Run backend unit tests"
echo "-------------------------"
cd "$PROJECT_ROOT"
bash ./test/run_tests.sh backend > /dev/null 2>&1 || bash ./test/run_tests.sh backend
echo "PASSED: Backend tests passed"

echo ""
echo "=========================================="
echo "All PR #987 validation checks PASSED"
echo "=========================================="
