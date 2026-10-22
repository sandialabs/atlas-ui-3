"""Turn-level context handling for the chat orchestrator (issue #877).

Kept out of the orchestrator so its routing logic stays readable: this module
assembles "the next request" the way a turn would (system prompt, history with
any checkpoint applied, files manifest, tool definitions) and hands it to the
shared :class:`ContextCompactor` for estimation and compaction.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from atlas.domain.sessions.models import Session
from atlas.modules.llm.usage_tracking import UsageSink

from .compaction_controller import (
    TRIGGER_MANUAL,
    CompactionPolicy,
    ContextCompactor,
    RunCompactionGuard,
)

logger = logging.getLogger(__name__)

MAX_FOCUS_CHARS = 1000


class TurnContext:
    """Builds requests for estimation and runs the turn-boundary triggers."""

    def __init__(
        self,
        *,
        compactor: ContextCompactor,
        message_builder: Any,
        tool_manager: Any,
        supports_vision: Callable[[str], bool],
        supports_pdf: Callable[[str], bool],
    ) -> None:
        self.compactor = compactor
        self.message_builder = message_builder
        self.tool_manager = tool_manager
        self.supports_vision = supports_vision
        self.supports_pdf = supports_pdf

    def policy(self, auto: Any) -> CompactionPolicy:
        return self.compactor.policy(auto if isinstance(auto, bool) else None)

    def _request_builder(
        self,
        session: Session,
        model: str,
        user_email: Optional[str],
        tools: Optional[List[str]],
        custom_system_prompt: Optional[str],
    ):
        async def build() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
            messages = await self.message_builder.build_messages(
                session=session,
                include_files_manifest=True,
                model_supports_vision=self.supports_vision(model),
                model_supports_pdf=self.supports_pdf(model),
                custom_system_prompt=custom_system_prompt,
            )
            return messages, self._tools_schema(tools, user_email)
        return build

    def _tools_schema(self, tools: Optional[List[str]], user_email: Optional[str]) -> List[Dict[str, Any]]:
        if not tools or self.tool_manager is None:
            return []
        try:
            return list(self.tool_manager.get_tools_schema(list(tools), user_email) or [])
        except Exception:
            logger.debug("Tool schemas unavailable for the context estimate", exc_info=True)
            return []

    async def compact_manually(
        self,
        session: Session,
        model: str,
        user_email: Optional[str],
        focus: Any,
        custom_system_prompt: Optional[str],
    ) -> Dict[str, Any]:
        """Compact between turns at the user's request; never adds a user turn."""
        focus_text = focus.strip()[:MAX_FOCUS_CHARS] if isinstance(focus, str) else None
        outcome = await self.compactor.compact_history(
            history=session.history,
            model=model,
            user_email=user_email,
            build_request=self._request_builder(session, model, user_email, None, custom_system_prompt),
            trigger=TRIGGER_MANUAL,
            focus=focus_text or None,
            # Manual compaction keeps only the latest turn verbatim: the user
            # asked for a smaller context, not for the auto threshold's.
            keep_tokens=0,
        )
        return outcome

    async def compact_before_turn(
        self,
        session: Session,
        model: str,
        user_email: Optional[str],
        tools: Optional[List[str]],
        auto: Any,
        custom_system_prompt: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Run the automatic triggers once the turn's user message is in history."""
        policy = self.policy(auto)
        if not policy.auto:
            return None
        build = self._request_builder(session, model, user_email, tools, custom_system_prompt)
        try:
            from atlas.modules.llm.token_estimation import estimate_request_tokens
            messages, schema = await build()
            estimate = estimate_request_tokens(messages, schema, model)
        except Exception:
            logger.debug("Pre-turn context estimate failed", exc_info=True)
            return None
        trigger = self.compactor.should_compact_before_turn(
            policy, self.compactor.window(model), estimate, session.history,
        )
        if trigger is None:
            return None
        return await self.compactor.compact_history(
            history=session.history,
            model=model,
            user_email=user_email,
            build_request=build,
            trigger=trigger,
        )

    def run_guard(
        self, session: Session, model: str, user_email: Optional[str], auto: Any,
    ) -> Optional[RunCompactionGuard]:
        policy = self.policy(auto)
        if not policy.enabled:
            return None
        return self.compactor.run_guard(
            model=model, user_email=user_email, history=session.history, policy=policy,
        )

    async def report_usage(
        self,
        session: Session,
        model: str,
        user_email: Optional[str],
        tools: Optional[List[str]],
        auto: Any,
        custom_system_prompt: Optional[str],
        sink: Optional[UsageSink] = None,
    ) -> Optional[Dict[str, Any]]:
        """Publish the estimated size of the conversation's next request."""
        policy = self.policy(auto)
        if not policy.enabled:
            return None
        try:
            messages, schema = await self._request_builder(
                session, model, user_email, tools, custom_system_prompt,
            )()
            return await self.compactor.publish_usage(
                model=model,
                messages=messages,
                tools_schema=schema,
                policy=policy,
                history=session.history,
                sink=sink,
                session_context=session.context,
            )
        except Exception:
            logger.debug("Context usage report failed", exc_info=True)
            return None
