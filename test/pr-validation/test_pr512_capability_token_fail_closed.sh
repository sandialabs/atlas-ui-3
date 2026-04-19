#!/bin/bash
# Test script for PR #512: Remove hardcoded capability-token dev secret (fail-closed).
#
# Test plan:
# - With the unset fixture (CAPABILITY_TOKEN_SECRET=""), a forged token
#   signed with the legacy b"dev-capability-secret" constant is rejected.
# - With the unset fixture, mint/verify still roundtrip inside one process
#   via the ephemeral per-process secret.
# - With the unset fixture, _get_secret() returns a 32-byte stable random
#   value that is NOT the legacy constant.
# - With the configured fixture, the configured CAPABILITY_TOKEN_SECRET is
#   preferred over the ephemeral secret.
# - End-to-end HTTP: /api/files/download/<key>?token=<forged> returns 403
#   even when the legacy dev secret was used to sign the forgery.
# - Backend capability-token security test suite passes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr512"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

print_header "PR #512: Capability-token fail-closed validation"

# Activate virtual environment
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PYTHONPATH="$PROJECT_ROOT"

run_with_fixture() {
    local fixture="$1"
    local script="$2"
    (
        set -a
        # shellcheck disable=SC1090
        source "$fixture"
        set +a
        python3 -c "$script"
    )
}

# ==========================================
# 1. Legacy forged token is rejected (unset fixture)
# ==========================================
print_header "1. Legacy forged token rejected (CAPABILITY_TOKEN_SECRET unset)"

