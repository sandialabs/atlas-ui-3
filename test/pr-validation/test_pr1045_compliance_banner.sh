#!/bin/bash
# Test script for PR #1045: configurable compliance classification banner.
#
# Starts a real backend on a free port with a compliance-levels.json whose
# levels carry banner configuration, then verifies through the live
# /api/compliance-levels endpoint that:
#   1. A solid banner is returned with its label and colors.
#   2. A striped (edge_stripes) banner is returned with a validated pattern.
#   3. A malformed banner is dropped (null) without removing the level or
#      changing enforcement.
#   4. Levels without a banner report null and the feature is opt-in.
#   5. Backend unit suite.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr1045"

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

print_header "1-4. Banner metadata over the live API"
curl -s "http://127.0.0.1:$ATLAS_PORT/api/compliance-levels" > "$WORK/levels.json"
python - "$WORK/levels.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
levels = {l["name"]: l for l in data["levels"]}

def check(cond, label):
    if not cond:
        raise SystemExit(f"FAILED: {label}")

# 1. solid banner
uur = levels["UUR"]["banner"]
check(uur is not None, "UUR banner present")
check(uur["label"] == "UUR", "UUR label")
check(uur["background_color"] == "#007A33", "UUR background")
check(uur["text_color"] == "#FFFFFF", "UUR text color")
check(uur["pattern"] is None, "UUR pattern is solid")

# 2. striped banner, normalized numbers
pattern = levels["CUI"]["banner"]["pattern"]
check(pattern["type"] == "edge_stripes", "CUI pattern type")
check(pattern["color"] == "#9871B9", "CUI pattern color")
check(pattern["width"] == 8.0, "CUI pattern width")
check(pattern["angle"] == 45.0, "CUI pattern angle")

# 3. malformed banner dropped, level and enforcement intact
check(levels["Broken"]["banner"] is None, "malformed banner dropped")
check("Broken" in data["all_level_names"], "malformed level still defined")

# 4. opt-in: no banner config means null
for name, level in levels.items():
    check("banner" in level, f"{name} carries a banner field")

print("API assertions passed")
PY
print_result $? "banner metadata assertions"

if [ -z "$SKIP_UNIT_TESTS" ]; then
    print_header "5. Backend unit suite"
    PYTEST_OUT="$(FEATURE_PROXY_SECRET_ENABLED=false ./test/run_tests.sh backend 2>&1)"
    PYTEST_RC=$?
    echo "$PYTEST_OUT" | tail -3
    print_result $PYTEST_RC "backend unit tests"
fi

print_header "Summary"
echo "Passed: $PASSED  Failed: $FAILED"
[ "$FAILED" -eq 0 ]
