"""Build a synthetic spans.jsonl fixture for the PR #550 validation script.

Emits a minimal but contract-correct span tree that exercises each of the
five dashboard views:
  - chat.turn (session search + turn drill-down)
  - llm.call  (LLM performance rollup)
  - tool.call (tool health rollup + per-tool failures)
  - rag.query (RAG effectiveness rollup)

The fixture deliberately includes sensitive-looking SECRET_* strings inside
span payloads' non-whitelisted attributes so the validation script can prove
the `_SAFE_ATTRIBUTE_KEYS` whitelist strips them before responses are
returned.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

SEC_NS = 1_000_000_000
NOW_NS = int(time.time() * SEC_NS)
TURN_ID = "turn-pr550-e2e"
SESSION_ID = "session-pr550-e2e"
TRACE_ID = "trace-pr550-e2e"


def span(
    *,
    name: str,
    span_id: str,
    parent: str | None,
    start_ns: int,
    duration_ns: int,
    attrs: dict,
    status: str = "OK",
) -> dict:
    return {
        "name": name,
        "span_id": span_id,
        "parent_span_id": parent,
        "trace_id": TRACE_ID,
        "start_time_ns": start_ns,
        "end_time_ns": start_ns + duration_ns,
        "duration_ns": duration_ns,
        "status": status,
        "attributes": attrs,
    }


def build() -> list[dict]:
    t0 = NOW_NS - 120 * SEC_NS
    return [
        span(
            name="chat.turn",
            span_id="s-turn",
            parent=None,
            start_ns=t0,
            duration_ns=5 * SEC_NS,
            attrs={
                "turn_id": TURN_ID,
                "session_id": SESSION_ID,
                "user_hash": "abcd1234abcd1234",
                "prompt_hash": "ffff0000ffff0000",
                "prompt_chars": 128,
                "prompt_tokens": 32,
                "model": "gpt-pr550",
                # Sensitive — MUST be dropped by _SAFE_ATTRIBUTE_KEYS whitelist
                "raw_prompt_text": "SECRET_PROMPT_DO_NOT_LEAK",
            },
        ),
        span(
            name="llm.call",
            span_id="s-llm-1",
            parent="s-turn",
            start_ns=t0 + 10_000_000,
            duration_ns=800_000_000,
            attrs={
                "model": "gpt-pr550",
                "provider": "test",
                "input_tokens": 120,
                "output_tokens": 60,
                "retry_count": 1,
                "latency_ms": 800,
                "finish_reason": "stop",
                "raw_prompt_text": "SECRET_PROMPT_DO_NOT_LEAK",
            },
        ),
        span(
            name="tool.call",
            span_id="s-tool-ok",
            parent="s-turn",
            start_ns=t0 + SEC_NS,
            duration_ns=150_000_000,
            attrs={
                "tool_name": "pr550_demo_tool",
                "tool_source": "builtin",
                "tool_call_id": "call-ok",
                "success": True,
                "duration_ms": 150,
                "args_hash": "deadbeefdeadbeef",
                "output_size": 42,
                "raw_tool_output": "SECRET_TOOL_OUTPUT_DO_NOT_LEAK",
            },
        ),
        span(
            name="tool.call",
            span_id="s-tool-fail",
            parent="s-turn",
            start_ns=t0 + 2 * SEC_NS,
            duration_ns=100_000_000,
            attrs={
                "tool_name": "pr550_demo_tool",
                "tool_source": "builtin",
                "tool_call_id": "call-fail",
                "success": False,
                "duration_ms": 100,
                "error_type": "TimeoutError",
                "error_message": "upstream timed out after 100ms",
                "raw_tool_output": "SECRET_TOOL_OUTPUT_DO_NOT_LEAK",
            },
            status="ERROR",
        ),
        span(
            name="rag.query",
            span_id="s-rag",
            parent="s-turn",
            start_ns=t0 + 3 * SEC_NS,
            duration_ns=200_000_000,
            attrs={
                "data_source": "pr550_docs",
                "query_hash": "0011223344556677",
                "query_chars": 64,
                "num_results": 5,
                "top_score": 0.91,
                "doc_ids": ["doc-1", "doc-2", "doc-3"],
                "doc_scores": [0.91, 0.83, 0.77],
                "docs_used_in_context": ["doc-1"],
                "raw_document_text": "SECRET_RAG_TEXT_DO_NOT_LEAK",
            },
        ),
    ]


def main(out_path: Path) -> None:
    spans = build()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        for s in spans:
            f.write(json.dumps(s) + "\n")
    print(f"wrote {len(spans)} spans to {out_path}")


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("spans.jsonl")
    main(target)
