"""Opt-in fine-tune capture: recorder, storage, and consent.

Capture is gated by two independent flags -- a system flag
(``FEATURE_FINETUNE_CAPTURE_ENABLED``) and a per-user consent record -- and is
off by default at both levels. When both are on, the recorder writes the full
LLM input/output for each turn to a JSONL store so it can later be exported as
fine-tuning data. See ``docs/developer/design-notes`` for the rationale.
"""

from atlas.application.chat.capture.capture_context import (
    CaptureTurnContext,
    capture_turn,
    current_capture_context,
    record_llm_call,
)
from atlas.application.chat.capture.capture_service import CaptureService
from atlas.application.chat.capture.capture_store import CaptureStore

__all__ = [
    "CaptureService",
    "CaptureStore",
    "CaptureTurnContext",
    "capture_turn",
    "current_capture_context",
    "record_llm_call",
]
