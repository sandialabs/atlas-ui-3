#!/bin/bash
# Test script for PR for issue #1134: an Enterprise LiteLLM gateway may be the
# sole model source in llmconfig.yml.
#
# Covers the issue's acceptance criteria end to end against a real backend:
#   1. A gateway-only llmconfig.yml (zero static models) starts the backend.
#   2. Model discovery/selection works: the shell config offers the gateway,
#      the gateway lists the user's teams and their models.
#   3. A real chat turn on a gateway model key reaches the proxy with the
#      chosen team header and streams the answer.
#   4. Static-only and mixed configs still validate (CLI validate).
#   5. A config with neither static models nor a gateway fails validation
#      with a clear message.
#   6. A schema error still blocks startup and stays distinct from a
#      gateway that is merely unreachable.
#   7. The CLI lists gateway-only deployments instead of "No models".
#   8. Backend unit suite.
#
# The Enterprise LiteLLM stand-in is mocks/litellm-mock; all prompts are
# synthetic placeholders.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES="$SCRIPT_DIR/fixtures/pr1134"

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

wait_for_url() {
    local url="$1" tries="${2:-60}"
    for _ in $(seq 1 "$tries"); do
        if curl -s -o /dev/null "$url"; then return 0; fi
        sleep 1
    done
    return 1
}

cd "$PROJECT_ROOT" || exit 1
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
else
    echo "WARNING: .venv not found at $PROJECT_ROOT/.venv; using the python on PATH"
    if ! python -c "import atlas.modules.config.models" 2>/dev/null; then
        echo "FAILED: no importable 'atlas' package either; install the project (uv pip install -e '.[dev]')"
        exit 1
    fi
fi

WORK="$(mktemp -d)"
export MOCK_LITELLM_PORT="$(free_port)"
export ATLAS_PORT="$(free_port)"
trap 'kill $MOCK_PID $BACKEND_PID 2>/dev/null; rm -rf "$WORK"' EXIT

print_header "Starting mock LiteLLM proxy on :$MOCK_LITELLM_PORT and backend on :$ATLAS_PORT"
MOCK_LITELLM_PORT="$MOCK_LITELLM_PORT" python mocks/litellm-mock/main.py > "$WORK/litellm-mock.log" 2>&1 &
MOCK_PID=$!

mkdir -p "$WORK/config" "$WORK/data" "$WORK/logs"
cp "$FIXTURES/../pr1043/mcp.json" "$WORK/config/mcp.json"
echo '{}' > "$WORK/config/rag-sources.json"
cp "$FIXTURES/../pr1043/compliance-levels.json" "$WORK/config/compliance-levels.json"
sed "s/__MOCK_LITELLM_PORT__/$MOCK_LITELLM_PORT/" "$FIXTURES/llmconfig.yml" > "$WORK/config/llmconfig.yml"

(
  cd "$PROJECT_ROOT/atlas" && \
  ENVIRONMENT=local DEBUG_MODE=true FEATURE_CHAT_HISTORY_ENABLED=true FEATURE_COMPLIANCE_LEVELS_ENABLED=false \
  FEATURE_OIDC_AUTH_ENABLED=false FEATURE_PROXY_SECRET_ENABLED=false \
  FEATURE_TOOLS_ENABLED=false FEATURE_RAG_ENABLED=false FEATURE_WORKSPACES_ENABLED=false \
  USE_MOCK_S3=true CHAT_HISTORY_DB_URL="duckdb:///$WORK/data/chat_history.db" APP_LOG_DIR="$WORK/logs" \
  APP_CONFIG_DIR="$WORK/config" PYTHONPATH="$PROJECT_ROOT" \
  python -m uvicorn main:app --host 127.0.0.1 --port "$ATLAS_PORT" > "$WORK/backend.log" 2>&1
) &
BACKEND_PID=$!

if wait_for_url "http://127.0.0.1:$ATLAS_PORT/api/heartbeat" 60; then
    print_result 0 "backend started on a gateway-only llmconfig.yml"
else
    print_result 1 "backend started on a gateway-only llmconfig.yml"
    echo "--- backend.log (tail) ---"; tail -40 "$WORK/backend.log"
    exit 1
fi

if grep -q "Backend initialized with 0 static LLM models and 1 LiteLLM gateway(s)" \
        "$WORK/backend.log" "$WORK"/logs/app.jsonl 2>/dev/null; then
    print_result 0 "startup log reports 0 static models and 1 gateway"
else
    print_result 1 "startup log reports 0 static models and 1 gateway"
    grep -h "Backend initialized" "$WORK/backend.log" "$WORK"/logs/app.jsonl 2>/dev/null | tail -2
fi

print_header "End-to-end scenario (discovery, selection, chat)"
python "$FIXTURES/scenario.py"
E2E=$?
[ $E2E -eq 0 ] && print_result 0 "end-to-end scenario" || print_result 1 "end-to-end scenario"

print_header "Config-shape checks via the CLI and the loader"
# Static-only and mixed configs validate; no valid source does not.
CHECKS="$(APP_CONFIG_DIR="$WORK/config" PYTHONPATH="$PROJECT_ROOT" python - <<'PYEOF'
import sys
from pathlib import Path

