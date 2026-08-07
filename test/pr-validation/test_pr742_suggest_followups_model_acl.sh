#!/bin/bash
# Test script for PR #742: Enforce per-model group restrictions on /api/suggest_followups
#
# Test plan (live server, real HTTP calls):
# - A non-member requesting a group-restricted model gets 404 -- and the SAME
#   status as requesting a nonexistent model, so restricted model names cannot
#   be enumerated (no 403-vs-404 oracle).
# - A group member requesting the restricted model gets 200 (call reaches the
#   mock LLM gateway).
# - Any user requesting an unrestricted model gets 200.
# - Run the model-access-control unit suite.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SCRATCH_DIR="/tmp/pr742_test_$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0
MOCK_PID=""
BACKEND_PID=""

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

cleanup() {
    [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null
    [ -n "$MOCK_PID" ] && kill "$MOCK_PID" 2>/dev/null
    rm -rf "$SCRATCH_DIR"
}
trap cleanup EXIT

cd "$PROJECT_ROOT"
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

MOCK_PORT="${MOCK_LLM_PORT:-8197}"
BACKEND_PORT=8198
BASE_URL="http://127.0.0.1:${BACKEND_PORT}"
MOCK_URL="http://127.0.0.1:${MOCK_PORT}"

if curl -s "$MOCK_URL/health" >/dev/null 2>&1; then
    echo -e "${RED}FAILED${NC}: Port $MOCK_PORT already in use (set MOCK_LLM_PORT to a free port)"
    exit 1
fi

# ==========================================
# Setup: config dir with a restricted and an open model, both -> mock LLM
# ==========================================
mkdir -p "$SCRATCH_DIR/config"
cat > "$SCRATCH_DIR/config/llmconfig.yml" <<EOF
models:
  admin-only-model:
    model_url: "http://127.0.0.1:${MOCK_PORT}/v1"
    model_name: "openai/mock-restricted"
    api_key: "mock-key"
    groups:
      - admin
  open-model:
    model_url: "http://127.0.0.1:${MOCK_PORT}/v1"
    model_name: "openai/mock-open"
    api_key: "mock-key"
EOF

# ==========================================
# Start mock LLM and backend
# ==========================================
print_header "Setup: start mock LLM gateway and backend"

MOCK_LLM_PORT="$MOCK_PORT" python "$PROJECT_ROOT/mocks/llm-mock/main.py" \
    > "$SCRATCH_DIR/mock.log" 2>&1 &
MOCK_PID=$!
for _ in $(seq 1 30); do
    curl -sf "$MOCK_URL/health" >/dev/null 2>&1 && break
    sleep 0.5
done
curl -sf "$MOCK_URL/health" >/dev/null 2>&1
print_result $? "Mock LLM gateway is up on port $MOCK_PORT"

if curl -sf "$BASE_URL/api/health" >/dev/null 2>&1; then
    echo -e "${RED}FAILED${NC}: Port $BACKEND_PORT already in use"
    exit 1
fi

cd "$PROJECT_ROOT/atlas"
DEBUG_MODE=true \
    FEATURE_FOLLOWUP_SUGGESTIONS_ENABLED=true \
    APP_CONFIG_DIR="$SCRATCH_DIR/config" \
    PORT=$BACKEND_PORT ATLAS_HOST=127.0.0.1 \
    python main.py > "$SCRATCH_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
cd "$PROJECT_ROOT"

for i in $(seq 1 30); do
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo -e "${RED}FAILED${NC}: Backend exited early; log tail:"
        tail -20 "$SCRATCH_DIR/backend.log"
        exit 1
    fi
    curl -sf "$BASE_URL/api/health" >/dev/null 2>&1 && break
    sleep 1
done
curl -sf "$BASE_URL/api/health" >/dev/null 2>&1
print_result $? "Backend is up on port $BACKEND_PORT"

suggest_status() {
    # $1 = user email, $2 = model name -> echoes HTTP status code
    curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$BASE_URL/api/suggest_followups" \
        -H "X-User-Email: $1" -H "Content-Type: application/json" \
        -d "{\"messages\": [{\"role\": \"user\", \"content\": \"hi\"}], \"model\": \"$2\"}"
}

# ==========================================
# Check 1: non-member is denied with 404, identically to a nonexistent model
# ==========================================
print_header "Check 1: restricted model is indistinguishable from nonexistent"

RESTRICTED_STATUS=$(suggest_status "user@example.com" "admin-only-model")
MISSING_STATUS=$(suggest_status "user@example.com" "no-such-model")
echo "  restricted -> $RESTRICTED_STATUS, nonexistent -> $MISSING_STATUS"
[ "$RESTRICTED_STATUS" = "404" ]
print_result $? "Non-member gets 404 for restricted model"
[ "$MISSING_STATUS" = "404" ] && [ "$RESTRICTED_STATUS" = "$MISSING_STATUS" ]
print_result $? "Nonexistent model gets the same 404 (no enumeration oracle)"

# ==========================================
# Check 2: group member reaches the model
# ==========================================
print_header "Check 2: group member is allowed"

# In DEBUG_MODE the mock group backend puts test@test.com in "admin".
curl -s -X POST "$MOCK_URL/test/reset-log" >/dev/null
MEMBER_STATUS=$(suggest_status "test@test.com" "admin-only-model")
echo "  member -> $MEMBER_STATUS"
[ "$MEMBER_STATUS" = "200" ]
print_result $? "Member gets 200 for restricted model"

# The suggestions endpoint swallows LLM errors and still returns 200, so prove
# the call actually reached the gateway rather than dying en route.
curl -s "$MOCK_URL/test/last-request" | grep -q "mock-restricted"
print_result $? "Member's request reached the mock LLM gateway"

# ==========================================
# Check 3: unrestricted model stays open to everyone
# ==========================================
print_header "Check 3: unrestricted model unaffected"

OPEN_STATUS=$(suggest_status "user@example.com" "open-model")
echo "  open model -> $OPEN_STATUS"
[ "$OPEN_STATUS" = "200" ]
print_result $? "Non-member gets 200 for model without groups"

# ==========================================
# Check 4: unit suite
# ==========================================
print_header "Check 4: model access-control unit suite"

python3 -m pytest atlas/tests/test_model_access_control.py -q
print_result $? "atlas/tests/test_model_access_control.py"

# Summary
echo ""
echo "=========================================="
echo -e "Passed: ${GREEN}$PASSED${NC} | Failed: ${RED}$FAILED${NC}"
echo "=========================================="
[ $FAILED -eq 0 ] && exit 0 || exit 1
