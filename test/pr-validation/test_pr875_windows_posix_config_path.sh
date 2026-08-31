#!/bin/bash
# Test script for PR #875: recover config lookup from stale POSIX-style APP_CONFIG_DIR
# Covers the PR test plan end to end:
#   1. Reproduce the exact Windows symptom path (C:\home\agarlan\...\config\mcp.json)
#      via PureWindowsPath and assert the repo-root fallback is searched first.
#   2. Run the REAL ConfigManager.mcp_config against a fixture checkout whose
#      APP_CONFIG_DIR points at a nonexistent POSIX path and assert the MCP
#      config is found in the conventional config/ directory (1 server loads,
#      instead of 0).
#   3. Assert the default (relative "config") search list is unchanged.
#   4. Assert a valid APP_CONFIG_DIR still wins over the fallback.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASSED=0
FAILED=0

print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "\033[0;32mPASSED\033[0m: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "\033[0;31mFAILED\033[0m: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
else
    echo "FATAL: .venv not found; run uv venv && uv pip install -e '.[dev]'"
    exit 1
fi

WORKDIR="$(mktemp -d /tmp/pr875.XXXXXX)"
trap 'rm -rf "$WORKDIR"' EXIT

python - "$WORKDIR" << 'PYEOF'
import logging
import sys
from pathlib import Path, PureWindowsPath

import atlas.modules.config.config_loader as config_loader_module
from atlas.modules.config.config_manager import ConfigManager

workdir = Path(sys.argv[1])
failures = []

def check(name, cond, detail=""):
    if cond:
        print(f"PASSED: {name}")
    else:
        print(f"FAILED: {name} {detail}")
        failures.append(name)

# ---- Fixture checkout: atlas/ package dir + conventional config/mcp.json ----
atlas_root = workdir / "atlas"
atlas_root.mkdir()
repo_config = workdir / "config"
repo_config.mkdir()
(repo_config / "mcp.json").write_text(
    '{"calc": {"command": ["python", "srv.py"], "groups": ["users"]}}'
)

def make_cm(app_config_dir):
    cm = ConfigManager(atlas_root=atlas_root)
    cm._app_settings = type(
        "S", (), {"app_config_dir": app_config_dir, "mcp_config_file": "mcp.json"}
    )()
    return cm

# --- 1. Real end-to-end: stale POSIX APP_CONFIG_DIR, config still found ---
cm = make_cm("/home/agarlan/git/atlas-ui-3/config")
cfg = cm.mcp_config
check(
    "stale POSIX APP_CONFIG_DIR still loads config/mcp.json (1 server, was 0)",
    list(cfg.servers) == ["calc"],
    f"got servers={list(cfg.servers)}",
)

# --- 2. Windows-flavour reproduction of the exact logged path + recovery ---
repo_cfg_win = PureWindowsPath("C:/Users/agarlan/git/atlas-ui-3/config")
saved = config_loader_module.Path
config_loader_module.Path = PureWindowsPath
try:
    cm = make_cm("/home/agarlan/git/atlas-ui-3/config")
    cm._atlas_root = PureWindowsPath("C:/Users/agarlan/git/atlas-ui-3/atlas")
    paths = cm._search_paths("mcp.json")
    strs = [str(p) for p in paths]
finally:
    config_loader_module.Path = Path

check(
    "reproduces logged bogus path C:\\home\\agarlan\\git\\atlas-ui-3\\config\\mcp.json",
    "C:\\home\\agarlan\\git\\atlas-ui-3\\config\\mcp.json" in strs,
    f"paths={strs}",
)
repo_candidate = str(repo_cfg_win / "mcp.json")
pkg_defaults = "C:\\Users\\agarlan\\git\\atlas-ui-3\\atlas\\config\\mcp.json"
check("repo-root fallback present on Windows-flavour lookup", repo_candidate in strs, f"paths={strs}")
check(
    "repo-root fallback precedes package defaults",
    strs.index(repo_candidate) < strs.index(pkg_defaults),
)
check(
    "bogus candidates come first (documented), real candidates follow",
    strs.index("C:\\home\\agarlan\\git\\atlas-ui-3\\config\\mcp.json") < strs.index(repo_candidate),
)

# --- 3. Default relative config: list must be deduplicated and unchanged in order ---
cm = make_cm("config")
paths = [str(p) for p in cm._search_paths("mcp.json")]
check("default relative config has no duplicate candidates", len(paths) == len(set(paths)))
check(
    "default lookup still puts user config before package defaults",
    paths.index(str(workdir / "config" / "mcp.json")) < paths.index(str(atlas_root / "config" / "mcp.json")),
)

# --- 4. Valid absolute APP_CONFIG_DIR wins over the fallback ---
custom = workdir / "custom"
custom.mkdir()
(custom / "mcp.json").write_text('{"from_custom": {}}')
cm = make_cm(str(custom))
data = cm._load_file_with_error_handling(cm._search_paths("mcp.json"), "JSON")
check("valid APP_CONFIG_DIR keeps priority", data == {"from_custom": {}}, f"data={data}")

# --- 4b. Existing custom dir is never padded from the repo checkout ---
partial = workdir / "partial"
partial.mkdir()  # exists but has no mcp.json
cm = make_cm(str(partial))
paths = [str(p) for p in cm._search_paths("mcp.json")]
check(
    "existing custom APP_CONFIG_DIR excludes repo-root config from candidates",
    str(workdir / "config" / "mcp.json") not in paths,
    f"paths={paths}",
)

# --- 5. Self-diagnosing warning fires when nothing is found ---
records = []
handler = logging.Handler()
handler.emit = lambda r: records.append(r)
logger = logging.getLogger("atlas.modules.config.config_loader")
logger.addHandler(handler)
logger.setLevel(logging.WARNING)
cm = make_cm("/nonexistent/posix/config")
data = cm._load_file_with_error_handling(cm._search_paths("rag-sources.json"), "JSON")
check(
    "warning names stale APP_CONFIG_DIR",
    data is None and any("APP_CONFIG_DIR" in r.getMessage() and "does not exist" in r.getMessage() for r in records),
    f"records={[r.getMessage() for r in records]}",
)

print()
if failures:
    print(f"PR875 validation: {len(failures)} FAILED")
    raise SystemExit(1)
print("PR875 validation: all checks passed")
PYEOF
print_result $? "ConfigManager stale-POSIX-path recovery (end-to-end)"

# --- 6. Backend unit tests as final gate ---
bash ./test/run_tests.sh backend > /tmp/pr875_backend.log 2>&1
print_result $? "backend test suite"

echo ""
echo "===================="
echo "PASSED: $PASSED  FAILED: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1