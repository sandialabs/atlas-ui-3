#!/usr/bin/env bash
# PR #433 - Add ability to run Agent Skills
#
# Validates:
# 1. SkillConfig and SkillsConfig Pydantic models exist in config_manager.py
# 2. skills_config_file in AppSettings
# 3. ConfigManager.skills_config property
# 4. skills.json default config file exists
# 5. /api/config returns skills list
# 6. MessageBuilder accepts skill_prompt parameter
# 7. ChatService resolves selected_skill to skill_prompt
# 8. main.py WebSocket handler passes selected_skill
# 9. Frontend AgentModal includes skill selector
# 10. useAgentMode includes selectedSkill state
# 11. useChatConfig loads skills from /api/config
# 12. ChatContext exposes selectedSkill
# 13. Backend unit tests pass

set -uo pipefail
# Note: -e is intentionally omitted so all checks run and results are collected before
# the summary exit. Individual failures are tracked via the PASSED/FAILED counters.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

pass() { echo "  PASSED: $1"; ((PASSED++)); }
fail() { echo "  FAILED: $1"; ((FAILED++)); }

echo "=== PR #433: Agent Skills Feature ==="
echo ""

# --- Check 1: SkillConfig and SkillsConfig models exist ---
echo "--- Check 1: SkillConfig and SkillsConfig Pydantic models ---"
CONFIG_MGR="$PROJECT_ROOT/atlas/modules/config/config_manager.py"
if grep -q 'class SkillConfig' "$CONFIG_MGR"; then
    pass "SkillConfig model defined"
else
    fail "SkillConfig model not found"
fi
if grep -q 'class SkillsConfig' "$CONFIG_MGR"; then
    pass "SkillsConfig model defined"
else
    fail "SkillsConfig model not found"
fi

# --- Check 2: skills_config_file in AppSettings ---
echo "--- Check 2: skills_config_file AppSetting ---"
if grep -q 'skills_config_file' "$CONFIG_MGR"; then
    pass "skills_config_file in AppSettings"
else
    fail "skills_config_file not found in AppSettings"
fi

# --- Check 3: ConfigManager.skills_config property ---
echo "--- Check 3: ConfigManager.skills_config property ---"
if grep -q 'def skills_config' "$CONFIG_MGR"; then
    pass "skills_config property defined on ConfigManager"
else
    fail "skills_config property not found on ConfigManager"
fi
if grep -q '_skills_config' "$CONFIG_MGR"; then
    pass "skills_config cache field exists"
else
    fail "_skills_config cache field not found"
fi

# --- Check 4: Default skills.json exists ---
echo "--- Check 4: Default skills.json config file ---"
SKILLS_FILE="$PROJECT_ROOT/atlas/config/skills.json"
if [ -f "$SKILLS_FILE" ]; then
    pass "atlas/config/skills.json exists"
else
    fail "atlas/config/skills.json not found"
fi
# Verify it is valid JSON
if python3 -c "import json; json.load(open('$SKILLS_FILE'))" 2>/dev/null; then
    pass "skills.json is valid JSON"
else
    fail "skills.json is not valid JSON"
fi

# --- Check 5: /api/config returns skills ---
echo "--- Check 5: /api/config response includes skills ---"
CONFIG_ROUTE="$PROJECT_ROOT/atlas/routes/config_routes.py"
if grep -q '"skills"' "$CONFIG_ROUTE" || grep -q "'skills'" "$CONFIG_ROUTE"; then
    pass "skills included in /api/config response"
else
    fail "skills not found in /api/config response"
fi
if grep -q 'skills_config' "$CONFIG_ROUTE"; then
    pass "skills_config referenced in config_routes.py"
else
    fail "skills_config not referenced in config_routes.py"
fi

# --- Check 6: MessageBuilder accepts skill_prompt ---
echo "--- Check 6: MessageBuilder skill_prompt parameter ---"
MSG_BUILDER="$PROJECT_ROOT/atlas/application/chat/preprocessors/message_builder.py"
if grep -q 'skill_prompt' "$MSG_BUILDER"; then
    pass "skill_prompt parameter in MessageBuilder.build_messages"
else
    fail "skill_prompt parameter not found in message_builder.py"
fi

