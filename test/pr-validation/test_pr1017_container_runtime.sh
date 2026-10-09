#!/usr/bin/env bash
# Exercise the built image for PR #1017 (issue #1011). Works with Docker or Podman.
set -euo pipefail
engine="${CONTAINER_ENGINE:-docker}"
image="${ATLAS_TEST_IMAGE:-atlas-1011:validation}"

"$engine" run --rm -i "$image" python - <<'PY'
import asyncio
import importlib.util
import json
import os
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

assert os.getuid() != 0
for executable in ("sudo", "node", "npm", "npx", "pip", "pip3", "uv", "uvx", "gcc"):
    assert shutil.which(executable) is None, executable
for module in ("pip", "ensurepip", "pytest"):
    assert importlib.util.find_spec(module) is None, module
for path in ("/app/test", "/app/docs", "/app/scripts", "/app/atlas/tests"):
    assert not Path(path).exists(), path
assert Path("/app/atlas/static/index.html").is_file()
assert Path("/app/atlas/.env.example").is_file()
metadata = distribution("atlas-chat").read_text("direct_url.json")
assert not json.loads(metadata or "{}").get("dir_info", {}).get("editable", False)

# Representative extras must remain usable even though build tools are absent.
import matplotlib
import pandas
import pptx
import reportlab
import scipy
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

async def check_mcp():
    transport = StdioTransport(
        command=sys.executable,
        args=["mcp/calculator/main.py"],
        cwd="/app/atlas",
    )
    async with Client(transport) as client:
        assert await client.list_tools()

asyncio.run(check_mcp())
print("Runtime surface and bundled MCP checks passed")
PY

name="atlas-runtime-check-$$"
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
for attempt in $(seq 1 60); do
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

# Proxy-secret enforcement is on by default. A protected route must reject a
# request without the proxy header and accept one the proxy has authenticated,
# even though the unauthenticated /api/heartbeat healthcheck reports healthy.
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
