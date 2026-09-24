#!/bin/bash
# Test script for PR #976: Enterprise LiteLLM team gateways
#
# Test plan:
# - Start the mock LiteLLM proxy (mocks/litellm-mock) and its smoke test
# - Start the real backend with a fixture llmconfig.yml that defines a
#   team-scoped gateway pointing at the mock
# - /api/config/shell lists the gateway
# - GET /api/llm/gateways/enterprise/teams returns only the user's teams
# - GET /api/llm/gateways/enterprise/models?team_id=... returns selectable
#   <gateway>::<team>::<model> keys; a team the user is not in is 403
# - atlas-chat through a team model reaches the mock with x-litellm-team-id set
#   to the selected team, for two different users/teams
# - atlas-chat with a hand-crafted key for someone else's team fails and no
#   request for that team reaches the mock
# - Run the backend unit test suite

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr976"

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

cd "$PROJECT_ROOT"
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"

WORK_DIR=$(mktemp -d -t atlas-pr976-XXXXXX)
CONFIG_DIR="$WORK_DIR/config"
LOG_DIR="$WORK_DIR/logs"
mkdir -p "$CONFIG_DIR" "$LOG_DIR"
cp "$FIXTURES_DIR/llmconfig.yml" "$CONFIG_DIR/llmconfig.yml"
echo '{}' > "$CONFIG_DIR/mcp.json"
MOCK_PID=""
BACKEND_PID=""
trap 'kill $BACKEND_PID $MOCK_PID 2>/dev/null || true; rm -rf "$WORK_DIR"' EXIT

MOCK_PORT=4176
PORT=8176
for p in $MOCK_PORT $PORT; do
    if curl -s "http://127.0.0.1:$p/" > /dev/null 2>&1; then
        echo "FAILED: Port $p is already in use by another process"
        exit 1
    fi
done
MOCK_URL="http://127.0.0.1:$MOCK_PORT"
export PR976_LITELLM_URL="$MOCK_URL"

# --- Mock LiteLLM proxy ---------------------------------------------------
print_header "PR #976: mock LiteLLM proxy"
MOCK_LITELLM_PORT=$MOCK_PORT python mocks/litellm-mock/main.py > "$WORK_DIR/mock.log" 2>&1 &
MOCK_PID=$!
for i in $(seq 1 20); do
    curl -sf "$MOCK_URL/health" > /dev/null 2>&1 && break
    sleep 0.5
done
MOCK_LITELLM_URL="$MOCK_URL" python mocks/litellm-mock/smoke_test.py
print_result $? "Mock proxy enforces team header and team models (smoke test)"
curl -s -X DELETE "$MOCK_URL/mock/requests" > /dev/null

# --- Backend --------------------------------------------------------------
print_header "PR #976: backend with a team gateway"
export PORT=$PORT
export ATLAS_HOST="127.0.0.1"
export APP_CONFIG_DIR="$CONFIG_DIR"
export APP_LOG_DIR="$LOG_DIR"
export USE_MOCK_S3=true
export DEBUG_MODE=true
export SKIP_AUTHORIZATION_CHECKS=true
export FEATURE_CHAT_HISTORY_ENABLED=false

cd "$ATLAS_DIR"
python main.py > "$WORK_DIR/backend.log" 2>&1 &
BACKEND_PID=$!
cd "$PROJECT_ROOT"
READY=0
for i in $(seq 1 60); do
    if ! kill -0 $BACKEND_PID 2>/dev/null; then
        echo "FAILED: Backend exited unexpectedly; log tail:"
        tail -30 "$WORK_DIR/backend.log"
        exit 1
    fi
    if curl -sf "http://127.0.0.1:$PORT/api/health" > /dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 1
done
if [ "$READY" -ne 1 ]; then
    echo "FAILED: Backend did not become ready; log tail:"
    tail -30 "$WORK_DIR/backend.log"
    exit 1
fi
API="http://127.0.0.1:$PORT"
USER_HDR="X-User-Email: test@test.com"

curl -s -H "$USER_HDR" "$API/api/config/shell" | python3 -c "
import sys, json
d = json.load(sys.stdin)
names = [g['name'] for g in d.get('llm_gateways', [])]
assert names == ['enterprise'], names
assert all(not m['name'].startswith('enterprise::') for m in d['models'])
print('llm_gateways:', d['llm_gateways'])
"
print_result $? "/api/config/shell lists the gateway (and no gateway models in the flat list)"

