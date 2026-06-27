#!/usr/bin/env python3
"""End-to-end validation that LiteLLM uses the configured per-model API key.

Drives the REAL Atlas LLM code path against the external `llm-mock` service to
prove the full chain works for an OpenAI-looking model served by a private
gateway:

    LLMConfig (model_name="openai/gpt5.4", api_key="${GATEWAY_API_KEY}")
      -> LiteLLMCaller.call_plain(...)
      -> LiteLLMCaller._get_model_kwargs()    (resolves api_key + api_base)
      -> litellm.acompletion(api_key=..., api_base=mock/v1)
      -> llm-mock /v1/chat/completions        (external service)

The scenario Anthony cares about: a model whose name *looks* like OpenAI
(`openai/gpt5.4`) but is actually served by a private gateway, while a
conflicting `OPENAI_API_KEY` is present in the environment. The configured key
must win and the request must go to the mock, not real OpenAI.

The mock's /test/last-request endpoint is the source of truth: it records the
exact Authorization token and model name that reached the endpoint, so the
assertions verify what the LLM provider actually saw rather than trusting the
Atlas side.

Note: this driver deliberately never prints API-key material (not even masked).
The assertions below compare credentials internally and only emit booleans and
non-sensitive descriptions, so no secret value ever reaches stdout.

Requires the mock to be running; set MOCK_LLM_URL (default
http://127.0.0.1:8002). Exits non-zero if any scenario fails.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.request

# --- Conflicting OPENAI_API_KEY must exist BEFORE atlas/litellm import --------
# This is the key that must NOT be used: a real OpenAI key in the environment
# (e.g. loaded from .env) should never override an explicitly configured
# per-model gateway key. (These are throwaway fixture values, not real keys.)
CONFLICTING_OPENAI_KEY = "sk-real-openai-DO-NOT-USE-0000000000000000"
GATEWAY_KEY = "sk-gateway-configured-WINS-1111111111111111"
os.environ["OPENAI_API_KEY"] = CONFLICTING_OPENAI_KEY
os.environ["GATEWAY_API_KEY"] = GATEWAY_KEY

MOCK_URL = os.environ.get("MOCK_LLM_URL", "http://127.0.0.1:8002")

# An OpenAI-looking model name pointed at the mock gateway, not real OpenAI.
INTERNAL_MODEL = "gateway-gpt5"
OPENAI_LOOKING_MODEL_ID = "openai/gpt5.4"


def _http_get_json(path: str) -> dict:
    with urllib.request.urlopen(f"{MOCK_URL}{path}", timeout=10) as resp:
        return json.loads(resp.read().decode())


def _http_post_json(path: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{MOCK_URL}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


PASS = "\033[0;32mPASS\033[0m"
FAIL = "\033[0;31mFAIL\033[0m"
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    line = f"  [{status}] {label}"
    if detail:
        line += f" -- {detail}"
    print(line)
    if not condition:
        _failures.append(label)


async def main() -> int:
    from atlas.modules.config.config_manager import LLMConfig, ModelConfig
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    print("E2E: LLM configured-key wins over conflicting OPENAI_API_KEY")
    print(f"  mock         : {MOCK_URL}")
    print(f"  model id     : {OPENAI_LOOKING_MODEL_ID} (looks like OpenAI, served by mock)")
    print("  configured key must be used; OPENAI_API_KEY must be ignored")
    print()

    # Confirm the mock is reachable.
    health = _http_get_json("/health")
    check("mock is healthy", health.get("status") == "healthy", str(health.get("status")))

    # Start from a clean request log.
    _http_post_json("/test/reset-log", {})

    llm_config = LLMConfig(
        models={
            INTERNAL_MODEL: ModelConfig(
                model_name=OPENAI_LOOKING_MODEL_ID,
                model_url=f"{MOCK_URL}/v1",
                api_key="${GATEWAY_API_KEY}",
            )
        }
    )
    caller = LiteLLMCaller(llm_config, debug_mode=True)

    # --- Real round trip through litellm to the mock --------------------------
    content = await caller.call_plain(
        INTERNAL_MODEL,
        messages=[{"role": "user", "content": "hello from the e2e gateway test"}],
    )
    check("call_plain returned content", bool(content), f"{len(content or '')} chars")

    # --- Source of truth: what did the mock actually receive? -----------------
    # NOTE: credential values are compared internally only; never printed.
    last = _http_get_json("/test/last-request")
    check("mock received a request", last.get("received") is True)

    received_token = last.get("authorization_token")
    check(
        "request carried an Authorization bearer token",
        last.get("had_authorization") is True,
    )
    check(
        "mock received the CONFIGURED gateway key",
        received_token == GATEWAY_KEY,
    )
    check(
        "mock did NOT receive the conflicting OPENAI_API_KEY",
        received_token != CONFLICTING_OPENAI_KEY,
    )
    check(
        "request reached the mock with the OpenAI-looking model name",
        last.get("model") == "gpt5.4",
        f"model={last.get('model')!r}",
    )

    # --- The env var must remain under admin control, never coerced ----------
    check(
        "OPENAI_API_KEY env var left unchanged (no coercion)",
        os.environ.get("OPENAI_API_KEY") == CONFLICTING_OPENAI_KEY,
    )

    # --- Confirm the assembled kwargs point at the mock, not real OpenAI -----
    kwargs = caller._get_model_kwargs(INTERNAL_MODEL)
    check("kwargs.api_key is the configured key", kwargs.get("api_key") == GATEWAY_KEY)
    check(
        "kwargs.api_base points at the mock gateway",
        kwargs.get("api_base") == f"{MOCK_URL}/v1",
        str(kwargs.get("api_base")),
    )

    print()
    if _failures:
        print(f"E2E RESULT: \033[0;31mFAILED\033[0m ({len(_failures)} check(s)): {_failures}")
        return 1
    print("E2E RESULT: \033[0;32mALL CHECKS PASSED\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
