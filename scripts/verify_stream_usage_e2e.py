#!/usr/bin/env python3
"""Offline end-to-end smoke test for request_stream_usage.

Loads actual llmconfig.yml through ConfigManager, calls LiteLLM against a
loopback-only OpenAI-compatible HTTP SSE server (no provider keys or fees),
and checks both wire requests and Atlas's per-turn usage sink.

Run from the repository root with:
    python scripts/verify_stream_usage_e2e.py
"""

import asyncio
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import yaml

# This script is launched from the checkout root, not the scripts directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from atlas.modules.config.config_manager import ConfigManager  # noqa: E402
from atlas.modules.llm.litellm_caller import LiteLLMCaller  # noqa: E402
from atlas.modules.llm.usage_tracking import usage_scope  # noqa: E402


class _FakeCompletionEndpoint(BaseHTTPRequestHandler):
    """Minimal local SSE endpoint sufficient for LiteLLM's real HTTP caller."""

    received = []
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        assert self.path.endswith("/chat/completions"), self.path
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        type(self).received.append(request)

        def chunk(content, usage=None):
            event = {
                "id": "chatcmpl-local-only",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "synthetic",
                "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": "stop"}]
                if content else [],
            }
            if usage is not None:
                event["usage"] = usage
            return f"data: {json.dumps(event)}\n\n".encode()

        events = [chunk("local verification")]
        if request.get("stream_options", {}).get("include_usage"):
            events.append(chunk("", {"prompt_tokens": 17, "completion_tokens": 4, "total_tokens": 21}))
        events.append(b"data: [DONE]\n\n")

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        for item in events:
            self.wfile.write(item)
            self.wfile.flush()
        self.close_connection = True

    def log_message(self, format, *args):
        pass


async def _run(base_url: str, temp_dir: Path) -> None:
    config_dir = temp_dir / "config"
    config_dir.mkdir()
    (config_dir / "llmconfig.yml").write_text(
        yaml.safe_dump({
            "models": {
                "usage-opted-in": {
                    "model_name": "synthetic",
                    "model_url": base_url,
                    "api_key": "local-only-not-a-provider-key",
                    "request_stream_usage": True,
                    "max_tokens": 24,
                },
                "usage-default-off": {
                    "model_name": "synthetic",
                    "model_url": base_url,
                    "api_key": "local-only-not-a-provider-key",
                    "max_tokens": 24,
                },
            }
        }), encoding="utf-8"
    )
    # Read the real YAML through Atlas's production config loader.
    manager = ConfigManager(atlas_root=temp_dir / "atlas")
    manager._app_settings = SimpleNamespace(
        app_config_dir=str(config_dir),
        llm_config_file="llmconfig.yml",
        mcp_config_file="mcp.json",
        rag_sources_config_file="rag-sources.json",
        file_extractors_config_file="file-extractors.json",
        hooks_config_file="hooks.json",
        model_fields_set={"app_config_dir"},
    )
    config = manager.llm_config
    assert config.models["usage-opted-in"].request_stream_usage is True
    assert config.models["usage-default-off"].request_stream_usage is False

    caller = LiteLLMCaller(llm_config=config)
    messages = [{"role": "user", "content": "synthetic smoke test"}]
    with usage_scope() as sink:
        tokens = [token async for token in caller.stream_plain("usage-opted-in", messages)]
    assert "".join(tokens) == "local verification", tokens
    assert (sink.prompt_tokens, sink.completion_tokens, sink.calls) == (17, 4, 1), sink

    # The actual HTTP request should have the opt-in bit.
    assert _FakeCompletionEndpoint.received[0]["stream_options"] == {"include_usage": True}

    tools = [{
        "type": "function",
        "function": {
            "name": "noop",
            "description": "does nothing",
            "parameters": {"type": "object", "properties": {}},
        },
    }]
    with usage_scope() as tool_sink:
        items = [
            item async for item in caller.stream_with_tools(
                "usage-opted-in", messages, tools,
            )
        ]
    assert "local verification" in items, items
    assert (tool_sink.prompt_tokens, tool_sink.completion_tokens, tool_sink.calls) == (17, 4, 1)
    assert _FakeCompletionEndpoint.received[1]["stream_options"] == {"include_usage": True}
    assert _FakeCompletionEndpoint.received[1].get("tools")

    with usage_scope():
        tokens = [token async for token in caller.stream_plain("usage-default-off", messages)]
    assert "".join(tokens) == "local verification"
    assert "stream_options" not in _FakeCompletionEndpoint.received[2]
    print("PASS: real YAML load -> LiteLLM HTTP SSE -> plain/tool usage sink, opt-in + default-off")


def main():
    with tempfile.TemporaryDirectory(prefix="atlas-stream-usage-") as temp_dir:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeCompletionEndpoint)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            asyncio.run(_run(f"http://127.0.0.1:{server.server_port}/openai/v1", Path(temp_dir)))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
