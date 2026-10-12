#!/bin/bash
# Render every supported Compose backend combination with the real Compose
# CLI (issue #1052). No Python environment needed, so CI can run this on the
# host while the hermetic test image (no Docker CLI) skips those pytest cases.
#
# Checks both fail-closed directions: missing or empty credentials of an
# included backend are rejected with a clear message, and credentials of a
# backend that is not included are never requested. When a container daemon
# is available, the bundled stacks are also started for real and health
# checked before being torn down.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

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

COMPOSE=""
if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE="docker-compose"
fi
if [ -z "$COMPOSE" ]; then
    echo "FAILED: Docker Compose is required to validate the Compose combinations"
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
POSTGRES_PASSWORD=pr-validation-value-only
FILES_APP=(-f "$PROJECT_ROOT/docker-compose.yml")
FILES_MINIO=("${FILES_APP[@]}" -f "$PROJECT_ROOT/docker-compose.minio.yml")
FILES_POSTGRES=("${FILES_APP[@]}" -f "$PROJECT_ROOT/docker-compose.postgres.yml")
FILES_ALL=("${FILES_MINIO[@]}" -f "$PROJECT_ROOT/docker-compose.postgres.yml")

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

# render_to <outfile> [env assignments...] -- [compose -f files...]
render_to() {
    local outfile="$1"; shift
    local assignments=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do assignments+=("$1"); shift; done
    shift # discard --
    "${CLEAN_ENV[@]}" "${assignments[@]}" $COMPOSE --env-file /dev/null "$@" config >"$outfile" 2>&1
}

# expect_render_ok <label> [env assignments...] -- [compose -f files...]
expect_render_ok() {
    local label="$1"; shift
    local assignments=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do assignments+=("$1"); shift; done
    shift # discard --
    if "${CLEAN_ENV[@]}" "${assignments[@]}" $COMPOSE --env-file /dev/null "$@" config >/dev/null 2>&1; then
        print_result 0 "$label"
    else
        print_result 1 "$label"
        "${CLEAN_ENV[@]}" "${assignments[@]}" $COMPOSE --env-file /dev/null "$@" config 2>&1 || true
    fi
}

# expect_render_rejected <label> <expected-substring> [env assignments...] -- [compose -f files...]
expect_render_rejected() {
    local label="$1" expected="$2"; shift 2
    local assignments=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do assignments+=("$1"); shift; done
    shift # discard --
    local out status=0
    out=$("${CLEAN_ENV[@]}" "${assignments[@]}" $COMPOSE --env-file /dev/null "$@" config 2>&1) || status=$?
    if [ "$status" -ne 0 ] && printf '%s' "$out" | grep -q "$expected"; then
        print_result 0 "$label"
    else
        print_result 1 "$label"
        printf '%s\n' "$out"
    fi
}

