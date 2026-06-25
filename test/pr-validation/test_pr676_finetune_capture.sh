#!/bin/bash
# Test script for PR #676: Opt-in fine-tune capture with rollback preference pairs
#
# Test plan:
# - E2E: Start backend with FEATURE_FINETUNE_CAPTURE_ENABLED=true; verify the
#   feature flag is exposed in /api/config/shell, and exercise the consent REST
#   endpoints (GET default off, POST opt-in, GET reflects, DELETE self-delete).
# - E2E: Start backend with the flag false; verify opt-in is rejected with 409.
# - E2E: Drive the real CaptureService to record an SFT turn and a rollback DPO
#   pair, then run the installed atlas-finetune-export CLI for raw/sft/dpo and
#   assert the record counts.
# - Run the backend capture unit tests.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
ATLAS_DIR="$PROJECT_ROOT/atlas"
SCRATCHPAD_DIR="/tmp/pr676_test_$$"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASSED=0
FAILED=0
SKIPPED=0
BACKEND_PID=""

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

print_skip() {
    echo -e "${YELLOW}SKIPPED${NC}: $1 -- $2"
    SKIPPED=$((SKIPPED + 1))
}

cleanup() {
    if [ -n "$BACKEND_PID" ] && kill -0 "$BACKEND_PID" 2>/dev/null; then
        kill "$BACKEND_PID" 2>/dev/null
        wait "$BACKEND_PID" 2>/dev/null
    fi
    rm -rf "$SCRATCHPAD_DIR"
}
trap cleanup EXIT

mkdir -p "$SCRATCHPAD_DIR"
cd "$PROJECT_ROOT"

if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo -e "${RED}ERROR: Virtual environment not found at .venv${NC}"
    exit 1
fi

print_header "PR #676 Test Plan -- Opt-in Fine-tune Capture"
echo "Project root: $PROJECT_ROOT"
echo "Date: $(date)"

USER_HDR="X-User-Email: pr676@test.com"

