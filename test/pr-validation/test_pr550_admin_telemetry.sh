#!/usr/bin/env bash
# PR #550 - Admin telemetry dashboard backed by OTel spans (issue #546)
#
# Exercises the new /admin/telemetry/* endpoints end-to-end:
#   1. Setup phase verifies routes and the SpanReader protocol wire cleanly.
#   2. Runtime phase runs real HTTP calls against every endpoint through
#      FastAPI TestClient, using a synthetic spans.jsonl fixture read by
#      FileSpanReader (the default backend).
#   3. AuthZ phase verifies require_admin rejects a non-admin user with 403.
#   4. Sensitive-data phase scans the aggregated JSON responses for any
#      SECRET_* markers that were deliberately injected into non-whitelisted
#      span attributes.
#   5. Finally runs the focused backend test module.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURE_DIR="$SCRIPT_DIR/fixtures/pr550"
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
echo "PR #550 Validation: Admin telemetry dashboard (issue #546)"
echo "================================================================"

# 1. Routes + protocol import cleanly
python -c "
from atlas.routes.telemetry_routes import (
    telemetry_router, SpanReader, FileSpanReader,
    set_span_reader, get_span_reader, _SAFE_ATTRIBUTE_KEYS,
)
assert telemetry_router.prefix == '/admin/telemetry'
" > /dev/null 2>&1
print_result $? "telemetry_routes module + SpanReader protocol import cleanly"

# 2. Route registered on FastAPI app
PYTHONPATH="$PROJECT_ROOT/atlas:$PROJECT_ROOT" python -c "
from main import app
paths = {r.path for r in app.routes}
for p in ('/admin/telemetry/status', '/admin/telemetry/overview',
          '/admin/telemetry/tools', '/admin/telemetry/llm',
          '/admin/telemetry/rag', '/admin/telemetry/sessions/search'):
    assert p in paths, f'missing route: {p}'
" > /dev/null 2>&1
print_result $? "All /admin/telemetry/* routes registered on FastAPI app"

# 3. Changelog entry exists
grep -q "### PR #550" "$PROJECT_ROOT/CHANGELOG.md"
print_result $? "CHANGELOG.md has a PR #550 entry"

# 4. Frontend dashboard component exists and is wired into App.jsx
test -f "$PROJECT_ROOT/frontend/src/components/TelemetryDashboard.jsx"
print_result $? "frontend TelemetryDashboard.jsx exists"
grep -q "TelemetryDashboard" "$PROJECT_ROOT/frontend/src/App.jsx"
print_result $? "TelemetryDashboard is registered in App.jsx"

# 5. Build the synthetic spans fixture and drive the endpoints via TestClient
export APP_LOG_DIR="$WORK_DIR/logs"
export ATLAS_DEBUG_MODE="true"
mkdir -p "$APP_LOG_DIR"
python "$FIXTURE_DIR/build_spans.py" "$APP_LOG_DIR/spans.jsonl" > /dev/null 2>&1
print_result $? "Synthetic spans.jsonl fixture built"

PYTHONPATH="$PROJECT_ROOT/atlas:$PROJECT_ROOT" python - <<'PYEOF' > "$WORK_DIR/e2e.log" 2>&1
import json
import os
import sys
from pathlib import Path

from starlette.testclient import TestClient

from atlas.modules.config import config_manager
config_manager.app_settings.debug_mode = True

from main import app
from atlas.routes import telemetry_routes

# Reset to default FileSpanReader backed by APP_LOG_DIR
telemetry_routes.set_span_reader(None)

ADMIN = {"X-User-Email": "admin@example.com"}
USER = {"X-User-Email": "user@example.com"}
client = TestClient(app)

failures = []

def must(cond, label, extra=None):
    if not cond:
        failures.append((label, extra))
        print(f"FAIL: {label} extra={extra}", flush=True)

# ---- status ----
r = client.get("/admin/telemetry/status", headers=ADMIN)
must(r.status_code == 200, "status 200 as admin", r.status_code)
body = r.json()
must(body.get("backend") == "FileSpanReader", "status reports FileSpanReader backend", body)
must(body.get("available") is True, "status reports spans file available", body)

# ---- overview ----
r = client.get("/admin/telemetry/overview", headers=ADMIN, params={"range": "24h"})
must(r.status_code == 200, "overview 200 as admin", r.status_code)
ov = r.json()
must(ov.get("turns") == 1, "overview turns=1", ov)
must(ov.get("llm_calls") == 1, "overview llm_calls=1", ov)
must(ov.get("tool_calls") == 2, "overview tool_calls=2", ov)
must(ov.get("rag_queries") == 1, "overview rag_queries=1", ov)
must(abs(ov.get("tool_success_rate") - 0.5) < 1e-6, "overview tool_success_rate=0.5", ov)

# ---- tools rollup ----
r = client.get("/admin/telemetry/tools", headers=ADMIN, params={"range": "24h"})
must(r.status_code == 200, "tools rollup 200 as admin", r.status_code)
tools = r.json().get("tools", [])
must(len(tools) == 1, "tools rollup has 1 row", tools)
must(tools[0]["tool_name"] == "pr550_demo_tool", "tool row names match", tools[0])
must(tools[0]["call_count"] == 2, "tool row call_count=2", tools[0])
must(tools[0]["failure_count"] == 1, "tool row failure_count=1", tools[0])

