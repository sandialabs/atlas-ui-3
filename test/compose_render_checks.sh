#!/bin/bash
# Render every supported Compose backend combination with the real Compose
# CLI (issue #1052). No Python environment needed, so CI can run this on the
# host while the hermetic test image (no Docker CLI) skips the pytest matrix
# in atlas/tests/test_container_security.py, which this script mirrors.
#
# Checks both fail-closed directions: missing or empty credentials of an
# included backend are rejected with a clear message, and credentials of a
# backend that is not included are neither required nor leaked. When a
# container daemon is available, the bundled stacks are also started for real
# and health checked before being torn down.
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

COMBO_NAMES=(
    "application-only"
    "bundled-minio"
    "bundled-postgres"
    "bundled-minio-and-postgres"
)

files_for_combo() {
    case "$1" in
        application-only) printf '%s\0' "${FILES_APP[@]}" ;;
        bundled-minio) printf '%s\0' "${FILES_MINIO[@]}" ;;
        bundled-postgres) printf '%s\0' "${FILES_POSTGRES[@]}" ;;
        bundled-minio-and-postgres) printf '%s\0' "${FILES_ALL[@]}" ;;
        *) return 1 ;;
    esac
}

includes_postgres() {
    case "$1" in
        bundled-postgres | bundled-minio-and-postgres) return 0 ;;
        *) return 1 ;;
    esac
}

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
    local out="$WORK_DIR/contains.yml" status=0
    render_to "$out" "${assignments[@]}" -- "$@" || status=$?
    if [ "$status" -eq 0 ] && grep -q "$expected" "$out"; then
        print_result 0 "$label"
    else
        print_result 1 "$label"
        cat "$out"
    fi
}

# expect_render_without <label> <unexpected-substring> [env assignments...] -- [compose -f files...]
expect_render_without() {
    local label="$1" unexpected="$2"; shift 2
    local assignments=()
    while [ "$#" -gt 0 ] && [ "$1" != "--" ]; do assignments+=("$1"); shift; done
    shift # discard --
    local out="$WORK_DIR/without.yml" status=0
    render_to "$out" "${assignments[@]}" -- "$@" || status=$?
    if [ "$status" -eq 0 ] && ! grep -q "$unexpected" "$out"; then
        print_result 0 "$label"
    else
        print_result 1 "$label"
        cat "$out"
    fi
}

# 1. Full required-credential matrix, mirroring the pytest parametrization:
#    for every backend combination, each required credential must fail
#    configuration both when missing and when empty, with a message naming it.
ALL_SECRETS=("${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" "POSTGRES_PASSWORD=$POSTGRES_PASSWORD")
for combo in "${COMBO_NAMES[@]}"; do
    mapfile -d '' -t combo_files < <(files_for_combo "$combo")
    required=("${APP_SECRETS[@]}" "${S3_SETTINGS[@]}")
    if includes_postgres "$combo"; then
        required+=("POSTGRES_PASSWORD=$POSTGRES_PASSWORD")
    fi
    for assignment in "${required[@]}"; do
        secret="${assignment%%=*}"
        without_assignment=()
        for candidate in "${required[@]}"; do
            [ "$candidate" = "$assignment" ] || without_assignment+=("$candidate")
        done
        expect_render_rejected "$combo rejects missing $secret" "$secret" \
            "${without_assignment[@]}" -- "${combo_files[@]}"
        expect_render_rejected "$combo rejects empty $secret" "$secret" \
            "${required[@]}" "$secret=" -- "${combo_files[@]}"
    done
done

# 2. Credentials of backends that are not included are neither required nor
#    leaked into the rendered stack (acceptance criteria: DuckDB works without
#    POSTGRES_PASSWORD, external S3 works without MINIO_ROOT_PASSWORD).
expect_render_without "base stack ignores a set MINIO_ROOT_PASSWORD" "unused-sentinel" \
    "${ALL_SECRETS[@]}" MINIO_ROOT_PASSWORD=unused-sentinel -- "${FILES_APP[@]}"
expect_render_without "base stack ignores a set POSTGRES_PASSWORD" "unused-sentinel" \
    "${ALL_SECRETS[@]}" POSTGRES_PASSWORD=unused-sentinel -- "${FILES_APP[@]}"
