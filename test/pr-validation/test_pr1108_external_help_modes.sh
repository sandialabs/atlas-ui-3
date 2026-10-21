#!/usr/bin/env bash
# Test script for PR #1108: externally hosted Help documentation.
#
# What this validates, against a running server and a mock documentation
# host (not against the unit tests alone):
# - The default 'local' mode still serves the local help.md.
# - 'remote_markdown' fetches the document server-side, renders it in the
#   Help page, resolves relative images against the document URL, and serves
#   them through the Atlas image proxy.
# - A second request reuses the cached document instead of refetching it.
# - 'external_link' returns the URL and fetches nothing at all.
# - A remote mode without a usable URL is reported as a configuration error
#   and falls back to the local document instead of leaving a broken button.
# - The image proxy refuses a host that is not the configured one.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURE_DIR="$SCRIPT_DIR/fixtures/pr1108"

PASSED=0
FAILED=0

print_header() { echo ""; echo "=========================================="; echo "$1"; echo "=========================================="; }
print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "  \033[0;32mPASSED\033[0m: $2"; PASSED=$((PASSED + 1))
    else
        echo -e "  \033[0;31mFAILED\033[0m: $2"; FAILED=$((FAILED + 1))
    fi
}

echo "=== PR #1108 Validation: externally hosted Help documentation ==="

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"
# The settings validator refuses a placeholder capability secret, and a
# developer .env may still hold one. These checks never use it, so give them
# a valid one rather than failing on the environment.
export CAPABILITY_TOKEN_SECRET="${CAPABILITY_TOKEN_SECRET:-$(python3 -c 'import secrets; print(secrets.token_hex(32))')}"
export MCP_TOKEN_ENCRYPTION_KEY="${MCP_TOKEN_ENCRYPTION_KEY:-$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')}"
# The suite expects a deployment with no statically configured groups. A
# developer .env often carries them (and the parent .env is loaded
# automatically), which would leak into the authorization tests.
export ADMIN_USERS="${ADMIN_USERS:-}"
export AUTH_STATIC_GROUPS="${AUTH_STATIC_GROUPS:-}"
# The suite expects the default feature flags. A developer .env often pins
# FEATURE_AGENT_MODE_AVAILABLE=false, which turns the agent-mode tests into
# ordinary chat turns and fails them.
export FEATURE_AGENT_MODE_AVAILABLE="${FEATURE_AGENT_MODE_AVAILABLE:-true}"
# The local .env may pin ENVIRONMENT=production, which refuses DEBUG_MODE.
export ENVIRONMENT="${ENVIRONMENT:-development}"
cd "$PROJECT_ROOT"

DOCS_PID=""
APP_PID=""

