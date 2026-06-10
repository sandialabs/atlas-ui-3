#!/usr/bin/env bash
# PR #549 - OpenTelemetry spans for S3 / file-storage operations
#
# Exercises the four new storage spans end-to-end:
#   1. Setup phase verifies the new attributes are wired into the clients.
#   2. Runtime phase drives a real upload / download / list / delete
#      through MockS3StorageClient against a live JSONLSpanExporter and
#      inspects the spans.jsonl output.
#   3. Negative-control phase verifies that raw keys / filenames / emails
#      never appear in span attributes, and that access_denied survives
#      the raised exception.
#   4. Runs the dedicated test module.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
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
echo "PR #549 Validation: OpenTelemetry spans for S3 / file storage"
echo "================================================================"

# 1. Imports still wire cleanly
python -c "
from atlas.modules.file_storage.s3_client import S3StorageClient
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient
from atlas.core.telemetry import start_span, set_attrs, hash_short, preview, safe_label
" > /dev/null 2>&1
print_result $? "S3 storage clients + telemetry helpers import cleanly"

# 2. Docs updated with the four new span tables
for span_name in "file.upload" "file.download" "storage.list" "storage.delete"; do
    grep -q "\`${span_name}\`" "$PROJECT_ROOT/docs/telemetry/README.md"
    print_result $? "docs/telemetry/README.md documents ${span_name}"
done

# 3. analysis_example.py gained storage aggregations
grep -q "upload_volume_by_user" "$PROJECT_ROOT/docs/telemetry/analysis_example.py"
print_result $? "analysis_example.py gained upload_volume_by_user"
grep -q "storage_success_rate_by_backend" "$PROJECT_ROOT/docs/telemetry/analysis_example.py"
print_result $? "analysis_example.py gained storage_success_rate_by_backend"

# 4. End-to-end: drive all four storage ops through MockS3 with a real
#    JSONLSpanExporter and assert the spans.jsonl output.
export APP_LOG_DIR="$WORK_DIR/logs"
export ATLAS_TELEMETRY_HMAC_SECRET="pr549-validation-secret"
mkdir -p "$APP_LOG_DIR"

python - <<'PYEOF' > "$WORK_DIR/e2e.log" 2>&1
import asyncio
import base64
import json
import os
import stat
from pathlib import Path

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from atlas.core.otel_config import JSONLSpanExporter
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient

spans_file = Path(os.environ["APP_LOG_DIR"]) / "spans.jsonl"
provider = TracerProvider(resource=Resource.create({"service.name": "pr549"}))
provider.add_span_processor(SimpleSpanProcessor(JSONLSpanExporter(spans_file)))
trace.set_tracer_provider(provider)

USER = "e2e-storage@example.com"
SECRET_FILENAME = "TOP_SECRET_REPORT_Q4.pdf"
SECRET_CONTENT = b"CLASSIFIED_PAYLOAD_DO_NOT_LEAK"

async def drive():
    client = MockS3StorageClient(s3_bucket_name="atlas-pr549")

    # upload
    up = await client.upload_file(
        user_email=USER,
        filename=SECRET_FILENAME,
        content_base64=base64.b64encode(SECRET_CONTENT).decode(),
        content_type="application/pdf",
        source_type="user",
    )
    key = up["key"]

    # download
    got = await client.get_file(USER, key)
    assert got is not None

    # list
    files = await client.list_files(USER, file_type="user", limit=50)
    assert any(f["key"] == key for f in files)

    # delete
    assert await client.delete_file(USER, key) is True

    # access_denied path on download
    try:
        await client.get_file("attacker@example.com", key)
    except Exception:
        pass

    # access_denied path on delete
    try:
        await client.delete_file("attacker@example.com", key)
    except Exception:
        pass

asyncio.run(drive())
provider.shutdown()

assert spans_file.exists(), f"spans file missing: {spans_file}"
records = [json.loads(l) for l in spans_file.read_text().splitlines() if l.strip()]

# One span for each operation
names = [r["name"] for r in records]
for expected in ("file.upload", "file.download", "storage.list", "storage.delete"):
    assert expected in names, f"missing span {expected}; saw {names}"