expect_render_without "bundled MinIO stack ignores a set POSTGRES_PASSWORD" "unused-sentinel" \
    "${ALL_SECRETS[@]}" POSTGRES_PASSWORD=unused-sentinel -- "${FILES_MINIO[@]}"
expect_render_without "bundled PostgreSQL stack ignores a set MINIO_ROOT_PASSWORD" "unused-sentinel" \
    "${ALL_SECRETS[@]}" MINIO_ROOT_PASSWORD=unused-sentinel -- "${FILES_POSTGRES[@]}"

# 3. Every supported combination renders successfully with all credentials,
#    and each bundled file also renders alone (agent_start.sh starts them
#    individually for a host-run Atlas).
for combo in "${COMBO_NAMES[@]}"; do
    mapfile -d '' -t combo_files < <(files_for_combo "$combo")
    expect_render_ok "$combo renders with every credential" \
        "${ALL_SECRETS[@]}" -- "${combo_files[@]}"
done
expect_render_ok "standalone bundled PostgreSQL renders with only POSTGRES_PASSWORD" \
    "POSTGRES_PASSWORD=$POSTGRES_PASSWORD" -- -f "$PROJECT_ROOT/docker-compose.postgres.yml"
expect_render_ok "standalone bundled MinIO renders with only the S3 credential pair" \
    S3_ACCESS_KEY=pr-validation-access-key S3_SECRET_KEY=pr-validation-secret-key \
    -- -f "$PROJECT_ROOT/docker-compose.minio.yml"

# 4. Host-oriented .env values cannot break the container paths: the DuckDB
#    URL is hard-coded to the absolute /data path, and the bundled MinIO file
#    pins the endpoint, bucket, and TLS setting.
expect_render_contains "container DuckDB path ignores a host-relative .env value" \
    "CHAT_HISTORY_DB_URL: duckdb:////data/chat_history.db" \
    "${ALL_SECRETS[@]}" \
    CHAT_HISTORY_DB_URL=duckdb:///data/chat_history.db -- "${FILES_APP[@]}"
expect_render_contains "bundled MinIO pins the in-network S3 endpoint" \
    "S3_ENDPOINT: http://minio:9000" \
    "${ALL_SECRETS[@]}" S3_ENDPOINT=http://localhost:9000 -- "${FILES_MINIO[@]}"
expect_render_contains "bundled PostgreSQL builds the chat history URL from its password" \
    "CHAT_HISTORY_DB_URL: postgresql://atlas:$POSTGRES_PASSWORD@postgres:5432/atlas_chat_history" \
    "${ALL_SECRETS[@]}" -- "${FILES_POSTGRES[@]}"

# 5. The base file never references the bundled MinIO credentials.
if grep -q "MINIO_ROOT" "$PROJECT_ROOT/docker-compose.yml"; then
    print_result 1 "base Compose file has no MINIO_ROOT_* references"
else
    print_result 0 "base Compose file has no MINIO_ROOT_* references"
fi

# 6. Real startup, when a container daemon is available: the bundled MinIO
#    becomes healthy and its initializer exits successfully, and the standalone
#    bundled PostgreSQL accepts connections. Uses an isolated project name so
#    an operator's real stack in the same directory is never touched.
STACK_ENV=("${APP_SECRETS[@]}" "${S3_SETTINGS[@]}" "POSTGRES_PASSWORD=$POSTGRES_PASSWORD")
if docker info >/dev/null 2>&1; then
    project="atlas-compose-check-$$"
    run_isolated() { "${CLEAN_ENV[@]}" "${STACK_ENV[@]}" $COMPOSE --env-file /dev/null -p "$project" "$@"; }
    # Bound every blocking wait so a wedged container cannot hang CI.
    bounded() { if command -v timeout >/dev/null 2>&1; then timeout 180 "$@"; else "$@"; fi; }

    if run_isolated "${FILES_MINIO[@]}" up -d minio minio-init &&
        bounded "${CLEAN_ENV[@]}" "${STACK_ENV[@]}" $COMPOSE --env-file /dev/null -p "$project" \
            "${FILES_MINIO[@]}" wait minio-init >/dev/null 2>&1; then
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
