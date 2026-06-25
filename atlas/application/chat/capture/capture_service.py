"""Policy + recorder for opt-in fine-tune capture.

Ties the two consent flags together, builds the per-turn context, and turns the
raw LLM I/O accumulated during a turn into a :class:`CapturedTurn` record. The
service is intentionally thin and fail-soft: every public method guards against
exceptions so a capture problem can never break a chat turn.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from atlas.application.chat.capture.capture_context import (
    CaptureTurnContext,
    normalize_tool_calls,
)
from atlas.application.chat.capture.capture_store import CaptureStore
from atlas.domain.capture.models import (
    CAPTURE_SCHEMA_VERSION,
    CapturedTurn,
    Label,
    Trajectory,
)

logger = logging.getLogger(__name__)

# Consent text/scope version. Bump when the capture scope changes so users are
# re-prompted (the frontend compares this against the stored consent_version).
CONSENT_VERSION = 1


class CaptureService:
    """Coordinates consent, context activation, and record writing."""

    def __init__(self, config_manager: Any, store: Optional[CaptureStore] = None):
        self._config = config_manager
        self._store = store or self._build_store(config_manager)

    # ------------------------------------------------------------- wiring
    @staticmethod
    def _build_store(config_manager: Any) -> CaptureStore:
        app_settings = getattr(config_manager, "app_settings", None)
        configured = getattr(app_settings, "runtime_capture_dir", None)
        if configured:
            root = Path(configured)
        else:
            # atlas/application/chat/capture/capture_service.py -> repo root
            project_root = Path(__file__).resolve().parents[4]
            root = project_root / "runtime" / "finetune_capture"
        salt = getattr(app_settings, "capture_user_salt", None)
        return CaptureStore(root, user_salt=salt)

    @property
    def store(self) -> CaptureStore:
        return self._store

    # ------------------------------------------------------------- policy
    def system_enabled(self) -> bool:
        app_settings = getattr(self._config, "app_settings", None)
        return bool(getattr(app_settings, "feature_finetune_capture_enabled", False))

    def is_enabled_for(self, user_email: Optional[str]) -> bool:
        """Capture happens iff the system flag AND the user's consent are on."""
        if not self.system_enabled():
            return False
        try:
            return self._store.get_consent(user_email).enabled
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("Consent lookup failed: %s", exc)
            return False

    def consent_state(self, user_email: Optional[str]) -> Dict[str, Any]:
        """Return the combined system + user consent state for the API."""
        record = self._store.get_consent(user_email)
        return {
            "system_enabled": self.system_enabled(),
            "user_enabled": record.enabled,
            "consent_version": record.consent_version,
            "current_consent_version": CONSENT_VERSION,
            "consented_at": record.consented_at,
            "needs_reconsent": record.enabled
            and record.consent_version < CONSENT_VERSION,
        }

    def set_consent(self, user_email: Optional[str], enabled: bool) -> Dict[str, Any]:
        self._store.set_consent(user_email, enabled, consent_version=CONSENT_VERSION)
        return self.consent_state(user_email)

    # ------------------------------------------------------------ context
    def build_context(
        self,
        *,
        user_email: Optional[str],
        conversation_id: str,
        model: str,
        temperature: Optional[float],
        correction: Optional[Dict[str, Any]] = None,
    ) -> CaptureTurnContext:
        """Create the per-turn accumulator (caller activates it via capture_turn)."""
        user_hash = self._store.user_hash(user_email)
        return CaptureTurnContext(
            turn_id=str(uuid4()),
            conversation_id=conversation_id,
            user_hash=user_hash,
            model=model,
            temperature=temperature,
            consent={
                "user_hash": user_hash,
                "consent_version": CONSENT_VERSION,
                "system_flag_version": CAPTURE_SCHEMA_VERSION,
            },
            correction=correction,
        )

    # ------------------------------------------------------------ recording
    def finish_turn(self, ctx: CaptureTurnContext) -> Optional[Path]:
        """Flush an activated context to storage. Safe to call unconditionally."""
        try:
            if not ctx.llm_calls:
                # Nothing tool-relevant happened (e.g. a plain chat turn with no
                # tools available). Skip -- the high-value signal is tool calls.
                return None
            turn = self._build_turn(ctx)
            return self._store.append_turn(turn)
        except Exception as exc:
            logger.error("Capture finish_turn failed: %s", exc, exc_info=True)
            return None

    def _build_turn(self, ctx: CaptureTurnContext) -> CapturedTurn:
        calls = ctx.llm_calls
        first = calls[0]
        last = calls[-1]

        system_prompt = ""
        prefix: List[Dict[str, Any]] = []
        for msg in first.get("messages", []):
            if msg.get("role") == "system" and not system_prompt:
                system_prompt = msg.get("content", "") or ""
            else:
                prefix.append(msg)

        available_tools = self._summarize_tools(first.get("tools") or [])

        # The decisive output is the model's first action given the prefix +
        # available tools; the final assistant text is the turn's conclusion.
        chosen = Trajectory(
            assistant_message=last.get("content", "") or first.get("content", "") or "",
            tool_calls=list(first.get("tool_calls") or []),
        )

        is_correction = bool(ctx.correction)
        rejected = None
        label = Label(source="implicit", confidence=0.0)
        parent_turn_id = None
        if is_correction:
            corr = ctx.correction or {}
            rejected = Trajectory(
                assistant_message=corr.get("rejected", {}).get("assistant_message", ""),
                tool_calls=normalize_tool_calls(
                    corr.get("rejected", {}).get("tool_calls")
                ),
            )
            parent_turn_id = corr.get("rejected_turn_id")
            label = Label(
                source="rollback",
                confidence=0.95,
                user_note=corr.get("note"),
            )

        return CapturedTurn(
            turn_id=ctx.turn_id,
            conversation_id=ctx.conversation_id,
            kind="pair" if is_correction else "turn",
            parent_turn_id=parent_turn_id,
            consent=ctx.consent,
            model=ctx.model,
            temperature=ctx.temperature,
            system_prompt=system_prompt,
            messages_prefix=prefix,
            available_tools=available_tools,
            chosen=chosen if not chosen.is_empty() else None,
            rejected=rejected if (rejected and not rejected.is_empty()) else None,
            transcript=calls,
            label=label,
        )

    @staticmethod
    def _summarize_tools(tools_schema: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Pin the available-tool schemas at capture time (combats tool drift)."""
        out: List[Dict[str, Any]] = []
        for tool in tools_schema:
            fn = tool.get("function") if isinstance(tool, dict) else None
            if isinstance(fn, dict):
                out.append(
                    {
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "schema": fn.get("parameters", {}),
                    }
                )
        return out

    # --------------------------------------------------------- passthroughs
    def stats(self) -> Dict[str, Any]:
        return self._store.stats()

    def iter_records(self, start_date=None, end_date=None):
        return self._store.iter_records(start_date=start_date, end_date=end_date)

    def delete_user_data(self, user_email: Optional[str]):
        return self._store.delete_user_data(user_email)