# Attribute contract — pick one of each type
def first(name):
    return next(r for r in records if r["name"] == name)

up_attrs = first("file.upload")["attributes"]
assert up_attrs["storage_backend"] == "mock"
assert up_attrs["success"] is True
assert up_attrs["source_type"] == "user"
assert up_attrs["category"] == "uploads"
assert up_attrs["content_type"] == "application/pdf"
assert up_attrs["file_size"] == len(SECRET_CONTENT)
assert len(up_attrs["user_hash"]) == 16
assert len(up_attrs["key_hash"]) == 16
assert isinstance(up_attrs["duration_ms"], int)

dl_attrs = first("file.download")["attributes"]
assert dl_attrs["storage_backend"] == "mock"
# The access_denied download should be present too
denied = [r for r in records if r["name"] == "file.download" and r["attributes"].get("access_denied")]
assert denied, "expected an access_denied file.download span"
assert denied[-1]["attributes"]["success"] is False

ls_attrs = first("storage.list")["attributes"]
assert ls_attrs["storage_backend"] == "mock"
assert ls_attrs["success"] is True
assert ls_attrs["file_type"] == "user"
assert isinstance(ls_attrs["num_results"], int)

# storage.delete: the first one succeeded, the access_denied one must be here too.
del_spans = [r for r in records if r["name"] == "storage.delete"]
assert any(s["attributes"].get("success") is True for s in del_spans)
denied_del = [s for s in del_spans if s["attributes"].get("access_denied")]
assert denied_del, "expected an access_denied storage.delete span"

# NEGATIVE CONTROLS: raw sensitive values must never appear anywhere in the
# attribute strings of any storage span.
storage_spans = [r for r in records
                 if r["name"] in ("file.upload", "file.download", "storage.list", "storage.delete")]
for r in storage_spans:
    flat = json.dumps(r["attributes"])
    assert USER not in flat, f"raw user email leaked in {r['name']} attrs: {flat}"
    assert "attacker@example.com" not in flat, (
        f"raw attacker email leaked in {r['name']} attrs: {flat}"
    )
    # Raw full key path (users/.../uploads/...) must not be present
    assert "users/" + USER not in flat, (
        f"raw key leaked in {r['name']} attrs: {flat}"
    )
    # Bucket name must not be on span attrs
    assert "atlas-pr549" not in flat, (
        f"raw bucket name leaked in {r['name']} attrs: {flat}"
    )
    # Raw content must not be there
    assert "CLASSIFIED_PAYLOAD_DO_NOT_LEAK" not in flat

# Filename may appear as a sanitized label on file.upload/file.download —
# that's intentional. But it must NEVER appear on storage.list/delete spans.
for r in storage_spans:
    if r["name"] in ("storage.list", "storage.delete"):
        flat = json.dumps(r["attributes"])
        assert SECRET_FILENAME not in flat, (
            f"filename leaked on {r['name']}: {flat}"
        )

# spans.jsonl file permissions
mode = stat.S_IMODE(os.stat(spans_file).st_mode)
assert mode & 0o077 == 0, f"spans.jsonl mode {oct(mode)} is too permissive"

print("E2E_OK")
PYEOF

if grep -q "E2E_OK" "$WORK_DIR/e2e.log"; then
    print_result 0 "End-to-end storage spans emitted with correct attributes; no sensitive data leaked"
else
    print_result 1 "End-to-end storage spans emission"
    echo "--- e2e.log ---"
    cat "$WORK_DIR/e2e.log"
    echo "---------------"
fi

# 5. Focused test module
PYTHONPATH="$PROJECT_ROOT" python -m pytest atlas/tests/test_storage_spans.py -q > "$WORK_DIR/tests.log" 2>&1
if [ $? -eq 0 ]; then
    print_result 0 "atlas/tests/test_storage_spans.py passes"
else
    print_result 1 "atlas/tests/test_storage_spans.py"
    echo "--- tests.log ---"
    cat "$WORK_DIR/tests.log"
    echo "-----------------"
fi

echo
echo "================================================================"
echo "Results: $PASS passed, $FAIL failed"
echo "================================================================"
exit $FAIL
