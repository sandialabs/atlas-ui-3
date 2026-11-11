#!/usr/bin/env bash
# Exercise the hardened distroless image (Dockerfile.hardened). Works with Docker or Podman.
# ATLAS_TEST_IMAGE            image to test (default atlas-hardened:local)
# ATLAS_EXPECT_STDIO_MCP      space-separated stdio servers the image was built with
#                             (default empty: atlas/mcp must be absent)
set -euo pipefail
engine="${CONTAINER_ENGINE:-docker}"
image="${ATLAS_TEST_IMAGE:-atlas-hardened:local}"
export ATLAS_EXPECT_STDIO_MCP="${ATLAS_EXPECT_STDIO_MCP:-}"

"$engine" run --rm -i -e ATLAS_EXPECT_STDIO_MCP "$image" python - <<'PY'
import asyncio
import importlib.util
import json
import os
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

assert os.getuid() == 10001 and os.getgid() == 10001, (os.getuid(), os.getgid())
for executable in ("sh", "bash", "busybox", "apk", "sudo", "node", "npm", "npx",
                   "pip", "pip3", "uv", "uvx", "gcc", "curl", "wget"):
    assert shutil.which(executable) is None, executable
for path in ("/bin/sh", "/usr/bin/sh", "/bin/bash"):
    assert not Path(path).exists(), path
for module in ("pip", "ensurepip", "pytest"):
    assert importlib.util.find_spec(module) is None, module
for path in ("/app/test", "/app/docs", "/app/scripts", "/app/atlas/tests"):
    assert not Path(path).exists(), path
assert Path("/app/atlas/static/index.html").is_file()
assert Path("/app/alembic.ini").is_file() and Path("/app/alembic").is_dir()
metadata = distribution("atlas-chat").read_text("direct_url.json")
assert not json.loads(metadata or "{}").get("dir_info", {}).get("editable", False)
# The code and the venv are read-only to the runtime user; only data dirs are writable.
for path in ("/app/atlas", "/app/.venv", "/app/alembic"):
    assert not os.access(path, os.W_OK), path
for path in ("/data", "/app/logs", "/app/runtime/logs", "/app/runtime/feedback",
             "/app/runtime/uploads", "/app/config"):
    assert os.access(path, os.W_OK), path
import alembic  # noqa: F401  (migration init container)
import atlas.modules.chat_history.schema_migration  # noqa: F401

expected = os.environ.get("ATLAS_EXPECT_STDIO_MCP", "").split()
mcp_dir = Path("/app/atlas/mcp")
if not expected:
    assert not mcp_dir.exists(), "atlas/mcp must be absent by default"
else:
    present = {p.name for p in mcp_dir.iterdir() if p.is_dir()} - {"common", "__pycache__"}
    assert present == set(expected), (present, expected)
    from fastmcp import Client
    from fastmcp.client.transports import StdioTransport

    async def check(name):
        transport = StdioTransport(command=sys.executable, args=[f"mcp/{name}/main.py"],
                                   cwd="/app/atlas")
        async with Client(transport) as client:
            assert await client.list_tools(), name

    for name in expected:
        asyncio.run(check(name))
print("Hardened runtime surface checks passed")
PY

# The venv copy of the package must not carry unlisted stdio servers either.
"$engine" run --rm -i -e ATLAS_EXPECT_STDIO_MCP "$image" python -I - <<'PY'
import os
from pathlib import Path
import atlas
expected = set(os.environ.get("ATLAS_EXPECT_STDIO_MCP", "").split())
for root in Path("/app/.venv/lib").glob("python*/site-packages/atlas/mcp"):
    present = {p.name for p in root.iterdir() if p.is_dir()} - {"common", "__pycache__"}
    assert present == expected, (root, present)
print("Installed package stdio MCP set matches")
PY

# There is no shell to exec, and pip must not be importable.
if "$engine" run --rm --entrypoint /bin/sh "$image" -c true >/dev/null 2>&1; then
    echo "image contains a shell" >&2
    exit 1
fi
if "$engine" run --rm "$image" python -I -c "import pip" >/dev/null 2>&1; then
    echo "pip is importable in the hardened image" >&2
    exit 1
fi

name="atlas-hardened-check-$$"
cleanup() { "$engine" rm -f "$name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
capability_secret="$(openssl rand -hex 32)"
mcp_key="$(openssl rand -hex 32)"
proxy_secret="$(openssl rand -hex 32)"
"$engine" run -d --name "$name" \
    -e "CAPABILITY_TOKEN_SECRET=$capability_secret" \
    -e "MCP_TOKEN_ENCRYPTION_KEY=$mcp_key" \
    -e "PROXY_SECRET=$proxy_secret" \
    -e USE_MOCK_S3=true \
    "$image" >/dev/null
healthy=false
# Rootless Podman without a systemd timer never runs the periodic probe;
# `podman healthcheck run` executes the image's HEALTHCHECK on demand.
can_run_probe=false
if "$engine" healthcheck --help >/dev/null 2>&1; then can_run_probe=true; fi
for attempt in $(seq 1 60); do
    if [ "$can_run_probe" = true ]; then "$engine" healthcheck run "$name" >/dev/null 2>&1 || true; fi
    status="$("$engine" inspect --format '{{.State.Health.Status}}' "$name")"
    if [ "$status" = healthy ]; then
        echo "Heartbeat HEALTHCHECK passed"
        healthy=true
        break
    fi
    if [ "$("$engine" inspect --format '{{.State.Running}}' "$name")" != true ]; then
        break
    fi
    sleep 2
done
if [ "$healthy" != true ]; then
    "$engine" logs "$name"
    echo "Runtime did not become healthy" >&2
    exit 1
fi

"$engine" exec -e "PROXY_SECRET=$proxy_secret" "$name" python -c '
import os
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path, headers=None):
    request = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


assert get("/api/heartbeat") == 200, "heartbeat must stay unauthenticated"
assert get("/api/config/shell") == 401, "missing proxy secret must be rejected"
assert get(
    "/api/config/shell",
    {
        "X-Proxy-Secret": os.environ["PROXY_SECRET"],
        "X-User-Email": "test@test.com",
    },
) == 200, "proxy-authenticated request should succeed"
print("Proxy-secret enforcement checked: 401 without header, 200 with header")
'
