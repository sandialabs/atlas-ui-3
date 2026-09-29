#!/bin/bash
# PR #987 Validation Script: Slim down Dockerfile.runtimeonly (issue #986)
# Builds the runtime-only image and checks that the final image has a shell and
# env but no package manager or compiler, that /app is owned by nonroot, that
# the example hooks work when run by script path, and that the container serves
# /api/health and the UI.

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
if (cd atlas && python -m pytest -q tests/test_docker_env_sync.py -k runtime_only > /dev/null 2>&1); then
    echo "PASSED: static Dockerfile checks (test_docker_env_sync.py)"
else
    (cd atlas && python -m pytest -q tests/test_docker_env_sync.py -k runtime_only)
    echo "FAILED: static Dockerfile checks"
    exit 1
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
CHECK="$("$CLI" run --rm "$IMAGE" python -c 'import os, shutil, sys; print(os.getuid(), os.stat("/app/.venv").st_uid, sys.prefix, *[bool(shutil.which(b)) for b in ("sh", "bash", "env", "apk", "gcc")])' 2> /tmp/pr987-run.err)" || {
    echo "FAILED: could not run the image: $(cat /tmp/pr987-run.err)"
    exit 1
}
if [ "$CHECK" = "65532 65532 /app/.venv True True True False False" ]; then
    echo "PASSED: runs as 65532, /app/.venv owned by 65532, venv on PATH, sh/bash/env present, no apk or gcc"
else
    echo "FAILED: unexpected uid/owner/tools (uid owner prefix sh bash env apk gcc): $CHECK"
    exit 1
fi
CA="$("$CLI" run --rm "$IMAGE" python -c 'import os, ssl; p = ssl.get_default_verify_paths(); print(os.path.exists(p.cafile or p.openssl_cafile))' 2> /tmp/pr987-run.err)" || {
    echo "FAILED: could not run the image: $(cat /tmp/pr987-run.err)"
    exit 1
}
if [ "$CA" = "True" ]; then
    echo "PASSED: CA certificate bundle present"
else
    echo "FAILED: CA certificate bundle missing"
    exit 1
fi

# Hooks are spawned without a shell, so a script-path hook needs its #!
# interpreter. Run the shipped examples by path with real event envelopes.
HOOKS=/app/atlas/config/hooks-example
envelope() {  # envelope <event> <tool_name> <path>
    printf '{"event": "%s", "payload": {"tool_name": "%s", "tool_args": {"path": "%s"}}}' "$1" "$2" "$3"
}
set +e
envelope PreToolUse filesystem__write_file /etc/passwd | "$CLI" run --rm -i --entrypoint "$HOOKS/block_destructive.sh" "$IMAGE" > /dev/null 2> /tmp/pr987-hook.err
RC=$?
envelope PreToolUse filesystem__write_file /tmp/x | "$CLI" run --rm -i --entrypoint "$HOOKS/block_destructive.sh" "$IMAGE" > /dev/null 2>&1
RC_ALLOW=$?
APPROVAL="$(envelope PreToolUse http_get /tmp/x | "$CLI" run --rm -i --entrypoint "$HOOKS/require_approval_network.py" "$IMAGE" 2>&1)"
AUDIT="$(envelope PostToolUse filesystem__write_file /tmp/x | "$CLI" run --rm -i -e ATLAS_PROJECT_DIR=/tmp --entrypoint bash "$IMAGE" -c "$HOOKS/audit_tool.sh && cat /tmp/logs/tool-audit.jsonl" 2>&1)"
set -e
if [ $RC -eq 2 ] && grep -q "blocked by policy" /tmp/pr987-hook.err && [ $RC_ALLOW -eq 0 ]; then
    echo "PASSED: block_destructive.sh denies /etc/passwd (exit 2) and allows /tmp/x"
else
    echo "FAILED: block_destructive.sh exit $RC for /etc/passwd, $RC_ALLOW for /tmp/x: $(cat /tmp/pr987-hook.err)"
    exit 1
fi
if echo "$APPROVAL" | grep -q '"decision": "require_approval"'; then
    echo "PASSED: require_approval_network.py requires approval for http_get"
else
    echo "FAILED: require_approval_network.py returned: $APPROVAL"
    exit 1
fi
if echo "$AUDIT" | grep -q '"tool_name": "filesystem__write_file"'; then
    echo "PASSED: audit_tool.sh wrote an audit record"
else
    echo "FAILED: audit_tool.sh output: $AUDIT"
    exit 1
fi

echo ""
echo "4. Start the container and exercise it"
echo "--------------------------------------"
KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
"$CLI" run -d --name "$CONTAINER" -p "127.0.0.1:$PORT:8000" \
    -e MCP_TOKEN_ENCRYPTION_KEY="$KEY" -e DEBUG_MODE=true "$IMAGE" > /dev/null || {
    echo "FAILED: could not start the container"
    exit 1
}
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
if bash ./test/run_tests.sh backend > /tmp/pr987-backend-tests.log 2>&1; then
    echo "PASSED: Backend tests passed"
else
    tail -30 /tmp/pr987-backend-tests.log
    echo "FAILED: Backend tests failed (full log in /tmp/pr987-backend-tests.log)"
    exit 1
fi

echo ""
echo "=========================================="
echo "All PR #987 validation checks PASSED"
echo "=========================================="
