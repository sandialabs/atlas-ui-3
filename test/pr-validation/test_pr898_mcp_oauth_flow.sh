#!/bin/bash
# Validation script: MCP OAuth 2.1 authorization flow (issue #898)
#
# Drives the real Atlas app against a real (minimal) OAuth-protected MCP
# deployment on localhost: an MCP endpoint that answers 401 with an RFC 9728
# resource_metadata challenge, its protected-resource document, and an
# authorization server with RFC 7591 dynamic registration plus authorize,
# token and revoke endpoints that genuinely verify PKCE and the redirect URI.
#
# Exercises discovery from the challenge, dynamic client registration, the
# authorization redirect, CSRF state rejection, the callback and code
# exchange, encrypted per-user token storage, per-user isolation, silent
# refresh, and disconnect with revocation.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "Validation: MCP OAuth 2.1 authorization flow (#898)"
echo "=========================================="

cd "$PROJECT_ROOT"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "FAILED: Virtual environment not found at $PROJECT_ROOT/.venv"
    exit 1
fi

export PYTHONPATH="$PROJECT_ROOT"

python "$SCRIPT_DIR/fixtures/pr898/mcp_oauth_end_to_end.py"
