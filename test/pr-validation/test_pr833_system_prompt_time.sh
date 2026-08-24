#!/usr/bin/env bash
# Test script for PR #833: inject current date/time into the system prompt (issue #823).
#
# Test plan:
# - Config: SYSTEM_PROMPT_TIMEZONE and SYSTEM_PROMPT_TIME_REFRESH_MINUTES settings
#   exist on AppSettings and are read from the environment
# - Helper + wiring: enrich_system_prompt_with_time is defined and wired into
#   MessageBuilder; the orchestrator passes config_manager to MessageBuilder
# - Docs: .env.example documents the two new variables
# - Behavior: MessageBuilder injects the current date/time into the system
#   prompt (default and custom) and appends the elapsed note after a long gap;
#   the prompt provider itself is unchanged (pure template render)
# - Unit tests: atlas/tests/test_system_prompt_time.py + backend suite
#
# Every assertion below can fail this script: grep/python/pytest statuses are
# captured directly (no `|| true` + `print_result $?` pattern, no unconditional
# PASS), and fallible commands run under `set -euo pipefail` via `rc=0; cmd || rc=$?`
# so a real failure is recorded as FAILED rather than aborting the script.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"

# Activate the venv if present (matches run_tests.sh convention).
if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
PASSED=0
FAILED=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

# Assert a fixed string is present in a file. Captures the grep status before
# reporting so a missing target records FAILED rather than being masked.
check_grep() {
    local pattern="$1" file="$2" desc="$3"
    if grep -q "$pattern" "$file"; then
        print_result 0 "$desc"
    else
        print_result 1 "$desc"
    fi
}

echo "=== PR #833 Validation: System Prompt Time Injection (issue #823) ==="

# 1. Config settings exist on AppSettings.
check_grep "system_prompt_timezone" \
    "$ATLAS_DIR/modules/config/settings.py" \
    "SYSTEM_PROMPT_TIMEZONE field declared"
check_grep "system_prompt_time_refresh_minutes" \
    "$ATLAS_DIR/modules/config/settings.py" \
    "SYSTEM_PROMPT_TIME_REFRESH_MINUTES field declared"

# 2. Helper + wiring exist.
check_grep "enrich_system_prompt_with_time" \
    "$ATLAS_DIR/application/chat/preprocessors/system_prompt_time.py" \
    "enrich_system_prompt_with_time helper defined"
check_grep "enrich_system_prompt_with_time" \
    "$ATLAS_DIR/application/chat/preprocessors/message_builder.py" \
    "MessageBuilder wires the time-injection helper"
# Anchor to the MessageBuilder construction (prompt_provider + config_manager
# together), not the bare `config_manager=config_manager` substring, which also
# matches the unrelated ToolAuthorizationService call earlier in the file and
# would still pass if the MessageBuilder wiring were reverted.
check_grep "prompt_provider=prompt_provider, config_manager=config_manager" \
    "$ATLAS_DIR/application/chat/orchestrator.py" \
    "orchestrator passes config_manager to MessageBuilder"

# 3. .env.example documents the new variables.
check_grep "SYSTEM_PROMPT_TIMEZONE" \
    "$PROJECT_ROOT/.env.example" \
    ".env.example documents SYSTEM_PROMPT_TIMEZONE"
check_grep "SYSTEM_PROMPT_TIME_REFRESH_MINUTES" \
    "$PROJECT_ROOT/.env.example" \
    ".env.example documents SYSTEM_PROMPT_TIME_REFRESH_MINUTES"

# 4. End-to-end behavior via the real MessageBuilder + ConfigManager (the same
#    code path the orchestrator drives). rc is captured so a Python assertion
#    failure records FAILED instead of aborting under `set -e`.
rc=0
python - <<'PY' || rc=$?
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 4a. Env vars are read into AppSettings.
os.environ["SYSTEM_PROMPT_TIMEZONE"] = "America/Denver"
os.environ["SYSTEM_PROMPT_TIME_REFRESH_MINUTES"] = "12"
from atlas.modules.config import ConfigManager
cm = ConfigManager()
assert cm.app_settings.system_prompt_timezone == "America/Denver", cm.app_settings.system_prompt_timezone
assert cm.app_settings.system_prompt_time_refresh_minutes == 12, cm.app_settings.system_prompt_time_refresh_minutes
print("PASSED: env vars parsed into AppSettings")

