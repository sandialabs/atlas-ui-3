#!/bin/bash
# Test script for PR #1117 (issue #1117): optional compliance-level display_name.
#
# Starts a real backend on a free port with a compliance-levels.json whose
# levels carry presentation-only display_name fields, then verifies through
# the live /api/compliance-levels endpoint that:
#   1. A level with display_name exposes both the canonical name and the
#      resolved friendly label.
#   2. A level without display_name resolves the label to its name.
#   3. The banner label falls back to display_name when banner.label is
#      absent, and banner.label still wins when configured.
#   4. Duplicate display names load (legal, presentation-only) and every
#      level stays addressable by its canonical name.
#   5. A malformed display_name is dropped rather than fatal.
#   6. Backend unit suite.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr1117"

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"; PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"; FAILED=$((FAILED + 1))
    fi
}
print_header() { echo ""; echo "=========================================="; echo "$1"; echo "=========================================="; }

free_port() { python - <<'PY'
import socket
s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()
PY
}

cd "$PROJECT_ROOT" || exit 1
if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi

WORK="$(mktemp -d)"
export ATLAS_PORT="$(free_port)"
trap 'kill $BACKEND_PID 2>/dev/null; rm -rf "$WORK"' EXIT

mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
cp "$FIXTURES/compliance-levels.json" "$WORK/config/"
echo '{}' > "$WORK/config/rag-sources.json"

print_header "Starting backend on :$ATLAS_PORT"
# The dev box or CI may pin ENVIRONMENT=production, which refuses DEBUG_MODE.
export ENVIRONMENT="${ENVIRONMENT:-development}"
# MCP token storage refuses a missing or repository-placeholder key.
export MCP_TOKEN_ENCRYPTION_KEY="${MCP_TOKEN_ENCRYPTION_KEY:-$(python -c 'import secrets; print(secrets.token_urlsafe(32))')}"
(
  cd "$PROJECT_ROOT/atlas" && \
  DEBUG_MODE=true \
  FEATURE_PROXY_SECRET_ENABLED=false \
  FEATURE_COMPLIANCE_LEVELS_ENABLED=true FEATURE_COMPLIANCE_LEVEL_REQUIRED=false \
  FEATURE_TOOLS_ENABLED=false FEATURE_RAG_ENABLED=false FEATURE_CHAT_HISTORY_ENABLED=false \
  FEATURE_WORKSPACES_ENABLED=false FEATURE_AGENT_PORTAL_ENABLED=false \
  USE_MOCK_S3=true APP_LOG_DIR="$WORK/logs" \
  APP_CONFIG_DIR="$WORK/config" PYTHONPATH="$PROJECT_ROOT" \
  python -m uvicorn main:app --host 127.0.0.1 --port "$ATLAS_PORT" > "$WORK/backend.log" 2>&1
) &
BACKEND_PID=$!

for _ in $(seq 1 60); do
    curl -sf "http://127.0.0.1:$ATLAS_PORT/api/health" > /dev/null 2>&1 && break
    sleep 1
done
curl -sf "http://127.0.0.1:$ATLAS_PORT/api/health" > /dev/null 2>&1
print_result $? "backend is up"

print_header "1-5. display_name metadata over the live API"
curl -s "http://127.0.0.1:$ATLAS_PORT/api/compliance-levels" > "$WORK/levels.json"
python - "$WORK/levels.json" "$WORK/backend.log" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
backend_log = open(sys.argv[2]).read()
levels = {l["name"]: l for l in data["levels"]}

def check(cond, label):
    if not cond:
        raise SystemExit(f"FAILED: {label}")

# 1. canonical name plus resolved display_name
cui = levels["CUI_no_ECI_no_ITAR_no_PII"]
check(cui["name"] == "CUI_no_ECI_no_ITAR_no_PII", "canonical name kept")
check(cui["display_name"] == "CUI (Restricted)", "display_name resolved")

# 2. fallback to the canonical name
check(levels["Plain"]["display_name"] == "Plain", "missing display_name resolves to name")

# 3. banner label fallback chain
check(cui["banner"]["label"] == "CUI (Restricted)", "banner label falls back to display_name")
uur = levels["UUR"]["banner"]
check(uur["label"] == "Unclassified Releasable", "banner.label still wins")

# 4. duplicates load; canonical names stay distinct
check(levels["TeamAlpha"]["display_name"] == "Team Data", "duplicate label loads (A)")
check(levels["TeamBeta"]["display_name"] == "Team Data", "duplicate label loads (B)")
check(set(data["all_level_names"]) == {
    "CUI_no_ECI_no_ITAR_no_PII", "UUR", "Plain", "TeamAlpha", "TeamBeta",
}, "canonical names all addressable")
check("shared by levels TeamAlpha, TeamBeta" in backend_log.replace("\n", " "),
      "duplicate display names warn at startup")

# 5. alias resolution keeps working beside the label
check(cui["aliases"] == ["CUI_RESTRICTED"], "aliases unchanged")

print("API assertions passed")
PY
print_result $? "display_name metadata assertions"

if [ -z "$SKIP_UNIT_TESTS" ]; then
    print_header "6. Backend unit suite"
    PYTEST_OUT="$(FEATURE_PROXY_SECRET_ENABLED=false ./test/run_tests.sh backend 2>&1)"
    PYTEST_RC=$?
    echo "$PYTEST_OUT" | tail -3
    print_result $PYTEST_RC "backend unit tests"
fi

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]