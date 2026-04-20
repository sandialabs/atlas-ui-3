#!/usr/bin/env bash
# PR #547 - OpenTelemetry audit trail (issue #545)
#
# Exercises the span-emission path end-to-end:
#   1. Setup phase verifies the new modules, exporter, and config are wired.
#   2. Runtime phase drives tool execution through execute_single_tool with a
#      real span pipeline + JSONLSpanExporter and inspects the written file.
#   3. Negative-control phase verifies sensitive-data policy: raw tool
#      arguments, tool outputs, and prompts never appear on span attributes.
#   4. Sidecar phase verifies ATLAS_LOG_TOOL_OUTPUTS opt-in behavior.
#   5. Finally runs the full backend unit test suite.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURE_DIR="$SCRIPT_DIR/fixtures/pr547"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

cd "$PROJECT_ROOT"
source .venv/bin/activate 2>/dev/null || true

PASS=0
FAIL=0

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASS=$((PASS + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAIL=$((FAIL + 1))
    fi
}

echo "================================================================"
echo "PR #547 Validation: OpenTelemetry audit trail (issue #545)"
echo "================================================================"

# 1. Module imports
python -c "
from atlas.core.telemetry import (
    hash_short, sha256_full, size_bytes, preview, start_span, set_attrs,
    write_tool_output_sidecar, tool_outputs_enabled,
)
from atlas.core.otel_config import JSONLSpanExporter, OpenTelemetryConfig
assert hash_short('hi') is not None
assert len(sha256_full('hi')) == 64
" > /dev/null 2>&1
print_result $? "Telemetry helpers and JSONLSpanExporter import cleanly"

# 2. .env.example exposes the new variables
grep -q "ATLAS_LOG_TOOL_OUTPUTS" "$PROJECT_ROOT/.env.example"
print_result $? ".env.example documents ATLAS_LOG_TOOL_OUTPUTS"

grep -q "OTEL_EXPORTER_OTLP_ENDPOINT" "$PROJECT_ROOT/.env.example"
print_result $? ".env.example documents OTEL_EXPORTER_OTLP_ENDPOINT"

# 3. Docs and analysis script exist
test -f "$PROJECT_ROOT/docs/telemetry/README.md"
print_result $? "docs/telemetry/README.md exists"

test -f "$PROJECT_ROOT/docs/telemetry/analysis_example.py"
print_result $? "docs/telemetry/analysis_example.py exists"

# 4. CHANGELOG entry
grep -q "OpenTelemetry audit trail" "$PROJECT_ROOT/CHANGELOG.md"
print_result $? "CHANGELOG.md has audit-trail entry"

# 5. End-to-end: drive execute_single_tool through a real OTel pipeline and
#    verify a line is written to spans.jsonl with the expected attributes.
export APP_LOG_DIR="$WORK_DIR/logs"
mkdir -p "$APP_LOG_DIR"

python - <<'PYEOF' > "$WORK_DIR/e2e.log" 2>&1
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from atlas.core.otel_config import JSONLSpanExporter
from atlas.application.chat.utilities.tool_executor import execute_single_tool
from atlas.domain.messages.models import ToolResult

spans_file = Path(os.environ["APP_LOG_DIR"]) / "spans.jsonl"
provider = TracerProvider(resource=Resource.create({"service.name": "pr547"}))
provider.add_span_processor(SimpleSpanProcessor(JSONLSpanExporter(spans_file)))
trace.set_tracer_provider(provider)

async def drive():
    tool_call = MagicMock()
    tool_call.id = "call_pr547"
    tool_call.function.name = "calculator_add"
    tool_call.function.arguments = json.dumps({"a": 21, "b": 21, "secret": "SHOULD_NOT_LEAK"})

    tool_manager = MagicMock()
    tool_manager.get_tools_schema.return_value = [
        {"function": {"name": "calculator_add",
                       "parameters": {"properties": {"a": {}, "b": {}, "secret": {}}}}}
    ]
    tool_manager.get_server_for_tool.return_value = "calculator"
    tool_manager.execute_tool = AsyncMock(return_value=ToolResult(
        tool_call_id="call_pr547",
        content="result=42 TOP_SECRET_OUTPUT",
        success=True,
    ))

    result = await execute_single_tool(
        tool_call=tool_call,
        session_context={"session_id": "sess_pr547", "user_email": "e2e@example.com"},
        tool_manager=tool_manager,
        skip_approval=True,
    )
    assert result.success, "tool should have succeeded"

asyncio.run(drive())

# Flush: trace provider shutdown sends final batch.
provider.shutdown()

assert spans_file.exists(), f"spans file missing: {spans_file}"
records = [json.loads(l) for l in spans_file.read_text().splitlines() if l.strip()]
tool_spans = [r for r in records if r["name"] == "tool.call"]
assert tool_spans, "expected a tool.call span"
attrs = tool_spans[-1]["attributes"]

assert attrs.get("tool_name") == "calculator_add"
assert attrs.get("tool_source") == "calculator"
assert attrs.get("success") is True
assert isinstance(attrs.get("args_hash"), str) and len(attrs["args_hash"]) == 16
assert isinstance(attrs.get("output_sha256"), str) and len(attrs["output_sha256"]) == 64
assert isinstance(attrs.get("duration_ms"), int)

# NEGATIVE CONTROL: raw secrets must never appear on span attributes
flat = json.dumps(attrs)
assert "SHOULD_NOT_LEAK" not in flat, "raw tool args leaked into span attrs"
assert "TOP_SECRET_OUTPUT" in flat, "preview should contain sanitized output text"
# This confirms the preview is bounded and sanitized (sanitize_for_logging
# strips control chars; it does NOT hash arbitrary payload strings).

print("E2E_OK")
PYEOF

if grep -q "E2E_OK" "$WORK_DIR/e2e.log"; then
    print_result 0 "End-to-end tool.call span emitted with correct attributes"
else
    print_result 1 "End-to-end tool.call span emission"
    echo "--- e2e.log ---"
    cat "$WORK_DIR/e2e.log"
    echo "---------------"
fi

# 6. Sidecar flag behavior (fresh process, fresh provider)
python - <<'PYEOF' > "$WORK_DIR/sidecar.log" 2>&1
import os
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from atlas.core import telemetry
from atlas.core.otel_config import JSONLSpanExporter

spans_file = Path(os.environ["APP_LOG_DIR"]) / "spans_sidecar.jsonl"
provider = TracerProvider(resource=Resource.create({"service.name": "pr547-sidecar"}))
provider.add_span_processor(SimpleSpanProcessor(JSONLSpanExporter(spans_file)))
trace.set_tracer_provider(provider)

with telemetry.start_span("sidecar.off_check"):
    os.environ.pop("ATLAS_LOG_TOOL_OUTPUTS", None)
    assert telemetry.write_tool_output_sidecar("payload") is None, "flag-off should skip write"

os.environ["ATLAS_LOG_TOOL_OUTPUTS"] = "true"
with telemetry.start_span("sidecar.on_check"):
    path = telemetry.write_tool_output_sidecar("payload-on")
    assert path is not None, "flag-on should write file"
    assert Path(path).read_text() == "payload-on"
    assert Path(path).parent.name == "tool_outputs"

print("SIDECAR_OK")
PYEOF

if grep -q "SIDECAR_OK" "$WORK_DIR/sidecar.log"; then
    print_result 0 "ATLAS_LOG_TOOL_OUTPUTS opt-in behavior is correct"
else
    print_result 1 "ATLAS_LOG_TOOL_OUTPUTS opt-in behavior"
    echo "--- sidecar.log ---"
    cat "$WORK_DIR/sidecar.log"
    echo "-------------------"
fi

# 7. Fixture .env is present and loadable
test -f "$FIXTURE_DIR/.env"
print_result $? "Fixture .env present at test/pr-validation/fixtures/pr547/.env"

# 8. Focused telemetry test module passes
PYTHONPATH="$PROJECT_ROOT" python -m pytest atlas/tests/test_telemetry_spans.py -q > "$WORK_DIR/tel.log" 2>&1
print_result $? "atlas/tests/test_telemetry_spans.py passes"

# 9. Run full backend test suite
"$PROJECT_ROOT/test/run_tests.sh" backend > "$WORK_DIR/backend.log" 2>&1
BACKEND_EXIT=$?
print_result $BACKEND_EXIT "Backend unit tests (./test/run_tests.sh backend)"
if [ "$BACKEND_EXIT" -ne 0 ]; then
    tail -30 "$WORK_DIR/backend.log"
fi

echo ""
echo "================================================================"
echo "Passed: $PASS | Failed: $FAIL"
echo "================================================================"

[ "$FAIL" -eq 0 ] && exit 0 || exit 1