# expect_render_contains <label> <expected-substring> [env assignments...] -- [compose -f files...]
expect_render_contains() {
    local label="$1" expected="$2"; shift 2
    local assignments=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do assignments+=("$1"); shift; done
    shift # discard --
    local out="$WORK_DIR/render-$PASSED-$FAILED.yml" status=0
    render_to "$out" "${assignments[@]}" -- "$@" || status=$?
    if [ "$status" -eq 0 ] && grep -q "$expected" "$out"; then
        print_result 0 "$label"
    else
        print_result 1 "$label"
        cat "$out"
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
expect_render_contains "bundled MinIO root password equals the application S3 secret" \
    "MINIO_ROOT_PASSWORD: pr-validation-secret-key" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" -- "${FILES_MINIO[@]}"

# 5. Bundled PostgreSQL: required only when its file is included, and the
#    self-contained file also renders alone for a host-run Atlas.
expect_render_ok "bundled PostgreSQL stack renders with POSTGRES_PASSWORD" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" -- "${FILES_POSTGRES[@]}"
expect_render_rejected "bundled PostgreSQL stack rejects missing POSTGRES_PASSWORD" "POSTGRES_PASSWORD" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" -- "${FILES_POSTGRES[@]}"
expect_render_rejected "full bundled stack rejects missing POSTGRES_PASSWORD" "POSTGRES_PASSWORD" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" -- "${FILES_ALL[@]}"
expect_render_ok "standalone bundled PostgreSQL renders with only POSTGRES_PASSWORD" \
    "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" -- -f "$PROJECT_ROOT/docker-compose.postgres.yml"

# 6. Every supported combination renders successfully with all credentials.
expect_render_ok "full bundled stack renders with every credential" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" -- "${FILES_ALL[@]}"

# 7. Host-oriented .env values cannot break the container paths: the DuckDB
#    URL is hard-coded to the absolute /data path, and the bundled MinIO file
#    pins the endpoint, bucket, and TLS setting.
expect_render_contains "container DuckDB path ignores a host-relative .env value" \
    "CHAT_HISTORY_DB_URL: duckdb:////data/chat_history.db" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" \
    CHAT_HISTORY_DB_URL=duckdb:///data/chat_history.db -- "${FILES_APP[@]}"
expect_render_contains "bundled MinIO pins the in-network S3 endpoint" \
    "S3_ENDPOINT: http://minio:9000" \
    "${APP_SECRETS[@]}" S3_ENDPOINT=http://localhost:9000 \
    S3_ACCESS_KEY=pr-validation-access-key S3_SECRET_KEY=pr-validation-secret-key -- "${FILES_MINIO[@]}"
expect_render_contains "bundled PostgreSQL builds the chat history URL from its password" \
    "CHAT_HISTORY_DB_URL: postgresql://atlas:$POSTGRES_PASSWORD@postgres:5432/atlas_chat_history" \
    "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" -- "${FILES_POSTGRES[@]}"

# 8. The base file never references the bundled MinIO credentials.
if grep -q "MINIO_ROOT" "$PROJECT_ROOT/docker-compose.yml"; then
    print_result 1 "base Compose file has no MINIO_ROOT_* references"
else
    print_result 0 "base Compose file has no MINIO_ROOT_* references"
fi

# 9. Real startup, when a container daemon is available: the bundled MinIO
#    becomes healthy and its initializer exits successfully, and the standalone
#    bundled PostgreSQL accepts connections. Uses an isolated project name so
#    an operator's real stack in the same directory is never touched.
if docker info >/dev/null 2>&1; then
    project="atlas-compose-check-$$"
    run_isolated() { "${CLEAN_ENV[@]}" "${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" $COMPOSE --env-file /dev/null -p "$project" "$@"; }

    if run_isolated "${FILES_MINIO[@]}" up -d minio minio-init &&
        run_isolated "${FILES_MINIO[@]}" wait minio-init >/dev/null 2>&1; then
        print_result 0 "bundled MinIO starts healthy and its initializer exits cleanly"
    else
        print_result 1 "bundled MinIO starts healthy and its initializer exits cleanly"
        run_isolated "${FILES_MINIO[@]}" logs minio minio-init 2>&1 || true
        run_isolated "${FILES_MINIO[@]}" ps -a 2>&1 || true
    fi
    run_isolated "${FILES_MINIO[@]}" down -v --remove-orphans >/dev/null 2>&1 || true

    if run_isolated -f "$PROJECT_ROOT/docker-compose.postgres.yml" up -d postgres; then
        pg_ready=0
        waited=0
        until run_isolated -f "$PROJECT_ROOT/docker-compose.postgres.yml" exec -T postgres pg_isready >/dev/null 2>&1; do
            if [ "$waited" -ge 60 ]; then
                pg_ready=1
                break
            fi
            sleep 2
            waited=$((waited + 2))
        done
        print_result "$pg_ready" "standalone bundled PostgreSQL starts and accepts connections"
    else
        print_result 1 "standalone bundled PostgreSQL starts and accepts connections"
    fi
    run_isolated -f "$PROJECT_ROOT/docker-compose.postgres.yml" down -v --remove-orphans >/dev/null 2>&1 || true
else
    echo "SKIP: no container daemon available; startup checks not run"
fi

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
