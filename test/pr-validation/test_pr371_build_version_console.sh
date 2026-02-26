#!/usr/bin/env bash
# PR #371 Validation: App version + git hash in browser console and /api/health
# Tests that build-time version injection and health endpoint git_commit field work correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"
source .venv/bin/activate 2>/dev/null || true

PASSED=0
FAILED=0
pass() { echo "PASS  $1"; PASSED=$((PASSED + 1)); }
fail() { echo "FAIL  $1"; FAILED=$((FAILED + 1)); }

# ------------------------------------------------------------------
# 1. version.py matches pyproject.toml
# ------------------------------------------------------------------
echo "--- Check version.py matches pyproject.toml ---"
PY_VERSION=$(python3 -c "import re; print(re.search(r'VERSION\s*=\s*\"([^\"]+)\"', open('atlas/version.py').read()).group(1))")
TOML_VERSION=$(python3 -c "
import re
with open('pyproject.toml') as f:
    m = re.search(r'^version\s*=\s*\"([^\"]+)\"', f.read(), re.MULTILINE)
    print(m.group(1))
")
if [ "$PY_VERSION" = "$TOML_VERSION" ]; then
    pass "version.py ($PY_VERSION) matches pyproject.toml ($TOML_VERSION)"
else
    fail "version.py ($PY_VERSION) does not match pyproject.toml ($TOML_VERSION)"
fi

# ------------------------------------------------------------------
# 2. health_routes.py imports subprocess and defines GIT_COMMIT
# ------------------------------------------------------------------
echo "--- Check health_routes.py has git_commit support ---"
if python3 -c "
from atlas.routes.health_routes import GIT_COMMIT
assert isinstance(GIT_COMMIT, str) and len(GIT_COMMIT) > 0
print(f'GIT_COMMIT = {GIT_COMMIT}')
"; then
    pass "health_routes.GIT_COMMIT resolves to a non-empty string"
else
    fail "health_routes.GIT_COMMIT is missing or empty"
fi

# ------------------------------------------------------------------
# 3. GIT_COMMIT env var override works
# ------------------------------------------------------------------
echo "--- Check GIT_COMMIT env var override ---"
RESULT=$(GIT_COMMIT="abc1234" python3 -c "
from atlas.routes.health_routes import _resolve_git_commit
print(_resolve_git_commit())
")
if [ "$RESULT" = "abc1234" ]; then
    pass "GIT_COMMIT env var override works"
else
    fail "GIT_COMMIT env var override returned '$RESULT' instead of 'abc1234'"
fi

# ------------------------------------------------------------------
# 4. Start backend and hit /api/health, verify git_commit field
# ------------------------------------------------------------------
echo "--- Check /api/health includes git_commit ---"
PORT=18371
python3 -c "
import uvicorn, threading, time, sys
sys.path.insert(0, '$PROJECT_ROOT')
from atlas.main import app

def run():
    uvicorn.run(app, host='127.0.0.1', port=$PORT, log_level='error')

t = threading.Thread(target=run, daemon=True)
t.start()
time.sleep(3)

import urllib.request, json
resp = urllib.request.urlopen('http://127.0.0.1:$PORT/api/health')
data = json.loads(resp.read())
assert 'git_commit' in data, f'git_commit missing from health response: {data}'
assert isinstance(data['git_commit'], str) and len(data['git_commit']) > 0, f'git_commit is empty'
assert 'version' in data, f'version missing from health response: {data}'
print(f'Health response: version={data[\"version\"]} git_commit={data[\"git_commit\"]}')
" && pass "/api/health returns git_commit field" || fail "/api/health missing git_commit field"

# ------------------------------------------------------------------
# 5. Vite config contains define block with __APP_VERSION__
# ------------------------------------------------------------------
echo "--- Check vite.config.js has define block ---"
if grep -q '__APP_VERSION__' frontend/vite.config.js && \
   grep -q '__GIT_HASH__' frontend/vite.config.js && \
   grep -q '__BUILD_TIME__' frontend/vite.config.js; then
    pass "vite.config.js has __APP_VERSION__, __GIT_HASH__, __BUILD_TIME__ defines"
else
    fail "vite.config.js missing define block entries"
fi

# ------------------------------------------------------------------
# 6. App.jsx has console.info with build version
# ------------------------------------------------------------------
echo "--- Check App.jsx logs build info ---"
if grep -q 'console.info' frontend/src/App.jsx && \
   grep -q '__APP_VERSION__' frontend/src/App.jsx; then
    pass "App.jsx has console.info with version logging"
else
    fail "App.jsx missing console.info version logging"
fi

# ------------------------------------------------------------------
# 7. Dockerfile has GIT_HASH build arg
# ------------------------------------------------------------------
echo "--- Check Dockerfile has GIT_HASH build arg ---"
if grep -q 'ARG GIT_HASH' Dockerfile && \
   grep -q 'ARG APP_VERSION' Dockerfile && \
   grep -q 'GIT_COMMIT=' Dockerfile; then
    pass "Dockerfile has GIT_HASH, APP_VERSION build args and GIT_COMMIT env"
else
    fail "Dockerfile missing build args for version/git hash"
fi

# ------------------------------------------------------------------
# 8. Run backend unit tests
# ------------------------------------------------------------------
echo "--- Running backend unit tests ---"
if "$PROJECT_ROOT/test/run_tests.sh" backend; then
    pass "Backend tests pass"
else
    fail "Backend tests failed"
fi

# ------------------------------------------------------------------
echo ""
echo "================================================================"
echo "Results: $PASSED passed, $FAILED failed"
echo "================================================================"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