# ---- per-tool failures drill-down ----
r = client.get(
    "/admin/telemetry/tools/pr550_demo_tool/failures",
    headers=ADMIN,
    params={"range": "24h"},
)
must(r.status_code == 200, "tool failures 200 as admin", r.status_code)
fails = r.json().get("failures", [])
must(len(fails) == 1, "tool failures returns 1 entry", fails)
must(fails[0]["error_type"] == "TimeoutError", "failure error_type=TimeoutError", fails[0])

# ---- tool failures path param is validated ----
r = client.get(
    "/admin/telemetry/tools/..%2F..%2Fetc%2Fpasswd/failures",
    headers=ADMIN,
    params={"range": "24h"},
)
must(r.status_code in (400, 404, 422), "tool failures rejects malicious path param", r.status_code)

# ---- llm rollup ----
r = client.get("/admin/telemetry/llm", headers=ADMIN, params={"range": "24h"})
must(r.status_code == 200, "llm rollup 200 as admin", r.status_code)
models = r.json().get("models", [])
must(len(models) == 1 and models[0]["model"] == "gpt-pr550", "llm model grouped correctly", models)
must(models[0]["call_count"] == 1, "llm call_count=1", models[0])

# ---- rag rollup ----
r = client.get("/admin/telemetry/rag", headers=ADMIN, params={"range": "24h"})
must(r.status_code == 200, "rag rollup 200 as admin", r.status_code)
sources = r.json().get("sources", [])
must(len(sources) == 1 and sources[0]["data_source"] == "pr550_docs", "rag grouped by source", sources)

# ---- session search ----
r = client.get(
    "/admin/telemetry/sessions/search",
    headers=ADMIN,
    params={"session_id": "session-pr550-e2e", "range": "24h"},
)
must(r.status_code == 200, "session search 200 as admin", r.status_code)
turns = r.json().get("turns", [])
must(len(turns) == 1 and turns[0]["turn_id"] == "turn-pr550-e2e", "session search finds turn", turns)

# ---- turn drill-down ----
r = client.get("/admin/telemetry/turn/turn-pr550-e2e", headers=ADMIN)
must(r.status_code == 200, "turn drill-down 200 as admin", r.status_code)
turn = r.json()
must(turn.get("span_count", 0) >= 5, "turn has full span tree (>=5 spans)", turn.get("span_count"))
must(len(turn.get("waterfall", [])) >= 5, "turn waterfall populated", len(turn.get("waterfall", [])))

# ---- invalid range rejected ----
r = client.get("/admin/telemetry/overview", headers=ADMIN, params={"range": "bogus"})
must(r.status_code == 400, "invalid range rejected with 400", r.status_code)

# ---- authz: every endpoint rejects non-admin ----
endpoints = [
    "/admin/telemetry/status",
    "/admin/telemetry/overview",
    "/admin/telemetry/tools",
    "/admin/telemetry/llm",
    "/admin/telemetry/rag",
    "/admin/telemetry/tools/pr550_demo_tool/failures",
    "/admin/telemetry/sessions/search?session_id=session-pr550-e2e",
    "/admin/telemetry/turn/turn-pr550-e2e",
]
for path in endpoints:
    r = client.get(path, headers=USER)
    must(r.status_code in (302, 401, 403),
         f"non-admin rejected on {path}", r.status_code)

# ---- sensitive-data containment: aggregate every response body and prove
# no SECRET_* raw attribute values leak through the whitelist.
SENSITIVE = ("SECRET_PROMPT_DO_NOT_LEAK",
             "SECRET_TOOL_OUTPUT_DO_NOT_LEAK",
             "SECRET_RAG_TEXT_DO_NOT_LEAK")

collected = []
for path in [
    "/admin/telemetry/status",
    "/admin/telemetry/overview?range=24h",
    "/admin/telemetry/tools?range=24h",
    "/admin/telemetry/tools/pr550_demo_tool/failures?range=24h",
    "/admin/telemetry/llm?range=24h",
    "/admin/telemetry/rag?range=24h",
    "/admin/telemetry/sessions/search?session_id=session-pr550-e2e&range=24h",
    "/admin/telemetry/turn/turn-pr550-e2e",
]:
    r = client.get(path, headers=ADMIN)
    collected.append(r.text)

blob = "\n".join(collected)
for marker in SENSITIVE:
    must(marker not in blob, f"no {marker} leaked in aggregated responses", marker)

if failures:
    print(f"\n{len(failures)} e2e assertion(s) failed:", flush=True)
    for label, extra in failures:
        print(f"  - {label}: extra={extra}", flush=True)
    sys.exit(1)

print("E2E_OK")
PYEOF

if grep -q "E2E_OK" "$WORK_DIR/e2e.log"; then
    print_result 0 "End-to-end telemetry endpoints over TestClient (all views + authz + sensitive-data)"
else
    print_result 1 "End-to-end telemetry endpoints over TestClient"
    echo "--- e2e.log ---"
    cat "$WORK_DIR/e2e.log"
    echo "---------------"
fi

# 6. Focused test module
PYTHONPATH="$PROJECT_ROOT" python -m pytest atlas/tests/test_telemetry_routes.py -q > "$WORK_DIR/tests.log" 2>&1
if [ $? -eq 0 ]; then
    print_result 0 "atlas/tests/test_telemetry_routes.py passes"
else
    print_result 1 "atlas/tests/test_telemetry_routes.py"
    echo "--- tests.log ---"
    cat "$WORK_DIR/tests.log"
    echo "-----------------"
fi

echo
echo "================================================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================================================"
exit $FAIL