RESULT=$(run_with_fixture "$FIXTURES_DIR/.env.unset" '
import hmac, logging
logging.disable(logging.CRITICAL)
from hashlib import sha256
from atlas.core.capabilities import (
    _b64url_encode,
    _reset_ephemeral_secret_for_tests,
    verify_file_token,
)

_reset_ephemeral_secret_for_tests()

payload = b"{\"u\":\"victim@example.com\",\"k\":\"victim-file\",\"e\":9999999999}"
body = _b64url_encode(payload)
sig = hmac.new(b"dev-capability-secret", body.encode("ascii"), sha256).digest()
token = f"{body}.{_b64url_encode(sig)}"
print("FORGED_ACCEPTED" if verify_file_token(token) is not None else "REJECTED")
' 2>&1 | tail -1)

[ "$RESULT" = "REJECTED" ]
print_result $? "Forged token with legacy dev secret rejected (got: $RESULT)"

# ==========================================
# 2. Ephemeral secret mint/verify roundtrips (unset fixture)
# ==========================================
print_header "2. Ephemeral secret mint/verify roundtrip"

RESULT=$(run_with_fixture "$FIXTURES_DIR/.env.unset" '
import logging
logging.disable(logging.CRITICAL)
from atlas.core.capabilities import (
    _reset_ephemeral_secret_for_tests,
    generate_file_token,
    verify_file_token,
)

_reset_ephemeral_secret_for_tests()
token = generate_file_token("alice@example.com", "pr512-key", ttl_seconds=60)
claims = verify_file_token(token)
if claims and claims["u"] == "alice@example.com" and claims["k"] == "pr512-key":
    print("OK")
else:
    print("BAD")
' 2>&1 | tail -1)

[ "$RESULT" = "OK" ]
print_result $? "Ephemeral mint/verify roundtrip (got: $RESULT)"

# ==========================================
# 3. Ephemeral secret properties (unset fixture)
# ==========================================
print_header "3. Ephemeral secret is 32 random bytes, stable, not legacy"

RESULT=$(run_with_fixture "$FIXTURES_DIR/.env.unset" '
import logging
logging.disable(logging.CRITICAL)
from atlas.core.capabilities import _get_secret, _reset_ephemeral_secret_for_tests

_reset_ephemeral_secret_for_tests()
first = _get_secret()
second = _get_secret()
legacy = b"dev-capability-secret"
if len(first) == 32 and first == second and first != legacy:
    print("OK")
else:
    print("BAD len=" + str(len(first)) + " stable=" + str(first == second) + " legacy=" + str(first == legacy))
' 2>&1 | tail -1)

[ "$RESULT" = "OK" ]
print_result $? "Ephemeral secret is 32 random bytes, stable, not legacy (got: $RESULT)"

# ==========================================
# 4. Configured CAPABILITY_TOKEN_SECRET wins (configured fixture)
# ==========================================
print_header "4. Configured CAPABILITY_TOKEN_SECRET preferred over ephemeral"

RESULT=$(run_with_fixture "$FIXTURES_DIR/.env.configured" '
import logging
logging.disable(logging.CRITICAL)
from atlas.core.capabilities import _get_secret, _reset_ephemeral_secret_for_tests

_reset_ephemeral_secret_for_tests()
secret = _get_secret()
if secret == b"pr512-configured-strong-secret-0123456789abcdef":
    print("OK")
else:
    print(f"BAD secret={secret!r}")
' 2>&1 | tail -1)

[ "$RESULT" = "OK" ]
print_result $? "Configured secret wins (got: $RESULT)"

# ==========================================
# 5. End-to-end: forged token download attempt returns 403
# ==========================================
print_header "5. End-to-end forged download attempt returns 403"

RESULT=$(ATLAS_DIR="$ATLAS_DIR" run_with_fixture "$FIXTURES_DIR/.env.unset" '
import base64, hmac, logging, os, sys, types
logging.disable(logging.CRITICAL)

# atlas/main.py needs to be importable as "main".
sys.path.insert(0, os.environ["ATLAS_DIR"])

# Stub LiteLLM to avoid external deps (mirrors test_capability_tokens_and_injection.py).
fake = types.ModuleType("atlas.modules.llm.litellm_caller")

class _FakeLLM:
    def __init__(self, *args, **kwargs):
        pass
    async def call_plain(self, *args, **kwargs):
        return "ok"

fake.LiteLLMCaller = _FakeLLM
sys.modules["atlas.modules.llm.litellm_caller"] = fake

from hashlib import sha256
from main import app
from starlette.testclient import TestClient

from atlas.core.capabilities import _b64url_encode, _reset_ephemeral_secret_for_tests
from atlas.infrastructure import app_factory as af

_reset_ephemeral_secret_for_tests()

class _FakeS3:
    endpoint_url = "mock://s3"
    bucket_name = "test-bucket"
    async def get_file(self, user, key):
        return {
            "key": key,
            "filename": "leak.txt",
            "content_base64": base64.b64encode(b"secret").decode(),
            "content_type": "text/plain",
            "size": 6,
            "last_modified": "",
            "etag": "",
            "tags": {},
            "user_email": user,
        }

original = af.get_file_storage
af.get_file_storage = lambda: _FakeS3()
try:
    payload = b"{\"u\":\"victim@example.com\",\"k\":\"victim-file\",\"e\":9999999999}"
    body = _b64url_encode(payload)
    sig = hmac.new(b"dev-capability-secret", body.encode("ascii"), sha256).digest()
    forged = f"{body}.{_b64url_encode(sig)}"

    client = TestClient(app)
    resp = client.get(
        "/api/files/download/victim-file",
        params={"token": forged},
        headers={"X-User-Email": "attacker@example.com"},
    )
    print(f"STATUS={resp.status_code}")
finally:
    af.get_file_storage = original
' 2>&1 | tail -1)

# The forged token must be rejected with 403 by the download route's
# verify_file_token check. 401 would also indicate rejection upstream.
# 200 would mean the forgery succeeded (the vulnerability still exists).
[[ "$RESULT" == "STATUS=403" || "$RESULT" == "STATUS=401" ]]
print_result $? "Forged download rejected with 401/403 (got: $RESULT)"

# ==========================================
# 6. Backend capability-token security suite
# ==========================================
print_header "6. Backend capability-token security suite"

# Run pytest in a clean environment so the fixture sources above cannot leak
# DEBUG_MODE=false or empty CAPABILITY_TOKEN_SECRET into the suite.
cd "$ATLAS_DIR"
env -u DEBUG_MODE -u CAPABILITY_TOKEN_SECRET \
    PYTHONPATH="$PROJECT_ROOT" \
    python3 -m pytest tests/test_security_capability_tokens.py -x -q 2>&1
print_result $? "test_security_capability_tokens.py passes"

# ==========================================
# Summary
# ==========================================
print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC}"
echo -e "Failed: ${RED}$FAILED${NC}"
echo ""

if [ $FAILED -gt 0 ]; then
    echo -e "${RED}SOME TESTS FAILED${NC}"
    exit 1
else
    echo -e "${GREEN}ALL TESTS PASSED${NC}"
    exit 0
fi
