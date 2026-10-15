"""End-to-end driver for PR #1067: LiteLLM request tags.

Stands up an OpenAI-compatible model endpoint that records the request headers
it receives, writes a temporary ``llmconfig.yml`` describing several models, and
drives the real Atlas ``LiteLLMCaller`` (which uses the real LiteLLM SDK over
HTTP) so the ``x-litellm-tags`` header is exercised on a genuine outbound
request -- not by inspecting kwargs in isolation.

Nothing inside Atlas is mocked; the stand-in is the model endpoint.
"""

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlparse

# Run from a script path, ``sys.path[0]`` is this fixture's directory, so an
# ``atlas`` installed elsewhere could shadow the checkout under test. Put the
# repository root first so the code being validated is the code that is run.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"PASSED: {label}")
    else:
        print(f"FAILED: {label} {detail}")
        FAILURES.append(label)


# -- A capturing OpenAI-compatible model endpoint ----------------------------

# Headers received, oldest first: (path, x-litellm-tags, x-litellm-customer-id).
CALLS = []


class ModelHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        CALLS.append({
            "path": urlparse(self.path).path,
            "tags": self.headers.get("x-litellm-tags"),
            "customer_id": self.headers.get("x-litellm-customer-id"),
        })
        streaming = False
        try:
            streaming = bool(json.loads(body or b"{}").get("stream"))
        except ValueError:
            # A non-JSON body cannot request streaming; treat it as a plain call.
            streaming = False
        if streaming:
            chunks = (
                'data: {"id":"c","object":"chat.completion.chunk","choices":'
                '[{"index":0,"delta":{"role":"assistant","content":"hello"}}]}\n\n'
                "data: [DONE]\n\n"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(chunks)))
            self.end_headers()
            self.wfile.write(chunks)
            return
        payload = json.dumps({
            "id": "chatcmpl-validation", "object": "chat.completion",
            "created": int(time.time()), "model": "test-model",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def serve(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    model = serve(ModelHandler)
    model_url = f"http://127.0.0.1:{model.server_port}/v1"
    print(f"Model endpoint on {model_url}")

    config_dir = Path(tempfile.mkdtemp(prefix="atlas-pr1067-"))
    (config_dir / "llmconfig.yml").write_text(f"""
models:
  tagged-list:
    model_name: openai/test-model
    model_url: {model_url}
    api_key: sk-test
    litellm_tags:
      - atlas
      - prod
  tagged-string:
    model_name: openai/test-model
    model_url: {model_url}
    api_key: sk-test
    litellm_tags: "atlas, prod"
  untagged:
    model_name: openai/test-model
    model_url: {model_url}
    api_key: sk-test
  explicit-wins:
    model_name: openai/test-model
    model_url: {model_url}
    api_key: sk-test
    litellm_tags: [atlas]
    extra_headers:
      x-litellm-tags: pinned
  tagged-customer:
    model_name: openai/test-model
    model_url: {model_url}
    api_key: sk-test
    litellm_tags: [atlas]
    pass_user_as_customer_id: true
""")
    (config_dir / "mcp.json").write_text("{}")

    os.environ.update({
        "APP_CONFIG_DIR": str(config_dir),
        "USE_MOCK_S3": "true",
        "FEATURE_CHAT_HISTORY_ENABLED": "false",
        "SKIP_AUTHORIZATION_CHECKS": "true",
        "ENVIRONMENT": "development",
        "CAPABILITY_TOKEN_SECRET": "",
        "DEBUG_MODE": "true",
    })

    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    messages = [{"role": "user", "content": "hello"}]

    def call_plain(model_name, user=None):
        return asyncio.run(LiteLLMCaller().call_plain(model_name, messages, user_email=user))

    def stream(model_name):
        async def _drain():
            client = LiteLLMCaller()
            async for _ in client.stream_plain(model_name, messages):
                pass
        asyncio.run(_drain())

    def call_with_tools(model_name):
        tools_schema = [{
            "type": "function",
            "function": {
                "name": "ping", "description": "ping",
                "parameters": {"type": "object", "properties": {}},
            },
        }]
        return asyncio.run(
            LiteLLMCaller().call_with_tools(model_name, messages, tools_schema)
        )

    CALLS.clear()
    call_plain("tagged-list")
    check("A tag list is sent as the comma-separated x-litellm-tags header",
          CALLS[-1]["tags"] == "atlas,prod", str(CALLS[-1]))

    call_plain("tagged-string")
    check("A comma-separated string tag is normalized and sent",
          CALLS[-1]["tags"] == "atlas,prod", str(CALLS[-1]))

    call_plain("untagged")
    check("A model with no litellm_tags sends no x-litellm-tags header",
          CALLS[-1]["tags"] is None, str(CALLS[-1]))

    call_plain("explicit-wins")
    check("An explicit x-litellm-tags in extra_headers wins",
          CALLS[-1]["tags"] == "pinned", str(CALLS[-1]))

    call_plain("tagged-customer", user="alice@example.com")
    last = CALLS[-1]
    check("Tags combine with the customer-id header",
          last["tags"] == "atlas" and last["customer_id"] == "alice@example.com",
          str(last))

    CALLS.clear()
    stream("tagged-list")
    check("The streaming path also sends x-litellm-tags",
          CALLS and CALLS[-1]["tags"] == "atlas,prod", str(CALLS[-1]) if CALLS else "no call")

    CALLS.clear()
    call_with_tools("tagged-list")
    check("The tool-carrying path also sends x-litellm-tags",
          CALLS and CALLS[-1]["tags"] == "atlas,prod", str(CALLS[-1]) if CALLS else "no call")

    if FAILURES:
        print(f"\nFAILED: {len(FAILURES)} check(s): {', '.join(FAILURES)}")
        return 1
    print("\nALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
