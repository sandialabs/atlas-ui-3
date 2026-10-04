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
"$engine" run -d --name "$name" \
    -e "CAPABILITY_TOKEN_SECRET=$capability_secret" \
    -e "MCP_TOKEN_ENCRYPTION_KEY=$mcp_key" \
    -e USE_MOCK_S3=true \
    -e FEATURE_PROXY_SECRET_ENABLED=false \
    "$image" >/dev/null
for attempt in $(seq 1 60); do
    status="$("$engine" inspect --format '{{.State.Health.Status}}' "$name")"
    if [ "$status" = healthy ]; then
        echo "Heartbeat HEALTHCHECK passed"
        exit 0
    fi
    if [ "$("$engine" inspect --format '{{.State.Running}}' "$name")" != true ]; then
        break
    fi
    sleep 2
done
"$engine" logs "$name"
echo "Runtime did not become healthy" >&2
exit 1