# --- Check 7: ChatService resolves selected_skill ---
echo "--- Check 7: ChatService skill resolution ---"
SERVICE="$PROJECT_ROOT/atlas/application/chat/service.py"
if grep -q 'selected_skill' "$SERVICE"; then
    pass "selected_skill handled in service.py"
else
    fail "selected_skill not found in service.py"
fi
if grep -q 'skill_prompt' "$SERVICE"; then
    pass "skill_prompt resolved in service.py"
else
    fail "skill_prompt not resolved in service.py"
fi

# --- Check 8: main.py passes selected_skill ---
echo "--- Check 8: WebSocket handler passes selected_skill ---"
MAIN="$PROJECT_ROOT/atlas/main.py"
if grep -q 'selected_skill' "$MAIN"; then
    pass "selected_skill passed in main.py WebSocket handler"
else
    fail "selected_skill not found in main.py"
fi

# --- Check 9: AgentModal includes skill selector ---
echo "--- Check 9: AgentModal skill selector UI ---"
AGENT_MODAL="$PROJECT_ROOT/frontend/src/components/AgentModal.jsx"
if grep -q 'selectedSkill' "$AGENT_MODAL"; then
    pass "selectedSkill in AgentModal.jsx"
else
    fail "selectedSkill not found in AgentModal.jsx"
fi
if grep -q 'skills' "$AGENT_MODAL"; then
    pass "skills prop consumed in AgentModal.jsx"
else
    fail "skills prop not found in AgentModal.jsx"
fi
if grep -q '<select' "$AGENT_MODAL"; then
    pass "skill dropdown select element in AgentModal.jsx"
else
    fail "skill dropdown select element not found in AgentModal.jsx"
fi

# --- Check 10: useAgentMode includes selectedSkill ---
echo "--- Check 10: useAgentMode selectedSkill state ---"
AGENT_HOOK="$PROJECT_ROOT/frontend/src/hooks/chat/useAgentMode.js"
if grep -q 'selectedSkill' "$AGENT_HOOK"; then
    pass "selectedSkill state in useAgentMode.js"
else
    fail "selectedSkill state not found in useAgentMode.js"
fi
if grep -q 'setSelectedSkill' "$AGENT_HOOK"; then
    pass "setSelectedSkill in useAgentMode.js"
else
    fail "setSelectedSkill not found in useAgentMode.js"
fi

# --- Check 11: useChatConfig loads skills ---
echo "--- Check 11: useChatConfig loads skills from /api/config ---"
CONFIG_HOOK="$PROJECT_ROOT/frontend/src/hooks/chat/useChatConfig.js"
if grep -q 'skills' "$CONFIG_HOOK"; then
    pass "skills state in useChatConfig.js"
else
    fail "skills state not found in useChatConfig.js"
fi
if grep -q 'setSkills' "$CONFIG_HOOK"; then
    pass "setSkills call in useChatConfig.js"
else
    fail "setSkills not found in useChatConfig.js"
fi

# --- Check 12: ChatContext exposes selectedSkill ---
echo "--- Check 12: ChatContext exposes selectedSkill ---"
CHAT_CTX="$PROJECT_ROOT/frontend/src/contexts/ChatContext.jsx"
if grep -q 'selectedSkill' "$CHAT_CTX"; then
    pass "selectedSkill exposed in ChatContext.jsx"
else
    fail "selectedSkill not found in ChatContext.jsx"
fi
if grep -q 'selected_skill' "$CHAT_CTX"; then
    pass "selected_skill sent in WebSocket message"
else
    fail "selected_skill not sent in WebSocket message"
fi

# --- Check 13: Backend unit tests pass ---
echo "--- Check 13: Backend unit tests ---"
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
fi
cd "$PROJECT_ROOT/atlas"
if python -m pytest tests/test_agent_skills.py -v --tb=short -q 2>&1 | grep -q "17 passed"; then
    pass "All 17 agent skills unit tests pass"
else
    fail "Agent skills unit tests did not all pass"
fi

# --- Summary ---
echo ""
echo "======================================="
echo "Results: $PASSED passed, $FAILED failed"
echo "======================================="

if [ $FAILED -gt 0 ]; then
    exit 1
fi
exit 0
