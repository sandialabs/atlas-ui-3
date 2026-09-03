#!/bin/bash
# Test script for PR #882: preconfigured personas from a folder of markdown files (#880)
# Covers the PR test plan end to end against a REAL running backend:
#   1. GET /api/personas lists the packaged sample personas.
#   1b. <APP_CONFIG_DIR>/personas/ (config/personas/) is the default admin
#       location and fully replaces the packaged samples.
#   2. An override folder (PERSONAS_DIR) fully replaces the packaged samples.
#   3. A persona with no access_group is visible to everyone.
#   4. A persona gated on a group the user IS in is listed; GET by id is 200.
#   5. A persona gated on a group the user is NOT in is hidden; GET by id is 404.
#   6. A file with frontmatter but no prompt body is skipped.
#   7. README.md in the folder is not offered as a persona.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASSED=0
FAILED=0
PORT=8931
BASE="http://127.0.0.1:$PORT"

print_result() {
    if [ "$1" -eq 0 ]; then
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

SERVER_PID=""
WORKDIR="$(mktemp -d /tmp/pr882.XXXXXX)"
cleanup() {
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null
    rm -rf "$WORKDIR"
}
trap cleanup EXIT

start_server() {
    # $1 = value for PERSONAS_DIR (may be empty for the conventional locations)
    # $2 = value for APP_CONFIG_DIR (may be empty to inherit the environment)
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null && sleep 2
    if [ -n "$2" ]; then
        PERSONAS_DIR="$1" APP_CONFIG_DIR="$2" PYTHONPATH="$PROJECT_ROOT" \
            python -m uvicorn atlas.main:app --host 127.0.0.1 --port "$PORT" \
            > "$WORKDIR/server.log" 2>&1 &
    else
        PERSONAS_DIR="$1" PYTHONPATH="$PROJECT_ROOT" \
            python -m uvicorn atlas.main:app --host 127.0.0.1 --port "$PORT" \
            > "$WORKDIR/server.log" 2>&1 &
    fi
    SERVER_PID=$!
    for _ in $(seq 1 40); do
        if curl -sf "$BASE/api/health" > /dev/null 2>&1; then return 0; fi
        sleep 1
    done
    echo "FATAL: backend did not become healthy on port $PORT"
    tail -20 "$WORKDIR/server.log"
    exit 1
}

ids() {
    curl -s "$BASE/api/personas" | python -c \
        "import json,sys; print(' '.join(p['id'] for p in json.load(sys.stdin)['personas']))"
}

status() {
    curl -s -o /dev/null -w '%{http_code}' "$BASE/api/personas/$1"
}

# ---------------------------------------------------------------- 1. defaults
start_server ""
DEFAULT_IDS="$(ids)"
echo "$DEFAULT_IDS" | grep -q "research-assistant"
print_result $? "packaged sample personas are listed (got: $DEFAULT_IDS)"

echo "$DEFAULT_IDS" | grep -qv "readme"
print_result $? "README.md in the personas folder is not a persona"

# ------------------------------------------- 1b. <APP_CONFIG_DIR>/personas
APPCONFIG="$WORKDIR/appconfig"
mkdir -p "$APPCONFIG/personas"
cat > "$APPCONFIG/personas/config-dir-persona.md" <<'EOF'
---
name: Config Dir Persona
---
You came from the user config dir.
EOF

start_server "" "$APPCONFIG"
CONFIG_IDS="$(ids)"

echo "$CONFIG_IDS" | grep -q "config-dir-persona"
print_result $? "persona from <APP_CONFIG_DIR>/personas is listed (got: $CONFIG_IDS)"

echo "$CONFIG_IDS" | grep -qv "research-assistant"
print_result $? "config-dir personas replace the packaged samples"

# ------------------------------------------------------- 2-7. override folder
PERSONAS="$WORKDIR/personas"
mkdir -p "$PERSONAS"
cat > "$PERSONAS/open-persona.md" <<'EOF'
---
name: Open Persona
description: Visible to everyone
---
You are visible to everyone.
EOF
cat > "$PERSONAS/allowed-persona.md" <<'EOF'
---
name: Allowed Persona
access_group: users
---
You are gated on a group the test user is in.
EOF
cat > "$PERSONAS/denied-persona.md" <<'EOF'
---
name: Denied Persona
access_group: no-such-group-here
---
You are gated on a group nobody is in.
EOF
cat > "$PERSONAS/empty-persona.md" <<'EOF'
---
name: Empty Persona
---
EOF
cat > "$PERSONAS/README.md" <<'EOF'
# Docs, not a persona
EOF

start_server "$PERSONAS"
OVERRIDE_IDS="$(ids)"

echo "$OVERRIDE_IDS" | grep -q "open-persona"
print_result $? "ungated persona from PERSONAS_DIR is listed (got: $OVERRIDE_IDS)"

curl -s "$BASE/api/personas" | python -c "
import json, sys
personas = json.load(sys.stdin)['personas']
assert all('content' not in p for p in personas), 'list endpoint must not ship full content'
assert all(p.get('preview') for p in personas), 'list endpoint must ship a preview'
"
print_result $? "list endpoint ships previews, not full prompt bodies"

echo "$OVERRIDE_IDS" | grep -q "allowed-persona"
print_result $? "persona gated on a group the user IS in is listed"

echo "$OVERRIDE_IDS" | grep -qv "denied-persona"
print_result $? "persona gated on a group the user is NOT in is hidden"

echo "$OVERRIDE_IDS" | grep -qv "research-assistant"
print_result $? "override folder replaces the packaged samples"

echo "$OVERRIDE_IDS" | grep -qv "empty-persona"
print_result $? "persona file with no prompt body is skipped"

echo "$OVERRIDE_IDS" | grep -qv "readme"
print_result $? "README.md in the override folder is not a persona"

[ "$(status open-persona)" = "200" ]
print_result $? "GET /api/personas/open-persona returns 200"

[ "$(status allowed-persona)" = "200" ]
print_result $? "GET /api/personas/allowed-persona returns 200"

[ "$(status denied-persona)" = "404" ]
print_result $? "GET /api/personas/denied-persona returns 404 for a non-member"

[ "$(status no-such-persona)" = "404" ]
print_result $? "GET /api/personas/no-such-persona returns 404"

CONTENT="$(curl -s "$BASE/api/personas/open-persona" | python -c \
    "import json,sys; print(json.load(sys.stdin)['persona']['content'])")"
[ "$CONTENT" = "You are visible to everyone." ]
print_result $? "persona content is the markdown body below the frontmatter"

echo
echo "======================================"
echo "Passed: $PASSED   Failed: $FAILED"
echo "======================================"
[ "$FAILED" -eq 0 ] || exit 1