sys.path.insert(0, ".")
from atlas.modules.config.config_loader import ConfigManager

root = Path(".").resolve()
results = []

def cm_for(tmp: Path, yaml_text: str) -> ConfigManager:
    config_dir = tmp / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "llmconfig.yml").write_text(yaml_text, encoding="utf-8")
    from types import SimpleNamespace
    cm = ConfigManager(atlas_root=tmp / "atlas")
    cm._app_settings = SimpleNamespace(
        app_config_dir=str(config_dir),
        llm_config_file="llmconfig.yml",
        mcp_config_file="mcp.json",
        rag_sources_config_file="rag-sources.json",
        file_extractors_config_file="file-extractors.json",
        hooks_config_file="hooks.json",
        model_fields_set={"app_config_dir"},
    )
    return cm

static_only = """
models:
  static-model:
    model_name: openai/gpt-4o
    model_url: https://api.openai.com/v1
    api_key: "sk-x"
"""
gateway_only = """
litellm_gateways:
  enterprise:
    base_url: http://127.0.0.1:1
    api_key: sk-x
"""
import tempfile
with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    results.append(("static-only validates", cm_for(tmp / "a", static_only).validate_config()["llm_config"] is True))
    results.append(("mixed validates", cm_for(tmp / "b", static_only + gateway_only).validate_config()["llm_config"] is True))
    results.append(("gateway-only validates", cm_for(tmp / "c", gateway_only).validate_config()["llm_config"] is True))
    results.append(("no valid source fails validation", cm_for(tmp / "d", "models: {}\n").validate_config()["llm_config"] is False))
for label, ok in results:
    print(("PASSED" if ok else "FAILED") + ": " + label)
sys.exit(0 if all(ok for _, ok in results) else 1)
PYEOF
)"
echo "$CHECKS"
echo "$CHECKS" | grep -q FAILED && print_result 1 "config-shape checks" || print_result 0 "config-shape checks"

# A schema error still blocks startup, distinct from an unreachable gateway.
SCHEMA="$(APP_CONFIG_DIR="$WORK/config" PYTHONPATH="$PROJECT_ROOT" python - <<'PYEOF'
import sys
from pathlib import Path
sys.path.insert(0, ".")
from atlas.modules.config.config_loader import ConfigManager
from atlas.domain.errors import ConfigurationError

import tempfile
from types import SimpleNamespace

with tempfile.TemporaryDirectory() as d:
    tmp = Path(d)
    config_dir = tmp / "config"
    config_dir.mkdir()
    (config_dir / "llmconfig.yml").write_text(
        "models:\n  bad-model:\n    max_tokens: not-a-number\n", encoding="utf-8")
    cm = ConfigManager(atlas_root=tmp / "atlas")
    cm._app_settings = SimpleNamespace(
        app_config_dir=str(config_dir),
        llm_config_file="llmconfig.yml",
        mcp_config_file="mcp.json",
        model_fields_set={"app_config_dir"},
    )
    try:
        cm.llm_config
        print("FAILED: a malformed model entry did not block loading")
        sys.exit(1)
    except ConfigurationError as exc:
        message = str(exc)
        ok = "LLM configuration validation failed" in message and "max_tokens" in message
        print(("PASSED" if ok else "FAILED") + ": schema error raises ConfigurationError with the field named")
        sys.exit(0 if ok else 1)
PYEOF
)"
echo "$SCHEMA"
case "$SCHEMA" in *PASSED*) print_result 0 "schema error stays a ConfigurationError";; *) print_result 1 "schema error stays a ConfigurationError";; esac

# The CLI lists a gateway-only deployment instead of "No models".
# Run it from $WORK so AppSettings' ../.env lookup resolves this run's
# directory, not whatever sits next to the checkout.
CLI_OUT="$(cd "$WORK" && APP_CONFIG_DIR="$WORK/config" PYTHONPATH="$PROJECT_ROOT" python -m atlas.modules.config.cli list-models 2>&1)"
if echo "$CLI_OUT" | grep -q "LiteLLM gateway" && echo "$CLI_OUT" | grep -q "enterprise"; then
    print_result 0 "CLI list-models reports the gateway on a gateway-only config"
else
    print_result 1 "CLI list-models reports the gateway on a gateway-only config"
    echo "$CLI_OUT"
fi

print_header "Affected backend unit tests"
# The affected suites (config loading, gateways, the chat caller) rather than
# the whole backend suite: run_tests.sh backend needs the container's .env
# (admin config, secrets) that a plain checkout does not carry.
(
  cd "$PROJECT_ROOT/atlas" && \
  python -m pytest tests/test_llmconfig_model_sources.py tests/test_config_manager_paths.py \
      tests/test_litellm_gateway.py tests/test_llm_litellm_tags_header.py \
      tests/test_llm_reasoning_effort.py -q
)
UNIT=$?
[ $UNIT -eq 0 ] && print_result 0 "backend unit tests (affected suites)" || print_result 1 "backend unit tests (affected suites)"

echo ""
echo "=========================================="
echo "PR #1134 validation: $PASSED passed, $FAILED failed"
echo "=========================================="
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
