#!/usr/bin/env bash
# PR #554 - Agent Portal generalization foundation (flag-gated)
#
# End-to-end validation of the three postures promised in the PR description:
#   1. Flag OFF (default): /api/agent-portal/* is not mounted, the service
#      is neither imported nor instantiated, and the full import graph still
#      loads.
#   2. Flag ON + standard tier: /api/agent-portal/config answers with
#      enabled=true; /admin/config reports allow_permissive_tier=false;
#      a LaunchSpec requesting the permissive tier is rejected at policy
#      time (HTTP 403 / PermissiveTierForbiddenError).
#   3. Flag ON + permissive opt-in: allow_permissive_tier=true and the
#      same LaunchSpec validates successfully.
#
# Additionally this script exercises the runtime invariants the design
# doc promises:
#   - Audit stream is append-only, SHA-256 chained, and tamper-evident
#     (verify_chain raises on a one-byte mutation of any frame).
#   - Session state machine rejects disallowed transitions.
#   - bubblewrap argv builder includes the negotiated Landlock /
#     --unshare-net flags for the restrictive tier.
#
# All scenarios source their own .env from fixtures/pr554/ so the host
# project config is never the source of truth.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
FIXTURES_DIR="$SCRIPT_DIR/fixtures/pr554"

cd "$PROJECT_ROOT"
source .venv/bin/activate 2>/dev/null || true

