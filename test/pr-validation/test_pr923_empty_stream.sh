#!/bin/bash
# Exercise real HTTP streaming and timeout handling against a local provider.
set -euo pipefail
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$PROJECT_ROOT"
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi
export PYTHONPATH="$PROJECT_ROOT"
export LLM_MAX_RETRIES=1
export LLM_REQUEST_TIMEOUT_SECONDS=0.2

python - <<'PY'
import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from atlas.domain.errors import LLMEmptyStreamError, LLMTimeoutError
from atlas.modules.config.models import LLMConfig, ModelConfig
from atlas.modules.llm.litellm_caller import LiteLLMCaller


class Provider(BaseHTTPRequestHandler):
    mode = "recover"
    requests = 0

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        Provider.requests += 1
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.flush()
        if self.mode == "timeout":
            time.sleep(1)
            return
        if self.mode == "empty" or self.requests == 1:
            return
        chunk = {
            "id": "test", "object": "chat.completion.chunk",
            "created": 0, "model": "test",
            "choices": [{"index": 0, "delta": {"content": "recovered"}}],
        }
        self.wfile.write(f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode())
        self.wfile.flush()


async def main(port):
    caller = LiteLLMCaller(llm_config=LLMConfig(models={"test": ModelConfig(
        model_name="openai/test",
        model_url=f"http://127.0.0.1:{port}/v1",
        api_key="local-test-placeholder",
    )}))
    messages = [{"role": "user", "content": "hello"}]
    tools = [{"type": "function", "function": {
        "name": "test_tool", "parameters": {"type": "object", "properties": {}},
    }}]
    for with_tools in (False, True):
        for mode in ("recover", "empty", "timeout"):
            Provider.mode, Provider.requests = mode, 0
            stream = (
                caller.stream_with_tools("test", messages, tools)
                if with_tools else caller.stream_plain("test", messages)
            )
            started = time.monotonic()
            try:
                items = [item async for item in stream]
            except (LLMEmptyStreamError, LLMTimeoutError) as exc:
                expected = LLMEmptyStreamError if mode == "empty" else LLMTimeoutError
                assert isinstance(exc, expected), (mode, exc)
                assert mode != "recover"
            else:
                assert mode == "recover", (mode, items)
                assert [item for item in items if isinstance(item, str)] == ["recovered"]
            assert Provider.requests == 2, (mode, Provider.requests)
            assert time.monotonic() - started < 10
            print(f"PASS: tools={with_tools}, mode={mode}, requests={Provider.requests}")


server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    asyncio.run(main(server.server_port))
finally:
    server.shutdown()
    server.server_close()
PY
