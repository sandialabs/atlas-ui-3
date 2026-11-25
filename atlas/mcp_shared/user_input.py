"""Ask the user for input from an MCP tool, on any protocol version.

MCP 2026-07-28 removed server-initiated requests (SEP-2322), so a tool can no
longer block on ``ctx.elicit()``. It asks by *returning* an
``InputRequiredResult``; the client collects the answers and calls the tool
again with them, and the tool runs from the top each time. Whatever the tool
needs to remember between those calls travels in ``request_state``, which
FastMCP seals before it goes on the wire.

``UserInput`` hides that mechanics behind one call per question::

    @mcp.tool
    async def greet(ctx: Context) -> str | InputRequiredResult:
        form = UserInput(ctx)
        name = await form.ask("name", "What's your name?", str)
        if name is None:
            return form.input_required()
        if name.action != "accept":
            return "No name given."
        return f"Hello, {name.data}!"

``ask`` returns the answer once the user has given it and ``None`` while it is
still outstanding. Answers from earlier rounds are restored from
``request_state``, so a tool that asks several questions in sequence sees its
earlier answers on every re-run. Because the tool re-runs, keep everything
before the last ``ask`` free of side effects.

On a handshake-era connection (2025-11-25 and older) the same call falls back
to ``ctx.elicit()``, which answers immediately, so one tool body serves both.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Union

from fastmcp import Context
from fastmcp.server.elicitation import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
    handle_elicit_accept,
    parse_elicit_response_type,
)
from mcp_types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult
from mcp_types.version import MODERN_PROTOCOL_VERSIONS

Answer = Union[AcceptedElicitation[Any], DeclinedElicitation, CancelledElicitation]

_STATE_VERSION = 1


class UserInput:
    """Collects the answers one tool call needs, across round trips."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._answers: Dict[str, Dict[str, Any]] = self._restore_answers()
        self._pending: Dict[str, ElicitRequest] = {}

    @property
    def round_trips(self) -> bool:
        """True when input is gathered by returning ``InputRequiredResult``."""
        if self._ctx.is_background_task:
            # Background tasks exist only on 2026-07-28 connections.
            return True
        request_context = self._ctx.request_context
        version = getattr(request_context, "protocol_version", None)
        return version in MODERN_PROTOCOL_VERSIONS

    async def ask(
        self,
        key: str,
        message: str,
        response_type: Any,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[Answer]:
        """Return the user's answer to ``key``, or ``None`` if it must be requested.

        ``key`` names the question within this tool call and must be stable
        across re-runs. ``title`` and ``description`` label the field of a
        scalar, enum, or choice-list ``response_type``.
        """
        if not self.round_trips:
            return await self._ctx.elicit(
                message,
                response_type,
                response_title=title,
                response_description=description,
            )

        config = parse_elicit_response_type(
            response_type, response_title=title, response_description=description
        )
        answer = self._answers.get(key)
        if answer is None:
            self._pending[key] = ElicitRequest(
                params=ElicitRequestFormParams(
                    message=message, requested_schema=config.schema
                )
            )
            return None

        action = answer.get("action")
        if action == "accept":
            return handle_elicit_accept(config, answer.get("content"))
        if action == "decline":
            return DeclinedElicitation()
        return CancelledElicitation()

    def input_required(self) -> InputRequiredResult:
        """The result to return from the tool while questions are outstanding."""
        if not self._pending:
            raise RuntimeError("input_required() called with no outstanding question")
        return InputRequiredResult(
            input_requests=dict(self._pending),
            request_state=json.dumps(
                {"v": _STATE_VERSION, "answers": self._answers},
                separators=(",", ":"),
            ),
        )

    def _restore_answers(self) -> Dict[str, Dict[str, Any]]:
        """Answers from earlier rounds plus the ones delivered with this call."""
        answers: Dict[str, Dict[str, Any]] = {}
        state = self._ctx.request_state
        if state:
            try:
                decoded = json.loads(state)
            except ValueError:
                decoded = None
            if isinstance(decoded, dict) and decoded.get("v") == _STATE_VERSION:
                stored = decoded.get("answers")
                if isinstance(stored, dict):
                    answers.update(
                        {k: v for k, v in stored.items() if isinstance(v, dict)}
                    )
        for key, response in (self._ctx.input_responses or {}).items():
            action = getattr(response, "action", None)
            if action is None:
                continue  # not an elicitation answer
            answers[key] = {
                "action": action,
                "content": getattr(response, "content", None),
            }
        return answers