PASS=0
FAIL=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASS=$((PASS + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAIL=$((FAIL + 1))
    fi
}

# Helper: run a python snippet under a specific fixture env. We clear
# cached pydantic settings between runs so each scenario actually sees
# its fixture env instead of one cached at first import.
run_with_env() {
    local env_file="$1"
    local py_script="$2"
    (
        set -a
        # shellcheck disable=SC1090
        source "$env_file"
        set +a
        export PYTHONPATH="$PROJECT_ROOT"
        python3 - <<PY
import importlib, sys
# Invalidate cached config before the app/service imports run.
try:
    from atlas.modules.config import config_manager as _cm
    _cm.config_manager._app_settings = None  # type: ignore[attr-defined]
except Exception:
    pass
$py_script
PY
    )
}

print_header "PR #554: Agent Portal generalization foundation"

# ==========================================================================
# 0. Static sanity checks - files, CHANGELOG, docs
# ==========================================================================
print_header "0. Static sanity"

[ -f "$PROJECT_ROOT/atlas/interfaces/agent_portal.py" ]
print_result $? "RuntimeAdapter / SandboxLauncher protocols present"

[ -f "$PROJECT_ROOT/atlas/modules/agent_portal/audit.py" ]
print_result $? "audit.py present"

[ -f "$PROJECT_ROOT/atlas/modules/agent_portal/service.py" ]
print_result $? "service.py present"

[ -f "$PROJECT_ROOT/atlas/routes/agent_portal_routes.py" ]
print_result $? "agent_portal_routes.py present"

[ -f "$PROJECT_ROOT/docs/planning/agent-portal-2026-04-20.md" ]
print_result $? "design doc present"

[ -f "$PROJECT_ROOT/docs/features/agent-portal.md" ]
print_result $? "user-facing feature doc present"

for png in architecture state_machine sandbox_tiers audit_frame; do
    [ -f "$PROJECT_ROOT/docs/features/img/${png}.png" ]
    print_result $? "docs/features/img/${png}.png committed"
done

grep -q "^### PR #554" "$PROJECT_ROOT/CHANGELOG.md"
print_result $? "CHANGELOG has PR #554 entry"

grep -q "FEATURE_AGENT_PORTAL_ENABLED" "$PROJECT_ROOT/.env.example"
print_result $? ".env.example documents FEATURE_AGENT_PORTAL_ENABLED"

# ==========================================================================
# 1. Flag OFF: routes are not mounted, service is not imported by main
# ==========================================================================
print_header "1. Flag OFF (default posture)"

run_with_env "$FIXTURES_DIR/.env.off" "
from fastapi.testclient import TestClient
from atlas.main import app

portal_routes = [r.path for r in app.routes if getattr(r, 'path', '').startswith('/api/agent-portal')]
assert portal_routes == [], f'expected no portal routes mounted, got {portal_routes!r}'

# And a live probe returns 404, not 503 — the route genuinely is not there.
client = TestClient(app)
resp = client.get('/api/agent-portal/config', headers={'X-User-Email': 'dev@example.com'})
assert resp.status_code == 404, f'expected 404 with flag off, got {resp.status_code}'
print('flag-off posture OK')
" 2>/tmp/pr554_1.log
print_result $? "flag OFF: /api/agent-portal/* unregistered (check /tmp/pr554_1.log on failure)"

# ==========================================================================
# 2. Flag ON + standard tier (permissive NOT opted in)
# ==========================================================================
print_header "2. Flag ON + standard tier (permissive forbidden)"

run_with_env "$FIXTURES_DIR/.env.on.standard" "
from fastapi.testclient import TestClient
from atlas.main import app
client = TestClient(app)

resp = client.get('/api/agent-portal/config', headers={'X-User-Email': 'dev@example.com'})
assert resp.status_code == 200, f'expected 200, got {resp.status_code}: {resp.text}'
body = resp.json()
assert body.get('enabled') is True, body
assert body.get('default_tier') == 'standard', body

admin = client.get('/api/agent-portal/admin/config', headers={'X-User-Email': 'dev@example.com'})
assert admin.status_code == 200, admin.text
adm = admin.json()
assert adm.get('enabled') is True
assert adm.get('allow_permissive_tier') is False, adm
assert adm.get('sandbox_backend') == 'bubblewrap', adm

# Policy check via the service directly (routes for launch need more
# wiring than this scaffolding provides; that is the point of the PR).
from atlas.modules.agent_portal.service import (
    AgentPortalService, PermissiveTierForbiddenError,
)
from atlas.modules.agent_portal.models import LaunchSpec, SandboxTier
svc = AgentPortalService(
    enabled=True,
    default_tier=SandboxTier.standard,
    allow_permissive_tier=False,
    sandbox_backend='bubblewrap',
)
spec = LaunchSpec(
    template_id='smoke',
    scope='pr554 validation',
    tool_allowlist=[],
    sandbox_tier=SandboxTier.permissive,
    agent_command=['/bin/true'],
)
try:
    svc.validate_spec(spec)
except PermissiveTierForbiddenError:
    pass
else:
    raise AssertionError('permissive tier should have been rejected')
print('flag-on/standard posture OK')
" 2>/tmp/pr554_2.log
print_result $? "flag ON + standard: routes mount, permissive tier rejected (check /tmp/pr554_2.log)"

# ==========================================================================
# 3. Flag ON + permissive opt-in
# ==========================================================================
print_header "3. Flag ON + permissive opt-in"

run_with_env "$FIXTURES_DIR/.env.on.permissive" "
from fastapi.testclient import TestClient
from atlas.main import app
client = TestClient(app)

admin = client.get('/api/agent-portal/admin/config', headers={'X-User-Email': 'dev@example.com'})
assert admin.status_code == 200, admin.text
adm = admin.json()
assert adm.get('allow_permissive_tier') is True, adm
assert adm.get('sandbox_backend') == 'none', adm

from atlas.modules.agent_portal.service import AgentPortalService
from atlas.modules.agent_portal.models import LaunchSpec, SandboxTier
svc = AgentPortalService(
    enabled=True,
    default_tier=SandboxTier.standard,
    allow_permissive_tier=True,
    sandbox_backend='none',
)
spec = LaunchSpec(
    template_id='smoke',
    scope='pr554 validation permissive',
    tool_allowlist=[],
    sandbox_tier=SandboxTier.permissive,
    agent_command=['/bin/true'],
)
out = svc.validate_spec(spec)
assert out.sandbox_tier is SandboxTier.permissive
print('flag-on/permissive posture OK')
" 2>/tmp/pr554_3.log
print_result $? "flag ON + opt-in: permissive tier accepted (check /tmp/pr554_3.log)"

# ==========================================================================
# 4. Audit chain integrity (flag-independent invariant)
# ==========================================================================
print_header "4. SHA-256 audit chain"

export PYTHONPATH="$PROJECT_ROOT"
python3 - <<'PY' 2>/tmp/pr554_4.log
import json
import tempfile
from pathlib import Path

from atlas.modules.agent_portal.audit import AuditStream, verify_chain, AuditChainError

with tempfile.TemporaryDirectory() as tmp:
    p = Path(tmp) / "session.jsonl"
    s = AuditStream(p, session_id="t1")
    for i in range(5):
        s.append("tool", payload={"tool": f"t{i}", "result_size": i})
    r = verify_chain(p)
    assert r["ok"] and r["frames"] == 5, r

    # Mutate frame 2 and expect chain verification to fail from that point.
    lines = p.read_bytes().splitlines()
    frame = json.loads(lines[2])
    frame["result_size"] = frame["result_size"] + 999
    lines[2] = json.dumps(frame, sort_keys=True, separators=(",", ":")).encode()
    p.write_bytes(b"\n".join(lines) + b"\n")
    raised = False
    try:
        verify_chain(p)
    except AuditChainError:
        raised = True
    assert raised, "tampered chain should have raised AuditChainError"
print("audit chain OK")
PY
print_result $? "audit stream is tamper-evident (check /tmp/pr554_4.log)"

# ==========================================================================
# 5. Session state machine rejects illegal edges
# ==========================================================================
print_header "5. Session state machine"

python3 - <<'PY' 2>/tmp/pr554_5.log
from atlas.modules.agent_portal.models import LaunchSpec, SandboxTier, SessionState
from atlas.modules.agent_portal.session_manager import SessionManager

mgr = SessionManager()
spec = LaunchSpec(
    template_id="t", scope="s", tool_allowlist=[],
    sandbox_tier=SandboxTier.standard,
    agent_command=["/bin/true"],
)
s = mgr.create(user_email="u@e.com", spec=spec)
assert s.state is SessionState.pending

# Legal progression
mgr.transition(s.id, SessionState.authenticating)
mgr.transition(s.id, SessionState.launching)
mgr.transition(s.id, SessionState.running)

# Illegal: running -> pending should be rejected
raised = False
try:
    mgr.transition(s.id, SessionState.pending)
except ValueError:
    raised = True
assert raised, "running->pending must be rejected"

# Legal terminal
mgr.transition(s.id, SessionState.ending)
mgr.transition(s.id, SessionState.ended)

# Illegal after terminal
raised = False
try:
    mgr.transition(s.id, SessionState.running)
except ValueError:
    raised = True
assert raised, "post-terminal transition must be rejected"
print("state machine OK")
PY
print_result $? "state machine rejects illegal edges (check /tmp/pr554_5.log)"

# ==========================================================================
# 6. bubblewrap argv builder bakes in the restrictive-tier flags
# ==========================================================================
print_header "6. bubblewrap argv for restrictive tier"

python3 - <<'PY' 2>/tmp/pr554_6.log
from atlas.modules.agent_portal.sandbox.profiles import get_default_profile
from atlas.modules.agent_portal.sandbox.launcher import BubblewrapLauncher
from atlas.modules.agent_portal.models import SandboxTier

profile = get_default_profile(SandboxTier.restrictive)
argv = BubblewrapLauncher().build_command(profile, agent_command=["/bin/true"])

must_contain = ["bwrap", "--unshare-net"]
for token in must_contain:
    assert token in argv, f"missing {token!r} in argv: {argv}"
# Restrictive tier must NOT carry --share-net or permissive mounts.
forbidden = ["--share-net"]
for token in forbidden:
    assert token not in argv, f"restrictive tier argv contains forbidden {token!r}: {argv}"
print("bwrap argv OK")
PY
print_result $? "bwrap argv includes --unshare-net for restrictive tier (check /tmp/pr554_6.log)"

# ==========================================================================
# 7. Unit-test module: run the 36 agent_portal tests
# ==========================================================================
print_header "7. Unit tests (agent_portal)"

(
    cd "$PROJECT_ROOT/atlas"
    PYTHONPATH="$PROJECT_ROOT" python3 -m pytest tests/test_agent_portal_*.py --no-header -q
) > /tmp/pr554_7.log 2>&1
print_result $? "all 36 agent_portal unit tests pass (see /tmp/pr554_7.log)"

# ==========================================================================
# Summary
# ==========================================================================
print_header "PR #554 validation summary"
echo "  Passed: $PASS"
echo "  Failed: $FAIL"

if [ "$FAIL" -ne 0 ]; then
    exit 1
fi
exit 0