# 4b. MessageBuilder injects current date/time into the default prompt.
os.environ.pop("SYSTEM_PROMPT_TIMEZONE", None)
os.environ.pop("SYSTEM_PROMPT_TIME_REFRESH_MINUTES", None)
from atlas.application.chat.preprocessors.message_builder import MessageBuilder
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.modules.prompts.prompt_provider import PromptProvider

with tempfile.TemporaryDirectory() as d:
    pdir = Path(d) / "prompts"
    pdir.mkdir()
    (pdir / "system_prompt.md").write_text("Assistant for {user_email}.")
    cm2 = ConfigManager()
    cm2.app_settings.prompt_base_path = str(pdir)
    cm2.app_settings.system_prompt_timezone = "UTC"
    cm2.app_settings.system_prompt_time_refresh_minutes = 5
    builder = MessageBuilder(prompt_provider=PromptProvider(cm2), config_manager=cm2)

    # First turn: time present, no elapsed note.
    s = Session(user_email="t@example.com")
    s.history.add_message(Message(role=MessageRole.USER, content="hi"))
    import asyncio
    msgs = asyncio.run(builder.build_messages(session=s, include_files_manifest=False))
    assert msgs[0]["role"] == "system"
    assert "Current Date & Time" in msgs[0]["content"]
    assert "Time Since Previous Message" not in msgs[0]["content"]
    print("PASSED: default prompt gets current date/time, no note on first turn")

    # Long gap: elapsed note fires and uses the corrected wording.
    now = datetime.now(timezone.utc)
    s2 = Session(user_email="t@example.com")
    s2.history.add_message(Message(role=MessageRole.USER, content="hi", timestamp=now - timedelta(minutes=30)))
    s2.history.add_message(Message(role=MessageRole.USER, content="again", timestamp=now))
    msgs2 = asyncio.run(builder.build_messages(session=s2, include_files_manifest=False))
    assert "Time Since Previous Message" in msgs2[0]["content"]
    assert "30 minutes" in msgs2[0]["content"]
    assert "since your previous prompt" in msgs2[0]["content"], msgs2[0]["content"]
    print("PASSED: elapsed note appended after a 30-minute gap with corrected wording")

    # Custom prompt still gets time; default text does not leak.
    s3 = Session(user_email="t@example.com")
    s3.history.add_message(Message(role=MessageRole.USER, content="hi"))
    msgs3 = asyncio.run(builder.build_messages(
        session=s3, include_files_manifest=False, custom_system_prompt="You are a pirate."))
    assert msgs3[0]["content"].startswith("You are a pirate.")
    assert "Default for" not in msgs3[0]["content"]
    assert "Current Date & Time" in msgs3[0]["content"]
    print("PASSED: custom prompt keeps its text (no default leak) and gets time")

# 4c. Provider stays pure.
with tempfile.TemporaryDirectory() as d:
    pdir = Path(d) / "prompts"
    pdir.mkdir()
    (pdir / "system_prompt.md").write_text("Assistant for {user_email}.")
    cm3 = ConfigManager()
    cm3.app_settings.prompt_base_path = str(pdir)
    assert PromptProvider(cm3).get_system_prompt(user_email="a@b.c") == "Assistant for a@b.c."
print("PASSED: prompt provider output is unchanged (pure template render)")
PY
print_result $rc "end-to-end MessageBuilder behavior"

# 5. Targeted unit tests for the feature.
rc=0
( cd "$PROJECT_ROOT" && python -m pytest atlas/tests/test_system_prompt_time.py -q >/dev/null 2>&1 ) || rc=$?
print_result $rc "atlas/tests/test_system_prompt_time.py passes"

rc=0
( cd "$PROJECT_ROOT" && python -m pytest atlas/tests/test_system_prompt_loading.py -q >/dev/null 2>&1 ) || rc=$?
print_result $rc "atlas/tests/test_system_prompt_loading.py passes"

# 6. Backend suite (per PR validation README step 8).
if [ -x "$PROJECT_ROOT/test/run_tests.sh" ]; then
    rc=0
    ( cd "$PROJECT_ROOT" && ./test/run_tests.sh backend >/dev/null 2>&1 ) || rc=$?
    print_result $rc "backend test suite (./test/run_tests.sh backend)"
fi

echo ""
echo "=========================================="
echo "PR #833 validation: $PASSED passed, $FAILED failed"
echo "=========================================="
exit $FAILED