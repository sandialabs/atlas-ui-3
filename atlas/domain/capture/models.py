"""Domain models for the opt-in fine-tune capture feature.

These are plain, serializable dataclasses. The on-disk record format
(``CapturedTurn.to_dict``) is the stable contract that the export CLI and any
downstream training pipeline read, so bump :data:`CAPTURE_SCHEMA_VERSION` and
keep ``from_dict`` backward compatible when the shape changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Bump whenever the persisted record shape changes in a way exporters care
# about. Old records keep their stored version so exporters can branch on it.
CAPTURE_SCHEMA_VERSION = 1


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ConsentRecord:
    """A single user's opt-in state for fine-tune capture.

    Stored under the capture directory keyed by ``user_hash`` so the raw email
    never lands in a filename. ``enabled`` is the live decision; the timestamps
    and ``consent_version`` give an auditable trail and let the system re-prompt
    when the capture scope (and therefore the consent text) changes.
    """

    user_hash: str
    enabled: bool = False
    consent_version: int = 1
    consented_at: Optional[str] = None
    revoked_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_hash": self.user_hash,
            "enabled": self.enabled,
            "consent_version": self.consent_version,
            "consented_at": self.consented_at,
            "revoked_at": self.revoked_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConsentRecord":
        return cls(
            user_hash=data.get("user_hash", ""),
            enabled=bool(data.get("enabled", False)),
            consent_version=int(data.get("consent_version", 1)),
            consented_at=data.get("consented_at"),
            revoked_at=data.get("revoked_at"),
        )


@dataclass
class Trajectory:
    """One side of a turn: what the assistant produced.

    A trajectory is either an assistant message, a set of tool calls, or both.
    ``tool_calls`` entries are ``{"name": str, "arguments": dict}``. This is the
    unit a DPO pair compares (``rejected`` vs ``chosen``) and the unit an SFT
    example emits as its completion.
    """

    assistant_message: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.assistant_message and not self.tool_calls

    def to_dict(self) -> Dict[str, Any]:
        return {
            "assistant_message": self.assistant_message,
            "tool_calls": self.tool_calls,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> Optional["Trajectory"]:
        if not data:
            return None
        return cls(
            assistant_message=data.get("assistant_message", "") or "",
            tool_calls=list(data.get("tool_calls") or []),
        )


@dataclass
class Label:
    """Why this record is believed to be a useful training signal.

    ``source`` ranks quality (see the design note): ``rollback`` (a true DPO
    pair) is highest, ``implicit`` (silence) is lowest and defaults to low
    confidence so SFT pipelines can filter it out. Silence is never treated as
    success.
    """

    source: str = "implicit"  # rollback | tool_edit | rating | implicit
    confidence: float = 0.0
    user_note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "confidence": self.confidence,
            "user_note": self.user_note,
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Label":
        if not data:
            return cls()
        return cls(
            source=data.get("source", "implicit"),
            confidence=float(data.get("confidence", 0.0)),
            user_note=data.get("user_note"),
        )


@dataclass
class CapturedTurn:
    """A single captured turn, optionally a (rejected, chosen) preference pair.

    ``kind`` is ``"turn"`` for a normal opted-in turn (SFT material, ``rejected``
    is ``None``) or ``"pair"`` for a rollback correction (a DPO pair where the
    user picked the tool the model should have called).
    """

    turn_id: str
    conversation_id: str
    kind: str = "turn"  # turn | pair
    parent_turn_id: Optional[str] = None
    captured_at: str = field(default_factory=_utcnow_iso)
    schema_version: int = CAPTURE_SCHEMA_VERSION

    # Consent provenance pinned at capture time.
    consent: Dict[str, Any] = field(default_factory=dict)

    # Context the model saw when it decided.
    model: str = ""
    temperature: Optional[float] = None
    system_prompt: str = ""
    messages_prefix: List[Dict[str, Any]] = field(default_factory=list)
    available_tools: List[Dict[str, Any]] = field(default_factory=list)

    # The decision(s).
    chosen: Optional[Trajectory] = None
    rejected: Optional[Trajectory] = None

    # Raw, untruncated per-LLM-call transcript for full fidelity. Downstream
    # pipelines can rebuild any view from this even as derived fields evolve.
    transcript: List[Dict[str, Any]] = field(default_factory=list)

    label: Label = field(default_factory=Label)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "captured_at": self.captured_at,
            "kind": self.kind,
            "consent": self.consent,
            "conversation_id": self.conversation_id,
            "turn_id": self.turn_id,
            "parent_turn_id": self.parent_turn_id,
            "context": {
                "model": self.model,
                "temperature": self.temperature,
                "system_prompt": self.system_prompt,
                "messages_prefix": self.messages_prefix,
                "available_tools": self.available_tools,
            },
            "chosen": self.chosen.to_dict() if self.chosen else None,
            "rejected": self.rejected.to_dict() if self.rejected else None,
            "transcript": self.transcript,
            "label": self.label.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CapturedTurn":
        context = data.get("context") or {}
        return cls(
            turn_id=data.get("turn_id", ""),
            conversation_id=data.get("conversation_id", ""),
            kind=data.get("kind", "turn"),
            parent_turn_id=data.get("parent_turn_id"),
            captured_at=data.get("captured_at", _utcnow_iso()),
            schema_version=int(data.get("schema_version", CAPTURE_SCHEMA_VERSION)),
            consent=data.get("consent") or {},
            model=context.get("model", ""),
            temperature=context.get("temperature"),
            system_prompt=context.get("system_prompt", ""),
            messages_prefix=list(context.get("messages_prefix") or []),
            available_tools=list(context.get("available_tools") or []),
            chosen=Trajectory.from_dict(data.get("chosen")),
            rejected=Trajectory.from_dict(data.get("rejected")),
            transcript=list(data.get("transcript") or []),
            label=Label.from_dict(data.get("label")),
        )
