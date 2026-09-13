#!/usr/bin/env bash
# Test script for PR #932: agent mode defaults on; drop the start banner,
# keep the small Agent box (issue #849)
#
# What this validates:
# - /api/config and /api/config/shell both expose agent_max_steps and agree
#   with the configured AGENT_MAX_STEPS (the UI slider bound source).
# - The frontend agent_start handler no longer narrates
#   "Agent Mode Started (strategy: ..., max steps: ...)" and keeps the
#   agent_status marker row instead.
# - The Agent-mode persisted preference defaults to enabled and still honors
#   a stored "off" choice (checked against the bundled frontend source).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_header() { echo ""; echo "=== $1 ==="; }
print_result() {
    if [ "$1" -eq 0 ]; then
        echo "  PASSED: $2"; PASSED=$((PASSED + 1))
    else
        echo "  FAILED: $2"; FAILED=$((FAILED + 1))
    fi
}

echo "=== PR #932 Validation: agent mode default on, start banner removed ==="

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
export PYTHONPATH="$PROJECT_ROOT"
cd "$PROJECT_ROOT"

# ==========================================
print_header "Check 1: config endpoints expose agent_max_steps and honor AGENT_MAX_STEPS"
# ==========================================
python3 - <<'PY' 2>&1 | tail -6
import os
import sys

os.environ.setdefault("AGENT_MAX_STEPS", "30")

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from atlas.infrastructure.app_factory import app_factory
from atlas.core.log_sanitizer import get_current_user
from atlas.routes.config_routes import router

config_manager = app_factory.get_config_manager()
config_manager.app_settings.agent_max_steps = 30

app = FastAPI()
app.include_router(router)

app.dependency_overrides[get_current_user] = lambda: "val@test.com"

client = TestClient(app)
shell = client.get("/api/config/shell").json()
full = client.get("/api/config").json()

ok = True
if shell.get("agent_max_steps") != 30:
    print(f"FAIL: shell agent_max_steps={shell.get('agent_max_steps')!r}, expected 30")
    ok = False
if full.get("agent_max_steps") != 30:
    print(f"FAIL: full agent_max_steps={full.get('agent_max_steps')!r}, expected 30")
    ok = False
config_manager.app_settings.agent_max_steps = 10
shell_default = client.get("/api/config/shell").json()
if shell_default.get("agent_max_steps") != 10:
    print(f"FAIL: shell did not follow the setting back to 10: {shell_default.get('agent_max_steps')!r}")
    ok = False
if not ok:
    sys.exit(1)
print("shell and full config expose agent_max_steps and follow the setting")
PY
print_result $? "agent_max_steps exposed by /api/config and /api/config/shell"

# ==========================================
print_header "Check 2: agent_start keeps a marker but no strategy/max-steps narration"
# ==========================================
grep -q "Agent Mode Started" frontend/src/handlers/chat/websocketHandlers.js
if [ $? -ne 1 ]; then
    print_result 1 "verbose banner text removed from the websocket handler"
else
    print_result 0 "verbose banner text removed from the websocket handler"
fi

python3 - <<'PY'
import re, sys
src = open("frontend/src/handlers/chat/websocketHandlers.js").read()
start = src.index("case 'agent_start':")
block = src[start:src.index("case 'agent_turn_start'")]
ok = "addMessage" in block and "type: 'agent_status'" in block and "content: ''" in block
print("agent_start still adds an empty-content agent_status marker" if ok else "FAIL: agent_start marker missing")
sys.exit(0 if ok else 1)
PY
print_result $? "agent_start keeps the small Agent marker row"

# ==========================================
print_header "Check 3: the Agent badge renders alone for the marker (no text node)"
# ==========================================
python3 - <<'PY'
import re, sys
src = open("frontend/src/components/Message.jsx").read()
m = re.search(r"message\.type === 'agent_status'\)(.*?)\n      \}", src, re.S)
ok = bool(m and "hasContent" in m.group(1) and "aria-label" in m.group(1))
print("badge-only rendering path present" if ok else "FAIL: badge-only agent_status rendering missing")
sys.exit(0 if ok else 1)
PY
print_result $? "Message.jsx renders badge-only agent_status rows"

# ==========================================
print_header "Check 4: agent mode defaults on, stored preference honored, availability-gated"
# ==========================================
python3 - <<'PY'
import sys
src = open("frontend/src/hooks/chat/useAgentMode.js").read()
ok = "chatui-agent-mode-enabled', true)" in src
print("default-on preference found" if ok else "FAIL: agent mode still defaults off")
sys.exit(0 if ok else 1)
PY
print_result $? "useAgentMode defaults chatui-agent-mode-enabled to true"

python3 - <<'PY'
import sys
hook = open("frontend/src/hooks/chat/useAgentMode.js").read()
send = open("frontend/src/contexts/ChatContext.jsx").read()
ok = "available && storedEnabled" in hook \
     and "agent.agentModeAvailable && agent.agentModeEnabled" in send
print("effective flag gated on availability at the hook and on the wire" if ok
      else "FAIL: availability gating missing")
sys.exit(0 if ok else 1)
PY
print_result $? "agent_mode wire flag is gated on feature availability"

grep -q "SAFE_CACHE_FIELDS" frontend/src/hooks/chat/useChatConfig.js && \
  grep -q "'agent_mode_available', 'agent_max_steps'" frontend/src/hooks/chat/useChatConfig.js
print_result $? "agent_max_steps is cached as a safe config field"

# ==========================================
print_header "Check 5: frontend handler tests pin the new behavior"
# ==========================================
cd frontend && npx vitest run src/test/agent-mode-default.test.jsx \
  src/test/agent-status-marker-render.test.jsx src/handlers/chat/websocketHandlers.test.js \
  src/test/print-and-debug-mode.test.jsx --reporter=basic > /tmp/pr932_vitest.log 2>&1
VITEST_RC=$?
cd "$PROJECT_ROOT"
tail -4 /tmp/pr932_vitest.log
print_result $VITEST_RC "agent mode frontend tests pass"

echo ""
echo "=== Summary: $PASSED passed, $FAILED failed ==="
[ "$FAILED" -eq 0 ] && exit 0 || exit 1