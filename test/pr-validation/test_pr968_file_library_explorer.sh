#!/bin/bash
# Test script for the file-library explorer (issue #968).
#
# Test plan:
# - GET /api/files stays a JSON list and reports X-Total-Count.
# - limit/offset return one page, newest first, against the mock storage.
# - category and q narrow that page.
# - An unknown category is HTTP 400.
# - The file-library unit tests pass.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

PASSED=0
FAILED=0

print_header() {
    echo ""
    echo "=========================================="
    echo "$1"
    echo "=========================================="
}

print_result() {
    if [ "$1" -eq 0 ]; then
        echo -e "${GREEN}PASSED${NC}: $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}FAILED${NC}: $2"
        FAILED=$((FAILED + 1))
    fi
}

print_header "File library explorer"

cd "$PROJECT_ROOT"

if [ -f "$PROJECT_ROOT/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.venv/bin/activate"
fi

export PROJECT_ROOT

# A shared editable install can point at another checkout. Point it at this
# tree for this process only; the finder file on disk is left alone.
REMAP_PY='
import os, pathlib
root = pathlib.Path(os.environ["PROJECT_ROOT"]).resolve()
try:
    import __editable___atlas_chat_0_10_0_finder as finder
except ImportError:
    finder = None
if finder is not None and "atlas" in getattr(finder, "MAPPING", {}):
    current = pathlib.Path(finder.MAPPING["atlas"]).resolve().parent
    if current != root:
        old, new = str(current), str(root)
        finder.MAPPING = {k: v.replace(old, new) for k, v in finder.MAPPING.items()}
        finder.NAMESPACES = {
            k: [p.replace(old, new) for p in paths]
            for k, paths in finder.NAMESPACES.items()
        }
'

print_header "1. Paged GET /api/files against mock storage"
python - <<PY
$REMAP_PY
import base64, os, sys, tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.environ["PROJECT_ROOT"])
sys.path.insert(0, os.path.join(os.environ["PROJECT_ROOT"], "atlas"))

root = Path(tempfile.mkdtemp(prefix="atlas-pr968-"))
os.environ["MOCK_S3_ROOT"] = str(root)

from atlas.infrastructure.app_factory import app_factory
from atlas.modules.config import config_manager
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient
from main import app
from starlette.testclient import TestClient

user = config_manager.app_settings.test_user
storage = MockS3StorageClient(s3_bucket_name="atlas-pr968")
app_factory.file_storage = storage

import asyncio

async def seed():
    stored = []
    for name, source in (("notes.txt", "user"), ("photo.png", "user"), ("sheet.csv", "tool")):
        item = await storage.upload_file(
            user_email=user,
            filename=name,
            content_base64=base64.b64encode(b"x").decode(),
            content_type="application/octet-stream",
            source_type=source,
        )
        stored.append(item)
    stamps = {
        "notes.txt": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "photo.png": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "sheet.csv": datetime(2026, 2, 1, tzinfo=timezone.utc),
    }
    for item in stored:
        path = root / "atlas-pr968" / item["key"]
        when = stamps[item["filename"]]
        os.utime(path, (when.timestamp(), when.timestamp()))

asyncio.run(seed())

client = TestClient(app)
headers = {"X-User-Email": user}

page = client.get("/api/files", params={"limit": 1, "offset": 0}, headers=headers)
assert page.status_code == 200, page.text
assert page.headers["x-total-count"] == "3", page.headers
body = page.json()
assert isinstance(body, list) and len(body) == 1
assert body[0]["filename"] == "photo.png", body

nxt = client.get("/api/files", params={"limit": 1, "offset": 1}, headers=headers)
assert nxt.status_code == 200
assert nxt.json()[0]["filename"] == "sheet.csv"

images = client.get("/api/files", params={"category": "image"}, headers=headers)
assert images.status_code == 200
assert images.headers["x-total-count"] == "1"
assert images.json()[0]["filename"] == "photo.png"

found = client.get("/api/files", params={"q": "notes"}, headers=headers)
assert found.status_code == 200
assert found.headers["x-total-count"] == "1"
assert found.json()[0]["filename"] == "notes.txt"

bad = client.get("/api/files", params={"category": "nope"}, headers=headers)
assert bad.status_code == 400, bad.status_code

print("paged list ok")
PY
print_result $? "Paged GET /api/files (total, offset, category, q, 400)"

print_header "2. File library unit tests"
python - <<PY
$REMAP_PY
import pytest
raise SystemExit(pytest.main([
    "atlas/tests/test_file_library_explorer.py",
    "atlas/tests/test_files_last_modified.py",
    "-q",
    "--tb=short",
]))
PY
print_result $? "file library pytest"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ "$FAILED" -eq 0 ] && exit 0 || exit 1
