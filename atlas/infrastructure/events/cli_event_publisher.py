"""CLI event publisher for headless/non-interactive use."""

import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CLICollectedResult:
    """Structured result from a collected CLI chat session."""

    message: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    files: Dict[str, Any] = field(default_factory=dict)
    canvas_content: Optional[str] = None
    raw_events: List[Dict[str, Any]] = field(default_factory=list)


class CLIEventPublisher:
    """
    Event publisher for CLI / headless usage.

    Two modes:
    - streaming: prints token text to stdout, tool/status info to stderr
    - collecting: buffers all events, returns structured result
    """

    def __init__(self, streaming: bool = True, quiet: bool = False):
        self.streaming = streaming
        self.quiet = quiet
        self._collected = CLICollectedResult()

    def get_result(self) -> CLICollectedResult:
        """Return the collected result (useful in collecting mode)."""
        return self._collected

    async def publish_chat_response(
        self,
        message: str,
        has_pending_tools: bool = False,
    ) -> None:
        self._collected.message += message
        if self.streaming:
            sys.stdout.write(message)
            sys.stdout.flush()

    async def publish_response_complete(self) -> None:
        if self.streaming:
            # Ensure final newline
            sys.stdout.write("\n")
            sys.stdout.flush()

    async def publish_agent_update(
        self,
        update_type: str,
        **kwargs: Any,
    ) -> None:
        event = {"type": "agent_update", "update_type": update_type, **kwargs}
        self._collected.raw_events.append(event)
        if self.streaming and not self.quiet:
            _print_status(f"[agent] {update_type}")

    async def publish_tool_start(
        self,
        tool_name: str,
        **kwargs: Any,
    ) -> None:
        self._collected.tool_calls.append({"tool": tool_name, "status": "started"})
        if self.streaming and not self.quiet:
            _print_status(f"[tool] {tool_name} ...")

    async def publish_tool_complete(
        self,
        tool_name: str,
        result: Any,
        **kwargs: Any,
    ) -> None:
        # Update last matching tool call
        for tc in reversed(self._collected.tool_calls):
            if tc["tool"] == tool_name and tc["status"] == "started":
                tc["status"] = "complete"
                tc["result"] = result
                break
        if self.streaming and not self.quiet:
            _print_status(f"[tool] {tool_name} done")

    async def publish_files_update(
        self,
        files: Dict[str, Any],
    ) -> None:
        self._collected.files.update(files)
        if self.streaming and not self.quiet:
            _print_status(f"[files] {len(files)} file(s)")

    async def publish_canvas_content(
        self,
        content: str,
        content_type: str = "text/html",
        **kwargs: Any,
    ) -> None:
        self._collected.canvas_content = content

    async def publish_token_stream(
        self,
        token: str,
        is_first: bool = False,
        is_last: bool = False,
    ) -> None:
        """Stream tokens to stdout in CLI mode."""
        if token:
            self._collected.message += token
            if self.streaming:
                sys.stdout.write(token)
                sys.stdout.flush()

    async def send_json(self, data: Dict[str, Any]) -> None:
        self._collected.raw_events.append(data)
        msg_type = data.get("type", "")
        if msg_type == "tool_start":
            tool_name = data.get("tool_name", "unknown")
            self._collected.tool_calls.append({"tool": tool_name, "status": "started"})
            if self.streaming and not self.quiet:
                args = data.get("arguments", {})
                _print_status(f"[tool] {tool_name} called with: {args}")
        elif msg_type == "tool_complete":
            tool_name = data.get("tool_name", "unknown")
            success = data.get("success", False)
            result = data.get("result", "")
            for tc in reversed(self._collected.tool_calls):
                if tc["tool"] == tool_name and tc["status"] == "started":
                    tc["status"] = "complete"
                    tc["result"] = result
                    tc["success"] = success
                    break
            if self.streaming and not self.quiet:
                status = "ok" if success else "error"
                _print_status(f"[tool] {tool_name} {status}: {result}")

    async def publish_elicitation_request(
        self,
        elicitation_id: str,
        tool_call_id: str,
        tool_name: str,
        message: str,
        response_schema: Dict[str, Any],
    ) -> None:
        # CLI cannot handle interactive elicitation; log and skip
        logger.warning(
            "Elicitation requested by tool %s but CLI mode cannot respond interactively",
            tool_name,
        )
        if self.streaming and not self.quiet:
            _print_status(f"[elicitation] {tool_name}: {message} (skipped, non-interactive)")


def _print_status(text: str) -> None:
    """Print status/tool info to stderr so stdout stays clean for LLM output."""
    print(text, file=sys.stderr, flush=True)
