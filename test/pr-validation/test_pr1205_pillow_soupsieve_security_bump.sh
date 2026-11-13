#!/bin/bash
# PR #1205: Pillow and soupsieve security updates.
# Checks that uv.lock carries only patched versions (and no second, older
# soupsieve for Python 3.11.0-3.11.4), that the Python floor which keeps it
# that way is declared, that the lock matches pyproject.toml, and that the code
# paths using the two packages still work on the upgraded versions: the
# TIFF-to-PNG vision conversion, the image_demo MCP tool and the page
# extraction of the duckduckgo MCP server.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

PASSED=0
FAILED=0

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
if [ ! -f .venv/bin/activate ]; then
    echo "No .venv in $PROJECT_ROOT. Create it with: uv venv && uv pip install -e '.[dev,mcp-demos]'"
    exit 1
fi
source .venv/bin/activate
export PYTHONPATH="$PROJECT_ROOT"

python - <<'PY'
import sys
import tomllib

from packaging.specifiers import SpecifierSet
from packaging.version import Version

# soupsieve 4 needs Python >= 3.11.5. If the project or the lock admitted an
# older patch release, uv would lock a second, unpatched soupsieve for it.
with open("pyproject.toml", "rb") as handle:
    declared = tomllib.load(handle)["project"]["requires-python"]
with open("uv.lock", "rb") as handle:
    locked_for = tomllib.load(handle)["requires-python"]
print(f"requires-python: pyproject {declared}, uv.lock {locked_for}")
for spec in (declared, locked_for):
    if "3.11.4" in SpecifierSet(spec) or "3.11.5" not in SpecifierSet(spec):
        sys.exit(1)
PY
print_result $? "pyproject.toml and uv.lock exclude Python older than 3.11.5"

python - <<'PY'
import sys
import tomllib

from packaging.version import Version

minimum = {"pillow": Version("13.0.0"), "soupsieve": Version("4.0.0")}
with open("uv.lock", "rb") as handle:
    packages = tomllib.load(handle)["package"]
failed = False
for name, floor in minimum.items():
    locked = [Version(p["version"]) for p in packages if p["name"] == name]
    print(f"{name}: locked {[str(v) for v in locked]}, patched from {floor}")
    if not locked or any(version < floor for version in locked):
        failed = True
sys.exit(1 if failed else 0)
PY
print_result $? "uv.lock has only patched Pillow and soupsieve versions"

uv lock --check
print_result $? "uv.lock is in sync with pyproject.toml"

python - <<'PY'
import sys
from importlib.metadata import version

from packaging.version import Version

ok = Version(version("pillow")) >= Version("13.0.0") and Version(version("soupsieve")) >= Version("4.0.0")
print(f"installed: pillow {version('pillow')}, soupsieve {version('soupsieve')}")
if not ok:
    print("The venv predates this change; reinstall with: uv pip install -e '.[dev,mcp-demos]'")
sys.exit(0 if ok else 1)
PY
print_result $? "Installed Pillow and soupsieve are the patched versions"

python - <<'PY'
import base64
from io import BytesIO

from PIL import Image

from atlas.application.chat.utilities.file_processor import _convert_tiff_to_png_b64

for mode, size in (("RGB", (32, 16)), ("I;16", (32, 16)), ("P", (8, 8))):
    source = BytesIO()
    Image.new(mode, size).save(source, format="TIFF")
    png = base64.b64decode(_convert_tiff_to_png_b64(base64.b64encode(source.getvalue()).decode()))
    with Image.open(BytesIO(png)) as converted:
        assert converted.format == "PNG" and converted.size == size, (mode, converted.format, converted.size)
PY
print_result $? "TIFF uploads (RGB, 16-bit, palette) convert to PNG for vision input"

(cd atlas/mcp/image_demo && python - <<'PY'
import asyncio
import base64
from io import BytesIO

from fastmcp import Client
from PIL import Image

import main


async def call_tool():
    async with Client(main.mcp) as client:
        return await client.call_tool("generate_test_image", {})


images = [block for block in asyncio.run(call_tool()).content if block.type == "image"]
with Image.open(BytesIO(base64.b64decode(images[0].data))) as image:
    assert image.format == "PNG" and image.size == (400, 300), (image.format, image.size)
PY
)
print_result $? "image_demo MCP tool renders an image"

python - <<'PY'
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

os.environ["NO_PROXY"] = "127.0.0.1"

from atlas.mcp.duckduckgo.main import get_page_content

PAGE = b"""<html><head><title>Atlas</title><style>p { color: red }</style>
<script>var hidden = 1;</script></head>
<body>
<h1>Heading</h1>
<p>First  paragraph</p>
<p>Second</p>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


server = HTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    text = get_page_content(f"http://127.0.0.1:{server.server_port}/")
finally:
    server.shutdown()
assert text == "Atlas\nHeading\nFirst\nparagraph\nSecond", repr(text)
PY
print_result $? "duckduckgo server extracts page text from a served HTML page"

python - <<'PY'
from bs4 import BeautifulSoup

html = "<html><body><nav>skip</nav><main><p class='lead'>Hello</p><p>World</p><a href='/x'>link</a></main></body></html>"
soup = BeautifulSoup(html, "html.parser")
assert [p.get_text() for p in soup.select("main > p")] == ["Hello", "World"]
assert soup.select_one("p.lead ~ a[href]")["href"] == "/x"
assert len(soup.select("body :is(p, a):not(.lead)")) == 2
PY
print_result $? "BeautifulSoup CSS selectors work on soupsieve 4"

python -m pytest atlas/tests/test_vision_image_support.py -q -p no:cacheprovider > /dev/null 2>&1
print_result $? "Vision image tests"

# Final: run backend unit tests
./test/run_tests.sh backend > /dev/null 2>&1
print_result $? "Backend unit tests"

echo ""
echo "Passed: $PASSED | Failed: $FAILED"
[ $FAILED -eq 0 ] && exit 0 || exit 1
