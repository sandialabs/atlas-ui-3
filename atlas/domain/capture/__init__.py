"""Domain models for opt-in fine-tune capture.

Captures full LLM input/output for users who voluntarily opt in, so the
traffic can be exported as training data (SFT examples and DPO preference
pairs) for fine-tuning a small customized model. See
``docs/developer/design-notes`` for the design rationale.
"""

from atlas.domain.capture.models import (
    CAPTURE_SCHEMA_VERSION,
    CapturedTurn,
    ConsentRecord,
    Label,
    Trajectory,
)

__all__ = [
    "CAPTURE_SCHEMA_VERSION",
    "CapturedTurn",
    "ConsentRecord",
    "Label",
    "Trajectory",
]
