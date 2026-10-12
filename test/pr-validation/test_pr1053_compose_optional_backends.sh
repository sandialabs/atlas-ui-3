#!/bin/bash
# PR #1053: bundled MinIO and PostgreSQL are opt-in Compose files (issue #1052).
# Exercises the actual `docker compose config` surface: required credentials
# fail closed per backend combination, and credentials of backends that are
# not included are never requested.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

print_result() {
    if [ $1 -eq 0 ]; then
        echo "PASSED: $2"
        PASSED=$((PASSED + 1))
    else
        echo "FAILED: $2"
        FAILED=$((FAILED + 1))
    fi
}

cd "$PROJECT_ROOT"
source .venv/bin/activate

COMPOSE=""
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
fi
if [ -z "$COMPOSE" ]; then
    echo "FAILED: Docker Compose is required to validate this PR"
    exit 1
fi

# Isolated environment: no inherited credentials, no project .env auto-load.
CLEAN_ENV=(env -i PATH="$PATH" HOME="$HOME")
APP_SECRETS=(
    CAPABILITY_TOKEN_SECRET=pr-validation-value-only
    MCP_TOKEN_ENCRYPTION_KEY=pr-validation-value-only
    PROXY_SECRET=pr-validation-value-only
)
S3_SETTINGS=(
    S3_ENDPOINT=http://minio:9000
    S3_ACCESS_KEY=pr-validation-access-key
    S3_SECRET_KEY=pr-validation-secret-key
)
FILES_APP=(-f "$PROJECT_ROOT/docker-compose.yml")
FILES_MINIO=("${FILES_APP[@]}" -f "$PROJECT_ROOT/docker-compose.minio.yml")
FILES_POSTGRES=("${FILES_APP[@]}" -f "$PROJECT_ROOT/docker-compose.postgres.yml")
FILES_ALL=("${FILES_MINIO[@]}" -f "$PROJECT_ROOT/docker-compose.postgres.yml")

# expect_render_ok <label> [env assignments...] -- [compose -f files...]
expect_render_ok() {
    local label="$1"; shift
    local assignments=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do assignments+=("$1"); shift; done
    shift # discard --
    local out
    if out=$("${CLEAN_ENV[@]}" "${assignments[@]}" $COMPOSE --env-file /dev/null "${@}" config 2>&1); then
        print_result 0 "$label"
        printf '%s\n' "$out"
    else
        print_result 1 "$label"
        printf '%s\n' "$out"
    fi
}

# expect_render_rejected <label> <expected-substring> [env assignments...] -- [compose -f files...]
expect_render_rejected() {
    local label="$1" expected="$2"; shift 2
    local assignments=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do assignments+=("$1"); shift; done
    shift # discard --
    local out status=0
    out=$("${CLEAN_ENV[@]}" "${assignments[@]}" $COMPOSE --env-file /dev/null "${@}" config 2>&1) || status=$?
    if [ "$status" -ne 0 ] && printf '%s' "$out" | grep -q "$expected"; then
        print_result 0 "$label"
    else
        print_result 1 "$label"
        printf '%s\n' "$out"
    fi
}

# 1. External S3 + DuckDB: base stack renders without any bundled-backend
#    credential (acceptance criteria: DuckDB without POSTGRES_PASSWORD,
#    external S3 without MINIO_ROOT_PASSWORD).
expect_render_ok "base stack renders with application + S3 credentials only" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" -- "${FILES_APP[@]}"

# 2. Authenticated S3 is required in the Compose stack: missing or empty
#    credentials fail clearly at configuration time.
expect_render_rejected "base stack rejects missing S3_ENDPOINT" "S3_ENDPOINT" \
    "${APP_SECRETS[@]}" S3_ACCESS_KEY=pr-validation-access-key S3_SECRET_KEY=pr-validation-secret-key -- "${FILES_APP[@]}"
expect_render_rejected "base stack rejects missing S3_ACCESS_KEY" "S3_ACCESS_KEY" \
    "${APP_SECRETS[@]}" S3_ENDPOINT=http://minio:9000 S3_SECRET_KEY=pr-validation-secret-key -- "${FILES_APP[@]}"
expect_render_rejected "base stack rejects missing S3_SECRET_KEY" "S3_SECRET_KEY" \
    "${APP_SECRETS[@]}" S3_ENDPOINT=http://minio:9000 S3_ACCESS_KEY=pr-validation-access-key -- "${FILES_APP[@]}"
expect_render_rejected "base stack rejects empty S3_SECRET_KEY" "S3_SECRET_KEY" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" S3_SECRET_KEY= -- "${FILES_APP[@]}"

# 3. Application secrets stay mandatory in every combination.
expect_render_rejected "base stack rejects missing CAPABILITY_TOKEN_SECRET" "CAPABILITY_TOKEN_SECRET" \
    MCP_TOKEN_ENCRYPTION_KEY=pr-validation-value-only PROXY_SECRET=pr-validation-value-only "${S3_SETTINGS[@]}" -- "${FILES_APP[@]}"

# 4. Bundled MinIO: included file derives the root account from the
#    application's S3 pair, needs no MINIO_ROOT_PASSWORD, and fails closed
#    without that pair.
expect_render_ok "bundled MinIO stack renders without MINIO_ROOT_PASSWORD" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" -- "${FILES_MINIO[@]}"
expect_render_rejected "bundled MinIO stack rejects missing S3_SECRET_KEY" "S3_SECRET_KEY" \
    "${APP_SECRETS[@]}" S3_ENDPOINT=http://minio:9000 S3_ACCESS_KEY=pr-validation-access-key -- "${FILES_MINIO[@]}"
rendered=$("${CLEAN_ENV[@]}" "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" $COMPOSE --env-file /dev/null "${FILES_MINIO[@]}" config 2>&1) &&
    printf '%s' "$rendered" | grep -q "MINIO_ROOT_PASSWORD: pr-validation-secret-key"
print_result $? "bundled MinIO root password equals the application S3 secret"

# 5. Bundled PostgreSQL: required only when its file is included, and the
#    self-contained file also runs alone for a host-run Atlas.
expect_render_ok "bundled PostgreSQL stack renders with POSTGRES_PASSWORD" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" POSTGRES_PASSWORD=pr-validation-value-only -- "${FILES_POSTGRES[@]}"
expect_render_rejected "bundled PostgreSQL stack rejects missing POSTGRES_PASSWORD" "POSTGRES_PASSWORD" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" -- "${FILES_POSTGRES[@]}"
expect_render_rejected "full bundled stack rejects missing POSTGRES_PASSWORD" "POSTGRES_PASSWORD" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" -- "${FILES_ALL[@]}"
expect_render_ok "standalone bundled PostgreSQL renders with only POSTGRES_PASSWORD" \
    POSTGRES_PASSWORD=pr-validation-value-only -- -f "$PROJECT_ROOT/docker-compose.postgres.yml"

# 6. The base file never references the bundled MinIO credentials.
if grep -q "MINIO_ROOT" "$PROJECT_ROOT/docker-compose.yml"; then
    print_result 1 "base Compose file has no MINIO_ROOT_* references"
else
    print_result 0 "base Compose file has no MINIO_ROOT_* references"
fi

# Final: run backend unit tests.
CAPABILITY_TOKEN_SECRET="${CAPABILITY_TOKEN_SECRET:-$(python -c 'import secrets; print(secrets.token_hex(32))')}" \
    MCP_TOKEN_ENCRYPTION_KEY="${MCP_TOKEN_ENCRYPTION_KEY:-$(python -c 'import secrets; print(secrets.token_urlsafe(32))')}" \
    ./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