curl -s -H "$USER_HDR" "$API/api/llm/gateways/enterprise/teams" | python3 -c "
import sys, json
teams = json.load(sys.stdin)['teams']
assert [t['team_id'] for t in teams] == ['team-alpha-7f3a', 'team-beta-19c2'], teams
print(teams)
"
print_result $? "Teams endpoint returns only the user's LiteLLM teams"

curl -s -H "$USER_HDR" "$API/api/llm/gateways/enterprise/models?team_id=team-alpha-7f3a" | python3 -c "
import sys, json
models = json.load(sys.stdin)['models']
assert [m['name'] for m in models] == [
    'enterprise::team-alpha-7f3a::gpt-4o-mini',
    'enterprise::team-alpha-7f3a::claude-sonnet',
], models
print([m['name'] for m in models])
"
print_result $? "Models endpoint returns the team's models as selectable keys"

CODE=$(curl -s -o /dev/null -w "%{http_code}" -H "$USER_HDR" "$API/api/llm/gateways/enterprise/models?team_id=team-gamma-5d81")
[ "$CODE" = "403" ]
print_result $? "Models for a team the user is not in are refused (got $CODE)"

# --- Chat through team models with the CLI -------------------------------
print_header "PR #976: chat with the team header"
OUT=$(cd "$ATLAS_DIR" && python atlas_chat_cli.py "hello beta" --model "enterprise::team-beta-19c2::llama-3.3-70b" --user-email test@test.com 2>"$WORK_DIR/cli1.err")
echo "$OUT" | grep -q "\[Project Beta / llama-3.3-70b\]"
print_result $? "atlas-chat reply comes from the Project Beta team model"

OUT=$(cd "$ATLAS_DIR" && python atlas_chat_cli.py "hello alpha" --model "enterprise::team-alpha-7f3a::claude-sonnet" --user-email alice@example.com 2>"$WORK_DIR/cli2.err")
echo "$OUT" | grep -q "\[Project Alpha / claude-sonnet\]"
print_result $? "A different user and team route to Project Alpha"

curl -s "$MOCK_URL/mock/requests" | python3 -c "
import sys, json
reqs = json.load(sys.stdin)['requests']
got = [(r['model'], r['team_header'], r['outcome']) for r in reqs]
assert got == [
    ('llama-3.3-70b', 'team-beta-19c2', 'ok'),
    ('claude-sonnet', 'team-alpha-7f3a', 'ok'),
], got
print(got)
"
print_result $? "Mock received x-litellm-team-id for exactly the selected teams"

(cd "$ATLAS_DIR" && python atlas_chat_cli.py "sneaky" --model "enterprise::team-gamma-5d81::gpt-4o-mini" --user-email test@test.com > "$WORK_DIR/cli3.out" 2>&1)
CLI_RC=$?
REQS=$(curl -s "$MOCK_URL/mock/requests" | python3 -c "import sys, json; print(sum(r['team_header'] == 'team-gamma-5d81' for r in json.load(sys.stdin)['requests']))")
[ "$CLI_RC" -ne 0 ] || grep -qi "not a member" "$WORK_DIR/cli3.out"
FAILED_CLOSED=$?
[ "$FAILED_CLOSED" -eq 0 ] && [ "$REQS" = "0" ]
print_result $? "Crafted key for another user's team is refused before reaching LiteLLM (rc=$CLI_RC, gamma requests=$REQS)"

# --- Unit tests -------------------------------------------------------------
print_header "Backend unit test suite"
# Without this script's fixture environment: the suite's own e2e checks pick
# the first configured model and would otherwise talk to the mock proxy.
env -u APP_CONFIG_DIR -u APP_LOG_DIR -u PORT -u ATLAS_HOST -u PR976_LITELLM_URL \
    -u SKIP_AUTHORIZATION_CHECKS -u FEATURE_CHAT_HISTORY_ENABLED \
    ./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests (./test/run_tests.sh backend)"

print_header "SUMMARY"
echo -e "Passed: ${GREEN}$PASSED${NC} | Failed: ${RED}$FAILED${NC}"
[ $FAILED -eq 0 ] && exit 0 || exit 1