wait_for_backend() {
    local port="$1"
    for _ in $(seq 1 30); do
        if curl -s "http://127.0.0.1:$port/api/config/shell" > /dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# ==============================================================================
# Part 1: E2E -- Backend with capture ENABLED; consent endpoints
# ==============================================================================
print_header "Part 1: E2E -- capture enabled, consent flow"

PORT=18676
LOG_DIR="$SCRATCHPAD_DIR/logs_on"
CAP_DIR="$SCRATCHPAD_DIR/capture_on"
mkdir -p "$LOG_DIR" "$CAP_DIR"

cd "$ATLAS_DIR"
echo "  Starting backend on port $PORT with FEATURE_FINETUNE_CAPTURE_ENABLED=true..."
FEATURE_FINETUNE_CAPTURE_ENABLED=true \
RUNTIME_CAPTURE_DIR="$CAP_DIR" \
CAPTURE_USER_SALT="pr676-salt" \
APP_LOG_DIR="$LOG_DIR" \
PORT=$PORT \
LOG_LEVEL=INFO \
python main.py > "$SCRATCHPAD_DIR/backend_on.log" 2>&1 &
BACKEND_PID=$!

if wait_for_backend "$PORT"; then
    echo "  Backend started (PID=$BACKEND_PID)"

    # Feature flag exposed in shell config
    curl -s "http://127.0.0.1:$PORT/api/config/shell" > "$SCRATCHPAD_DIR/shell.json"
    if grep -q '"finetune_capture": *true' "$SCRATCHPAD_DIR/shell.json"; then
        print_result 0 "features.finetune_capture is true in /api/config/shell"
    else
        print_result 1 "features.finetune_capture is true in /api/config/shell"
    fi

    # Default consent is off
    GET1=$(curl -s -H "$USER_HDR" "http://127.0.0.1:$PORT/api/capture/consent")
    echo "    consent (default): $GET1"
    if echo "$GET1" | grep -q '"user_enabled": *false' && echo "$GET1" | grep -q '"system_enabled": *true'; then
        print_result 0 "GET consent defaults to opted-out with system enabled"
    else
        print_result 1 "GET consent defaults to opted-out with system enabled"
    fi

    # Opt in
    POST1=$(curl -s -X POST -H "$USER_HDR" -H "Content-Type: application/json" \
        -d '{"enabled": true}' "http://127.0.0.1:$PORT/api/capture/consent")
    if echo "$POST1" | grep -q '"user_enabled": *true'; then
        print_result 0 "POST consent enabled=true opts the user in"
    else
        echo "    response: $POST1"
        print_result 1 "POST consent enabled=true opts the user in"
    fi

    # GET reflects opt-in
    GET2=$(curl -s -H "$USER_HDR" "http://127.0.0.1:$PORT/api/capture/consent")
    if echo "$GET2" | grep -q '"user_enabled": *true'; then
        print_result 0 "GET consent reflects the opt-in"
    else
        print_result 1 "GET consent reflects the opt-in"
    fi

    # Self-delete
    DEL=$(curl -s -X DELETE -H "$USER_HDR" "http://127.0.0.1:$PORT/api/capture/me")
    if echo "$DEL" | grep -q '"deleted_records"'; then
        print_result 0 "DELETE /api/capture/me returns a deletion summary"
    else
        echo "    response: $DEL"
        print_result 1 "DELETE /api/capture/me returns a deletion summary"
    fi
else
    echo -e "  ${RED}Backend did not start within 30s${NC}"
    tail -10 "$SCRATCHPAD_DIR/backend_on.log" 2>/dev/null | sed 's/^/    /'
    print_result 1 "Backend starts with FEATURE_FINETUNE_CAPTURE_ENABLED=true"
fi
kill "$BACKEND_PID" 2>/dev/null; wait "$BACKEND_PID" 2>/dev/null; BACKEND_PID=""

# ==============================================================================
# Part 2: E2E -- Backend with capture DISABLED; opt-in rejected
# ==============================================================================
print_header "Part 2: E2E -- capture disabled, opt-in rejected"

PORT2=18677
LOG_DIR2="$SCRATCHPAD_DIR/logs_off"
mkdir -p "$LOG_DIR2"
echo "  Starting backend on port $PORT2 with FEATURE_FINETUNE_CAPTURE_ENABLED=false..."
FEATURE_FINETUNE_CAPTURE_ENABLED=false \
APP_LOG_DIR="$LOG_DIR2" \
PORT=$PORT2 \
LOG_LEVEL=INFO \
python main.py > "$SCRATCHPAD_DIR/backend_off.log" 2>&1 &
BACKEND_PID=$!

if wait_for_backend "$PORT2"; then
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST -H "$USER_HDR" \
        -H "Content-Type: application/json" -d '{"enabled": true}' \
        "http://127.0.0.1:$PORT2/api/capture/consent")
    echo "    opt-in HTTP status with system disabled: $CODE"
    if [ "$CODE" = "409" ]; then
        print_result 0 "Opt-in rejected with 409 when system flag is off"
    else
        print_result 1 "Opt-in rejected with 409 when system flag is off (got $CODE)"
    fi

    SHELL2=$(curl -s "http://127.0.0.1:$PORT2/api/config/shell")
    if echo "$SHELL2" | grep -q '"finetune_capture": *false'; then
        print_result 0 "features.finetune_capture is false when disabled"
    else
        print_result 1 "features.finetune_capture is false when disabled"
    fi
else
    echo -e "  ${RED}Backend did not start within 30s${NC}"
    print_result 1 "Backend starts with FEATURE_FINETUNE_CAPTURE_ENABLED=false"
fi
kill "$BACKEND_PID" 2>/dev/null; wait "$BACKEND_PID" 2>/dev/null; BACKEND_PID=""

# ==============================================================================
# Part 3: E2E -- record turns, then export via the real CLI
# ==============================================================================
print_header "Part 3: E2E -- capture recording + atlas-finetune-export CLI"

EXPORT_CAP_DIR="$SCRATCHPAD_DIR/capture_export"
mkdir -p "$EXPORT_CAP_DIR"

cd "$ATLAS_DIR"
# Drive the real CaptureService exactly as ChatService does: activate a per-turn
# context, record an LLM call, finish the turn. One normal turn (SFT) and one
# rollback correction (DPO pair).
python - "$EXPORT_CAP_DIR" <<'PY'
import sys
from types import SimpleNamespace
from pathlib import Path
from atlas.application.chat.capture.capture_store import CaptureStore
from atlas.application.chat.capture.capture_service import CaptureService
from atlas.application.chat.capture.capture_context import capture_turn, record_llm_call