cleanup() {
    [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null
    [ -n "$DOCS_PID" ] && kill "$DOCS_PID" 2>/dev/null
    wait 2>/dev/null
}
trap cleanup EXIT

free_port() {
    python3 - <<'PY'
import socket
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    print(s.getsockname()[1])
PY
}

# The mock documentation host: it counts the requests it served, so the
# caching and the "fetches nothing" checks can be made against a real host.
DOCS_PORT="$(free_port)"
DOCS_SCRIPT="$(mktemp)"
DOCS_COUNT="$(mktemp)"
cat > "$DOCS_SCRIPT" <<'PY'
import http.server, socketserver, sys, threading

PORT = int(sys.argv[1])
ROOT = sys.argv[2]
COUNT_FILE = sys.argv[3]

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def do_GET(self):
        with open(COUNT_FILE, "a", encoding="utf-8") as handle:
            handle.write(self.path + "\n")
        super().do_GET()

    def log_message(self, *args):
        pass

socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", PORT), Handler) as httpd:
    httpd.serve_forever()
PY
python3 "$DOCS_SCRIPT" "$DOCS_PORT" "$FIXTURE_DIR/docs" "$DOCS_COUNT" &
DOCS_PID=$!
sleep 1

DOCS_REQUESTS() {
    if [ -s "$DOCS_COUNT" ]; then wc -l < "$DOCS_COUNT" | tr -d ' '; else echo 0; fi
}

APP_PORT="$(free_port)"
APP_LOG="$(mktemp)"

# start_app <display mode> <content url>
start_app() {
    local mode="$1"
    local url="$2"
    DEBUG_MODE=true \
    ATLAS_HOST=127.0.0.1 \
    PORT="$APP_PORT" \
    HELP_DISPLAY_MODE="$mode" \
    HELP_CONTENT_URL="$url" \
    python3 -m atlas.main > "$APP_LOG" 2>&1 &
    APP_PID=$!
    for _ in $(seq 1 60); do
        if curl -fsS "http://127.0.0.1:$APP_PORT/api/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    echo "  the server did not come up; log follows:"
    tail -20 "$APP_LOG"
    return 1
}

stop_app() {
    [ -n "$APP_PID" ] && kill "$APP_PID" 2>/dev/null
    wait "$APP_PID" 2>/dev/null
    APP_PID=""
}

help_json() {
    curl -fsS -H "X-User-Email: pr1108@example.org" "http://127.0.0.1:$APP_PORT/api/help"
}

json_field() {
    python3 -c "import json,sys; print(json.load(sys.stdin).get('$1', ''))"
}

# ==========================================
print_header "Check 1: the default (local) mode still serves the local document"
# ==========================================
if start_app "local" ""; then
    BODY="$(help_json)"
    MODE="$(printf '%s' "$BODY" | json_field mode)"
    CONTENT="$(printf '%s' "$BODY" | json_field content)"
    if [ "$MODE" = "local" ] && [ -n "$CONTENT" ]; then
        print_result 0 "the local mode serves the local document"
    else
        print_result 1 "the local mode serves the local document (mode='$MODE')"
    fi

    SHELL_BODY="$(curl -fsS -H 'X-User-Email: pr1108@example.org' "http://127.0.0.1:$APP_PORT/api/config/shell")"
    SHELL_MODE="$(printf '%s' "$SHELL_BODY" | json_field help)"
    if printf '%s' "$SHELL_BODY" | python3 -c "import json,sys; sys.exit(0 if json.load(sys.stdin)['help']['mode']=='local' else 1)"; then
        print_result 0 "the shell config reports the local mode"
    else
        print_result 1 "the shell config reports the local mode (help='$SHELL_MODE')"
    fi
    stop_app
else
    print_result 1 "the local mode serves the local document (the server did not start)"
fi

# ==========================================
print_header "Check 2: remote_markdown fetches the document and resolves its images"
# ==========================================
BEFORE_DOCS="$(DOCS_REQUESTS)"
if start_app "remote_markdown" "http://127.0.0.1:$DOCS_PORT/help.md"; then
    BODY="$(help_json)"
    MODE="$(printf '%s' "$BODY" | json_field mode)"
    CONTENT="$(printf '%s' "$BODY" | json_field content)"
    if [ "$MODE" = "remote_markdown" ] && printf '%s' "$CONTENT" | grep -q "PR1108_HOSTED_DOCUMENT_MARKER"; then
        print_result 0 "the hosted document is fetched and returned"
    else
        print_result 1 "the hosted document is fetched and returned (mode='$MODE')"
    fi

    # The relative image reference must point at the documentation host
    # through the Atlas image proxy, not at an ATLAS-local path. The proxy
    # is given the identifier of the image, never a URL.
    EXPECTED_PROXY="/api/help/image?h="
    if printf '%s' "$CONTENT" | grep -q "$EXPECTED_PROXY" && ! printf '%s' "$CONTENT" | grep -q "image?url="; then
        print_result 0 "the relative image reference is resolved against the document"
    else
        print_result 1 "the relative image reference is resolved against the document"
    fi

    if printf '%s' "$CONTENT" | grep -q "/help-images/logo.png"; then
        print_result 1 "a relative image is not treated as an ATLAS-local path"
    else
        print_result 0 "a relative image is not treated as an ATLAS-local path"
    fi

    # The proxy really serves the image.
    PROXY_URL="$(printf '%s' "$CONTENT" | grep -o '/api/help/image?h=[^)]*' | head -1)"
    if [ -n "$PROXY_URL" ]; then
        IMAGE_FILE="$(mktemp)"
        STATUS="$(curl -s -o "$IMAGE_FILE" -w '%{http_code}' -H 'X-User-Email: pr1108@example.org' "http://127.0.0.1:$APP_PORT$PROXY_URL")"
        if [ "$STATUS" = "200" ] && [ -s "$IMAGE_FILE" ]; then
            print_result 0 "the image proxy serves the image bytes"
        else
            print_result 1 "the image proxy serves the image bytes (HTTP $STATUS)"
        fi
        rm -f "$IMAGE_FILE"
    else
        print_result 1 "the image proxy serves the image bytes (no proxy URL in the document)"
    fi

    # The proxy serves the images the document references and nothing else,
    # so an identifier of an image it does not know is refused.
    FOREIGN_STATUS="$(curl -s -o /dev/null -w '%{http_code}' -H 'X-User-Email: pr1108@example.org' \
        "http://127.0.0.1:$APP_PORT/api/help/image?h=00000000000000000000000000000000")"
    if [ "$FOREIGN_STATUS" = "404" ]; then
        print_result 0 "the image proxy refuses an image the document does not reference"
    else
        print_result 1 "the image proxy refuses an image the document does not reference (HTTP $FOREIGN_STATUS)"
    fi

    # A second request reuses the cached document.
    DOCS_AFTER_FIRST="$(DOCS_REQUESTS)"
    help_json > /dev/null
    DOCS_AFTER_SECOND="$(DOCS_REQUESTS)"
    if [ "$DOCS_AFTER_SECOND" = "$DOCS_AFTER_FIRST" ]; then
        print_result 0 "the cached document is reused instead of refetched"
    else
        print_result 1 "the cached document is reused instead of refetched ($DOCS_AFTER_FIRST -> $DOCS_AFTER_SECOND)"
    fi
    stop_app
else
    print_result 1 "the hosted document is fetched and returned (the server did not start)"
fi

# ==========================================
print_header "Check 3: external_link returns the URL and fetches nothing"
# ==========================================
DOCS_BEFORE_LINK="$(DOCS_REQUESTS)"
if start_app "external_link" "http://127.0.0.1:$DOCS_PORT/help.md"; then
    BODY="$(help_json)"
    MODE="$(printf '%s' "$BODY" | json_field mode)"
    LINK="$(printf '%s' "$BODY" | json_field link_url)"
    CONTENT="$(printf '%s' "$BODY" | json_field content)"
    if [ "$MODE" = "external_link" ] && [ "$LINK" = "http://127.0.0.1:$DOCS_PORT/help.md" ] && [ -z "$CONTENT" ]; then
        print_result 0 "the external-link mode returns the URL and no content"
    else
        print_result 1 "the external-link mode returns the URL and no content (mode='$MODE' link='$LINK')"
    fi

    DOCS_AFTER_LINK="$(DOCS_REQUESTS)"
    if [ "$DOCS_AFTER_LINK" = "$DOCS_BEFORE_LINK" ]; then
        print_result 0 "the external-link mode fetches nothing from the documentation host"
    else
        print_result 1 "the external-link mode fetches nothing from the documentation host ($DOCS_BEFORE_LINK -> $DOCS_AFTER_LINK)"
    fi
    stop_app
else
    print_result 1 "the external-link mode returns the URL and no content (the server did not start)"
fi

# ==========================================
print_header "Check 4: a missing URL is a reported configuration error"
# ==========================================
if start_app "remote_markdown" ""; then
    BODY="$(help_json)"
    MODE="$(printf '%s' "$BODY" | json_field mode)"
    ERROR="$(printf '%s' "$BODY" | json_field error)"
    CONTENT="$(printf '%s' "$BODY" | json_field content)"
    if [ "$MODE" = "local" ] && [ -n "$CONTENT" ]; then
        print_result 0 "the misconfiguration falls back to the local document"
    else
        print_result 1 "the misconfiguration falls back to the local document (mode='$MODE')"
    fi

    # The message that names the settings is for the operator; everybody else
    # gets a neutral notice.
    ADMIN_BODY="$(curl -fsS -H "X-User-Email: admin@example.com" "http://127.0.0.1:$APP_PORT/api/help")"
    ADMIN_ERROR="$(printf '%s' "$ADMIN_BODY" | json_field error)"
    if printf '%s' "$ADMIN_ERROR" | grep -q "HELP_CONTENT_URL"; then
        print_result 0 "the administrator is told which setting is wrong"
    else
        print_result 1 "the administrator is told which setting is wrong (error='$ADMIN_ERROR')"
    fi

    READER_BODY="$(curl -fsS -H "X-User-Email: pr1108-reader@example.org" "http://127.0.0.1:$APP_PORT/api/help")"
    READER_ERROR="$(printf '%s' "$READER_BODY" | json_field error)"
    if ! printf '%s' "$READER_ERROR" | grep -q "HELP_CONTENT_URL" && printf '%s' "$READER_ERROR" | grep -q "administrator"; then
        print_result 0 "a reader is given a neutral notice"
    else
        print_result 1 "a reader is given a neutral notice (error='$READER_ERROR')"
    fi

    ADMIN_BODY="$(curl -fsS -H 'X-User-Email: pr1108@example.org' "http://127.0.0.1:$APP_PORT/admin/help-config" 2>/dev/null || true)"
    if [ -z "$ADMIN_BODY" ]; then
        print_result 0 "the admin endpoint is admin-gated (the request was refused)"
    else
        print_result 1 "the admin endpoint is admin-gated (the request was not refused)"
    fi
    stop_app
else
    print_result 1 "the misconfiguration is reported and the local document is used (the server did not start)"
fi

# ==========================================
print_header "Check 5: the frontend opens the external documentation in a new tab"
# ==========================================
FE_OUT="$(cd frontend && npx vitest run src/test/help-display-mode.test.jsx 2>&1)"
FE_RC=$?
echo "$FE_OUT" | grep -E 'Test Files|Tests ' | tail -2
print_result $FE_RC "frontend unit tests for the Help display modes"

# ==========================================
print_header "Check 6: backend unit tests"
# ==========================================
./test/run_tests.sh backend >/dev/null 2>&1
print_result $? "backend test suite passes"

# ==========================================
print_header "Summary"
# ==========================================
echo "  Passed: $PASSED"
echo "  Failed: $FAILED"
if [ "$FAILED" -gt 0 ]; then
    echo "RESULT: FAILED"; exit 1
fi
echo "RESULT: PASSED"; exit 0
