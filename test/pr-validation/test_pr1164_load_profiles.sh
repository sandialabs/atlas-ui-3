#!/bin/bash
# Test script for PR #1164: load and scale profiles for multi-replica Atlas (issue #1129)
#
# Exercises the load tooling end to end against real replicas (podman Redis +
# Postgres via the multi-replica harness, mock LLM, mock OIDC IdP; no real
# LLM):
#   1. unit tests of the tooling (stats, SLO checks, scaling, profiles, mock IdP)
#   2. the `smoke` profile: every load path, report sections populated from real
#      traffic, SLO pass
#   3. the `oidc-delegated` profile: real Authorization Code + PKCE logins with
#      header auth off, RFC 8693 token exchange on every model call, every LLM
#      request carrying a verified delegated JWT, cross-replica relogin counted
#   4. `reconnect-storm` at 0.2 scale: a replica SIGKILLed mid-run, its sockets
#      reconnected to the survivor, the replica restarted
#   5. every process group and container the runs started is gone
#   6. ruff and the docs link check
set -uo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
# shellcheck disable=SC1091
[ -f .venv/bin/activate ] && source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
export ATLAS_MULTIREPLICA_MANIFEST="$WORK/manifest.jsonl"
: > "$ATLAS_MULTIREPLICA_MANIFEST"

fail() {
    echo "FAILED: $*"
    exit 1
}

echo "== 1. tooling unit tests"
python -m pytest test/loadtest/test_load_tooling.py -q -p no:cacheprovider || fail "tooling unit tests"
echo "PASSED: tooling unit tests"

run_profile() {  # run_profile <name> [extra args]: runs it, prints the summary, fails on non-zero exit
    local name="$1"; shift
    echo "== profile $name $*"
    if ! bash test/load_tests.sh "$name" --out "$WORK/$name" "$@" > "$WORK/$name.log" 2>&1; then
        tail -60 "$WORK/$name.log"
        fail "profile $name exited non-zero"
    fi
    sed -n '/^operation/,/^RESULT/p' "$WORK/$name.log"
}

check_report() {  # check_report <name> <python expression over r (report) that must hold>...
    local name="$1"; shift
    python - "$WORK/$name/report.json" "$@" <<'PY' || fail "report checks for $(basename "$(dirname "$1")" 2>/dev/null)"
import json, sys
r = json.load(open(sys.argv[1]))
ops, peaks, counters, mocks = r["operations"], r["peaks"], r["counters"], r["mock_stats"]
bad = [expr for expr in sys.argv[2:] if not eval(expr)]
for expr in sys.argv[2:]:
    print(("  ok    " if expr not in bad else "  FAIL  ") + expr)
sys.exit(1 if bad else 0)
PY
}

echo "== 2. smoke"
run_profile smoke
check_report smoke \
    'r["passed"]' \
    'all(ops[o]["ok"] > 0 and ops[o]["p95_ms"] is not None for o in ("ws_connect","ws_reconnect","chat_turn","chat_first_token","api_run_launch","api_run_total"))' \
    'peaks["client.open_connections"] == 20' \
    'peaks["client.active_runs"] > 0' \
    'peaks["server.replicas.A.server_conns"] + peaks["server.replicas.B.server_conns"] >= 20' \
    'peaks.get("server_active_api_runs.A", 0) + peaks.get("server_active_api_runs.B", 0) > 0' \
    'peaks["server.replicas.A.rss_mb"] > 0 and peaks["server.replicas.B.cpu_pct"] is not None' \
    'peaks["server.redis.connected_clients"] > 0 and peaks["server.postgres.connections"] > 0' \
    'counters.get("tool_approvals_sent", 0) > 0'
echo "PASSED: smoke"

echo "== 3. oidc-delegated"
run_profile oidc-delegated
check_report oidc-delegated \
    'r["passed"]' \
    'r["environment"]["auth"] == "oidc"' \
    'ops["oidc_login"]["ok"] >= 20 and ops["oidc_login"]["errors"] == 0' \
    'mocks["idp"].get("authorization_code", 0) == ops["oidc_login"]["ok"]' \
    'mocks["idp"].get("token_exchange", 0) >= 20' \
    'mocks["llm"]["completions"] > 0 and mocks["llm"].get("auth_verified") == mocks["llm"]["completions"]' \
    'mocks["llm"].get("auth_rejected", 0) == 0' \
    'counters.get("oidc_relogin", 0) == 20'
echo "PASSED: oidc-delegated (logins, token exchange and delegated JWTs verified)"

echo "== 4. reconnect-storm (0.2 scale, replica SIGKILL)"
run_profile reconnect-storm --scale 0.2
check_report reconnect-storm \
    'r["passed"]' \
    'any(p["kind"] == "reconnect_storm" and p.get("trigger") == "replica_kill" and p["affected"] > 0 and p["reconnected"] == p["affected"] for p in r["phases"])' \
    'ops["replica_restart"]["ok"] == 1' \
    'ops["ws_reconnect"]["errors"] == 0'
echo "PASSED: reconnect-storm"

echo "== 5. everything the runs started is gone"
python - "$ATLAS_MULTIREPLICA_MANIFEST" <<'PY' || fail "load runs left processes or containers behind"
import json, os, shutil, subprocess, sys

records = [json.loads(line) for line in open(sys.argv[1]) if line.strip()]
groups = [r for r in records if r["kind"] == "process"]
containers = [r["name"] for r in records if r["kind"] == "container"]
roles = {r["role"] for r in groups}
if not {"mock-llm", "mock-oidc", "replica-A", "replica-B"} <= roles:
    sys.exit(f"manifest is missing roles: {sorted(roles)}")
left = []
for r in groups:
    try:
        os.killpg(r["pgid"], 0)
    except ProcessLookupError:
        continue
    except PermissionError:
        pass
    left.append(f"{r['role']} process group {r['pgid']}")
podman = shutil.which("podman")
for name in containers:
    if podman and subprocess.run([podman, "container", "exists", name]).returncode == 0:
        left.append(f"container {name}")
print(f"checked {len(groups)} process group(s) and {len(containers)} container(s)")
if left:
    sys.exit("still present: " + ", ".join(left))
PY
echo "PASSED: cleanup"

echo "== 6. lint and docs"
ruff check test/loadtest || fail "ruff"
bash scripts/check-docs.sh || fail "docs check"
echo "PASSED: all checks"
