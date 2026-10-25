#!/bin/bash
# Validation for issue #991: standardize on the psycopg (v3) driver for PostgreSQL.
# A bare postgresql:// URL must reach SQLAlchemy as postgresql+psycopg:// in both the
# app (chat history engine) and the Alembic migrations, so the driver no longer
# depends on the installed SQLAlchemy version. URLs that already name a driver
# (postgresql+psycopg2://, postgresql+asyncpg://) must pass through untouched.

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
source .venv/bin/activate

# 1. Chat history: get_engine rewrites a bare CHAT_HISTORY_DB_URL to the psycopg (v3)
#    driver and preserves explicit driver URLs. create_engine is lazy, so no database
#    server is needed.
PYTHONPATH="$PROJECT_ROOT" python - <<'EOF'
from atlas.modules.chat_history import database

engine = database.get_engine("postgresql://atlas:pw@db.example.com:5432/atlas_chat_history")
assert engine.dialect.name == "postgresql", engine.dialect.name
assert engine.dialect.driver == "psycopg", engine.dialect.driver
assert str(engine.url).startswith("postgresql+psycopg://"), str(engine.url)
database.reset_engine()

engine = database.get_engine("postgresql+psycopg2://atlas:pw@db.example.com:5432/atlas_chat_history")
assert engine.dialect.driver == "psycopg2", engine.dialect.driver
database.reset_engine()
print("chat-history driver checks OK")
EOF
print_result $? "get_engine: bare postgresql:// -> psycopg v3, explicit drivers preserved"

# 2. Alembic: env.py normalizes the URL before handing it to SQLAlchemy; mirror its
#    online engine_from_config path for both the DB_* parts source and an explicit
#    driver URL.
PYTHONPATH="$PROJECT_ROOT" python - <<'EOF'
from sqlalchemy import engine_from_config
from atlas.modules.config.config_manager import (
    build_db_url_from_parts,
    normalize_postgres_driver_url,
)

url = build_db_url_from_parts(
    db_driver="postgresql",
    db_host="db.example.com",
    db_port="5432",
    db_name="atlas_chat_history",
    db_user="atlas",
    db_password="pw",
)
engine = engine_from_config({"sqlalchemy.url": normalize_postgres_driver_url(url)})
assert engine.dialect.name == "postgresql", engine.dialect.name
assert engine.dialect.driver == "psycopg", engine.dialect.driver

engine = engine_from_config({"sqlalchemy.url": "postgresql+psycopg2://atlas:pw@db/atlas"})
assert engine.dialect.driver == "psycopg2", engine.dialect.driver
print("alembic engine_from_config checks OK")
EOF
print_result $? "alembic engine path: normalized URL resolves to psycopg v3"

# 3. Alembic offline migrations run end to end against a bare postgresql:// URL,
#    both via the DB_* parts (default DB_DRIVER=postgresql) and via an explicit
#    CHAT_HISTORY_DB_URL. Offline mode generates SQL without connecting.
SQL_FILE="$(mktemp)"
DB_HOST=db.example.com DB_PORT=5432 DB_NAME=atlas_chat_history DB_USER=atlas DB_PASSWORD=pw \
    alembic upgrade head --sql > "$SQL_FILE" 2>&1
print_result $? "alembic upgrade --sql with DB_* parts (bare postgresql://)"
grep -q "CREATE TABLE" "$SQL_FILE"
print_result $? "generated migration SQL contains DDL"

SQL_FILE2="$(mktemp)"
CHAT_HISTORY_DB_URL="postgresql://atlas:pw@db.example.com:5432/atlas_chat_history" \
    alembic upgrade head --sql > "$SQL_FILE2" 2>&1
print_result $? "alembic upgrade --sql with explicit bare CHAT_HISTORY_DB_URL"
grep -q "CREATE TABLE" "$SQL_FILE2"
print_result $? "generated migration SQL contains DDL"
rm -f "$SQL_FILE" "$SQL_FILE2"

# Final: run backend unit tests
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

# Summary
echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