root = Path(sys.argv[1])
store = CaptureStore(root, user_salt="pr676-salt")
cfg = SimpleNamespace(app_settings=SimpleNamespace(
    feature_finetune_capture_enabled=True, runtime_capture_dir=str(root),
    capture_user_salt="pr676-salt", admin_group="admin"))
svc = CaptureService(cfg, store=store)
svc.set_consent("pr676@test.com", True)
assert svc.is_enabled_for("pr676@test.com")

msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "find weather"}]
tools = [{"type": "function", "function": {"name": "search", "description": "d", "parameters": {}}}]
tc = [SimpleNamespace(id="1", type="function",
                      function=SimpleNamespace(name="search", arguments='{"q":"weather"}'))]

ctx = svc.build_context(user_email="pr676@test.com", conversation_id="c", model="m", temperature=0.5)
with capture_turn(ctx):
    record_llm_call(msgs, tools, "", tc)
assert svc.finish_turn(ctx) is not None, "normal turn was not written"

ctx2 = svc.build_context(user_email="pr676@test.com", conversation_id="c", model="m", temperature=0.5,
    correction={"rejected_turn_id": ctx.turn_id, "note": "should search",
                "rejected": {"assistant_message": "", "tool_calls": [{"name": "fetch", "arguments": {}}]}})
with capture_turn(ctx2):
    record_llm_call(msgs, tools, "", tc)
assert svc.finish_turn(ctx2) is not None, "correction turn was not written"
print("recorded 2 turns (1 SFT, 1 DPO pair)")
PY
if [ $? -eq 0 ]; then
    print_result 0 "CaptureService records an SFT turn and a DPO pair"
else
    print_result 1 "CaptureService records an SFT turn and a DPO pair"
fi

# Invoke the installed CLI entry point (fall back to module form).
EXPORT_CMD="atlas-finetune-export"
if ! command -v atlas-finetune-export > /dev/null 2>&1; then
    EXPORT_CMD="python -m atlas.finetune_export_cli"
fi
echo "  Using exporter: $EXPORT_CMD"

RAW_N=$($EXPORT_CMD --format raw --capture-dir "$EXPORT_CAP_DIR" 2>/dev/null | grep -c .)
SFT_N=$($EXPORT_CMD --format sft --capture-dir "$EXPORT_CAP_DIR" 2>/dev/null | grep -c .)
DPO_N=$($EXPORT_CMD --format dpo --capture-dir "$EXPORT_CAP_DIR" 2>/dev/null | grep -c .)
echo "    raw=$RAW_N sft=$SFT_N dpo=$DPO_N"
[ "$RAW_N" = "2" ]; print_result $? "atlas-finetune-export --format raw emits 2 records"
[ "$SFT_N" = "2" ]; print_result $? "atlas-finetune-export --format sft emits 2 records"
[ "$DPO_N" = "1" ]; print_result $? "atlas-finetune-export --format dpo emits only the 1 pair"

# DPO content sanity: chosen=search, rejected=fetch
DPO_LINE=$($EXPORT_CMD --format dpo --capture-dir "$EXPORT_CAP_DIR" 2>/dev/null | head -1)
if echo "$DPO_LINE" | grep -q '"search"' && echo "$DPO_LINE" | grep -q '"fetch"'; then
    print_result 0 "DPO pair contains chosen=search and rejected=fetch"
else
    print_result 1 "DPO pair contains chosen=search and rejected=fetch"
fi

# ==============================================================================
# Part 4: Backend unit tests for capture
# ==============================================================================
print_header "Part 4: Backend capture unit tests"
cd "$ATLAS_DIR"
if python -m pytest tests/test_capture_store.py tests/test_capture_service.py \
    tests/test_capture_routes.py tests/test_finetune_export_cli.py -q \
    > "$SCRATCHPAD_DIR/pytest.log" 2>&1; then
    tail -1 "$SCRATCHPAD_DIR/pytest.log" | sed 's/^/    /'
    print_result 0 "Capture unit tests pass"
else
    tail -20 "$SCRATCHPAD_DIR/pytest.log" | sed 's/^/    /'
    print_result 1 "Capture unit tests pass"
fi

# ==============================================================================
# Summary
# ==============================================================================
print_header "Summary"
echo -e "  ${GREEN}PASSED: $PASSED${NC}"
echo -e "  ${RED}FAILED: $FAILED${NC}"
echo -e "  ${YELLOW}SKIPPED: $SKIPPED${NC}"

if [ "$FAILED" -gt 0 ]; then
    exit 1
fi
exit 0
