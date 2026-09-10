"""LLM-context injection for MCP tool-returned images (issue #909).

An MCP tool can return ``ImageContent`` blocks -- a CAD viewport screenshot,
a chart render, a browser-automation capture. ``mcp_result_processor``
extracts each one into a ``ToolResult`` artifact that reaches the frontend
canvas, but nothing ever put the pixels in front of the model: only the
normalized *text* result becomes the ``role: "tool"`` message, and for a
tool whose entire payload is the image that text is ``{"results": {}}``.
The model then reports it "can't directly inspect the returned image" and
keeps working blind.

The OpenAI chat-completions API -- and therefore every provider reached
through it via LiteLLM -- rejects image content inside a ``role: "tool"``
message, so the tool result itself cannot simply be widened. The portable
pattern is to append a synthetic **user** message immediately after the
step's tool results, carrying the images as ``image_url`` data-URI content
blocks; LiteLLM translates those blocks to each provider's native format
(Anthropic image block, Gemini ``inline_data``, Bedrock image).

Lifetime and cost policy:

* Injected images live in the turn's working transcript only. Conversation
  history is text-only and ``handle_session_files`` deliberately clears
  stale vision payloads at the start of every new turn, so a tool image is
  visible for the remainder of the turn in which it was produced, and
  nothing is persisted to the conversation store.
* A rolling cap keeps only the most recent N tool images in the live
  transcript. Older injected messages are demoted in place to a one-line
  text note, so a long agent loop that screenshots every step cannot grow
  the prompt without bound.
* Aggregate and per-image base64 size caps bound the request payload.
* Images are re-validated here (MIME allowlist, base64 decode, size) even
  though ``_extract_v2_components`` already screened ``ImageContent``,
  because structured ``artifacts`` entries reach this module without that
  screening.

Injection is best-effort: no failure here may break the tool-calling turn.
"""

import base64
import logging
from typing import Any, Dict, List, Optional

from .file_processor import _LLM_READY_IMAGE_MIME_TYPES

logger = logging.getLogger(__name__)

# Rolling cap on tool-returned images kept in the live transcript. At
# roughly 330 KB of base64 for a typical screenshot, retaining every image
# a 10-step agent loop produces would bloat both the context window and the
# bill; the most recent screenshots are the ones the model is reasoning
# about, so older ones are demoted to a text note.
MAX_TOOL_IMAGES_PER_TURN = 6

# Per-image base64 cap. base64 is ~4/3 of raw size, so 5 MB of base64 is
# roughly 3.7 MB of image data -- far above any ordinary screenshot, but a
# hard stop against a tool returning a runaway payload.
MAX_TOOL_IMAGE_B64_BYTES = 5 * 1024 * 1024

# Aggregate base64 budget for all live tool images in one transcript.
# Providers such as Bedrock enforce a ~20 MB limit on the entire request
# payload; staying well below that leaves headroom for history and prompt.
MAX_TOOL_IMAGE_TOTAL_B64_BYTES = 12 * 1024 * 1024

_INTRO_PREFIX = "[Automated system note, not written by the user: "
_UNSEEN_NOTE = (
    _INTRO_PREFIX + "tool(s) {tools} returned image(s) that are saved in "
    "the session files, but the current model does not support vision "
    "input, so the images cannot be shown here. Ask about them only via "
    "the tools that produced them.]"
)
_DEMOTED_NOTE = (
    _INTRO_PREFIX + "{count} image(s) returned by a tool were removed "
    "from context to stay within the tool-image budget. The file(s) "
    "remain in the session files.]"
)

# Filename extension -> LLM-ready image MIME, for artifacts without a mime field.
_EXT_TO_IMAGE_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
}


def model_supports_vision(config_manager: Any, model: str) -> bool:
    """Return True when the named model is configured with supports_vision.

    Reads the same configuration the orchestrator uses to gate user-upload
    vision attachment, so both paths agree on what the model can accept.
    """
    if not config_manager:
        return False
    try:
        model_config = config_manager.llm_config.models.get(model)
        return bool(model_config and getattr(model_config, "supports_vision", False))
    except Exception:
        return False


def _infer_image_mime(artifact: Dict[str, Any]) -> Optional[str]:
    """Return the artifact's LLM-ready image MIME type, or None.

    Prefers the explicit ``mime`` field; falls back to the filename
    extension. Anything outside the vision-ready allowlist (SVG included --
    it is vector XML the vision APIs do not accept as image input) yields
    None.
    """
    mime = artifact.get("mime") or artifact.get("mime_type")
    if isinstance(mime, str) and mime.strip().lower() in _LLM_READY_IMAGE_MIME_TYPES:
        return mime.strip().lower()

    name = artifact.get("name") or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return _EXT_TO_IMAGE_MIME.get(ext)


def extract_llm_ready_images(
    artifacts: Any,
    tool_name: str = "",
) -> List[Dict[str, str]]:
    """Return validated ``{name, b64, mime}`` image dicts from tool artifacts.

    Re-validates even what ``_extract_v2_components`` already accepted:
    structured ``artifacts`` entries arrive here without that function's
    base64 check, and the payload is about to be embedded in a provider
    request. Oversized or malformed entries are skipped (logged), never
    raised.
    """
    images: List[Dict[str, str]] = []
    if not artifacts or not isinstance(artifacts, list):
        return images

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        b64 = artifact.get("b64")
        if not isinstance(b64, str) or not b64.strip():
            continue
        b64 = "".join(b64.split())  # tolerate newline-chunked base64

        mime = _infer_image_mime(artifact)
        if mime is None:
            logger.info(
                "Skipping tool image %r from %s: MIME type not usable for "
                "LLM vision input",
                artifact.get("name"), tool_name or "unknown tool",
            )
            continue

        if len(b64) > MAX_TOOL_IMAGE_B64_BYTES:
            logger.warning(
                "Skipping tool image %r from %s: %d base64 bytes exceeds "
                "the %d byte per-image limit",
                artifact.get("name"), tool_name or "unknown tool",
                len(b64), MAX_TOOL_IMAGE_B64_BYTES,
            )
            continue

        try:
            base64.b64decode(b64, validate=True)
        except Exception:
            logger.warning(
                "Skipping tool image %r from %s: invalid base64 data",
                artifact.get("name"), tool_name or "unknown tool",
            )
            continue

        name = artifact.get("name") or f"mcp_image_{len(images)}"
        images.append({"name": str(name), "b64": b64, "mime": mime})
    return images


def build_tool_image_message(
    images: List[Dict[str, str]],
    tool_names: List[str],
) -> Dict[str, Any]:
    """Build the synthetic user message carrying tool-returned images.

    Uses the ``image_url`` data-URI block shape that
    ``message_builder._build_multimodal_user_message`` emits for user
    uploads, so LiteLLM's provider translation sees a layout it already
    handles everywhere.
    """
    seen: List[str] = []
    for tool in tool_names:
        if tool and tool not in seen:
            seen.append(tool)
    tool_label = ", ".join(seen) if seen else "a tool"
    count = len(images)
    intro = (
        f"{_INTRO_PREFIX} tool {tool_label} returned {count} "
        f"image{'s' if count != 1 else ''}, shown below for you to "
        "inspect. The file(s) are also in the session files.]"
    )
    content_blocks: List[Dict[str, Any]] = [{"type": "text", "text": intro}]
    for image in images:
        content_blocks.append({
            "type": "image_url",
            "image_url": {"url": f"data:{image['mime']};base64,{image['b64']}"},
        })
    return {"role": "user", "content": content_blocks}


def _is_image_artifact(artifact: Any) -> bool:
    """Loose check that an artifact is an image, for non-vision notes."""
    if not isinstance(artifact, dict):
        return False
    if artifact.get("viewer") == "image":
        return True
    mime = artifact.get("mime") or artifact.get("mime_type") or ""
    return isinstance(mime, str) and mime.lower().startswith("image/")


def _tool_name_from_artifact(artifact: Dict[str, Any]) -> str:
    """Best-effort tool name from the artifact description field."""
    description = artifact.get("description") or ""
    prefix = "Image returned by "
    if isinstance(description, str) and description.startswith(prefix):
        return description[len(prefix):].strip()
    return ""


class ToolImageInjector:
    """Appends tool-returned images to the live LLM transcript, under caps.

    One instance per turn: it tracks the synthetic messages it injected so
    the rolling "most recent N" cap can demote older images -- individually,
    not message-by-message -- as new ones arrive. All entry points are
    fail-open: an injection problem degrades to the pre-fix behavior
    (text-only tool result) instead of breaking the turn.
    """

    def __init__(self, *, enabled: bool):
        self._enabled = bool(enabled)
        # Live injected messages, oldest first, each with the per-image
        # base64 lengths of the image blocks still present in it (same
        # order as the blocks).
        self._live: List[Dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    def after_tool_results(
        self,
        messages: List[Dict[str, Any]],
        tool_results: List[Any],
        tool_names: Optional[Dict[str, str]] = None,
    ) -> None:
        """Hook called right after a step's tool results were appended.

        When the model supports vision, appends one synthetic user message
        per step carrying that step's images (most-recent-wins caps). When
        it does not, appends a short ``role: "system"`` note after the tool
        results -- the tool result JSON itself is left untouched -- so the
        model knows images exist and why it cannot see them.
        """
        if not tool_results:
            return
        try:
            if self._enabled:
                self._inject(messages, tool_results, tool_names or {})
            else:
                self._note_unseen(messages, tool_results, tool_names or {})
        except Exception:
            logger.warning(
                "Tool image context injection failed; continuing without it",
                exc_info=True,
            )

    # -- vision-capable path ------------------------------------------------

    def _inject(
        self,
        messages: List[Dict[str, Any]],
        tool_results: List[Any],
        tool_names: Dict[str, str],
    ) -> None:
        images: List[Dict[str, str]] = []
        tool_names_used: List[str] = []
        for result in tool_results:
            tool_name = tool_names.get(getattr(result, "tool_call_id", None), "")
            artifacts = getattr(result, "artifacts", None) or []
            if not tool_name:
                for artifact in artifacts:
                    tool_name = _tool_name_from_artifact(
                        artifact if isinstance(artifact, dict) else {}
                    )
                    if tool_name:
                        break
            result_images = extract_llm_ready_images(artifacts, tool_name)
            if result_images:
                tool_names_used.append(tool_name or "a tool")
                images.extend(result_images)

        if not images:
            return

        # A single step cannot hold more images than the rolling cap: one
        # oversized message would later be demoted wholesale. The newest of
        # the batch wins, mirroring the transcript-wide policy.
        if len(images) > MAX_TOOL_IMAGES_PER_TURN:
            logger.warning(
                "Tool returned %d images; keeping the newest %d for the LLM",
                len(images), MAX_TOOL_IMAGES_PER_TURN,
            )
            images = images[-MAX_TOOL_IMAGES_PER_TURN:]

        # Aggregate budget, newest wins: demote the oldest live images,
        # individually, until the new payload fits. Only a payload that
        # cannot fit even with nothing else live is dropped, which the
        # per-image cap already makes near-impossible.
        kept: List[Dict[str, str]] = []
        pending_total = self._live_b64_total()
        for image in images:
            while (
                pending_total + len(image["b64"]) > MAX_TOOL_IMAGE_TOTAL_B64_BYTES
                and self._live
            ):
                pending_total -= self._demote_oldest_images(1)[1]
            if pending_total + len(image["b64"]) > MAX_TOOL_IMAGE_TOTAL_B64_BYTES:
                logger.warning(
                    "Dropping tool-returned image from LLM context: aggregate "
                    "image budget (%d base64 chars) exhausted",
                    MAX_TOOL_IMAGE_TOTAL_B64_BYTES,
                )
                continue
            pending_total += len(image["b64"])
            kept.append(image)
        if not kept:
            return

        message = build_tool_image_message(kept, tool_names_used)
        messages.append(message)
        self._live.append({
            "message": message,
            "b64_lengths": [len(img["b64"]) for img in kept],
        })
        self._enforce_count_cap()
        logger.info(
            "Injected %d tool-returned image(s) into the LLM transcript "
            "(%d image(s) live, %d base64 chars)",
            len(kept), self._live_image_count(), self._live_b64_total(),
        )

    def _live_image_count(self) -> int:
        return sum(len(entry["b64_lengths"]) for entry in self._live)

    def _live_b64_total(self) -> int:
        return sum(sum(entry["b64_lengths"]) for entry in self._live)

    def _enforce_count_cap(self) -> None:
        overflow = self._live_image_count() - MAX_TOOL_IMAGES_PER_TURN
        while overflow > 0 and self._live:
            overflow -= self._demote_oldest_images(overflow)[0]

    def _demote_oldest_images(self, count: int) -> tuple[int, int]:
        """Demote up to *count* images from the oldest live message.

        Image blocks are removed from the message's content list oldest
        first; when a message's last image is taken, the message is
        replaced in place with a one-line note (the dict object stays, so
        transcript ordering and the role sequence are untouched). Returns
        ``(demoted_count, demoted_b64_chars)``.
        """
        if not self._live:
            return 0, 0
        entry = self._live[0]
        message = entry["message"]
        blocks = message.get("content")
        if not isinstance(blocks, list):
            self._live.pop(0)
            return 0, 0

        demoted = 0
        demoted_chars = 0
        while demoted < count and entry["b64_lengths"]:
            # Find and drop the first (oldest) image block.
            for index, block in enumerate(blocks):
                if isinstance(block, dict) and block.get("type") == "image_url":
                    removed = entry["b64_lengths"].pop(0)
                    demoted += 1
                    demoted_chars += removed
                    blocks.pop(index)
                    break
            else:
                break

        if not entry["b64_lengths"]:
            message["content"] = _DEMOTED_NOTE.format(
                count=_count_injected_images(message),
            )
            self._live.pop(0)
        return demoted, demoted_chars

    # -- non-vision path -----------------------------------------------------

    def _note_unseen(
        self,
        messages: List[Dict[str, Any]],
        tool_results: List[Any],
        tool_names: Dict[str, str],
    ) -> None:
        noted_tools: List[str] = []
        for result in tool_results:
            artifacts = getattr(result, "artifacts", None) or []
            if not any(_is_image_artifact(a) for a in artifacts):
                continue
            tool_name = tool_names.get(getattr(result, "tool_call_id", None), "")
            if not tool_name:
                for artifact in artifacts:
                    tool_name = _tool_name_from_artifact(
                        artifact if isinstance(artifact, dict) else {}
                    )
                    if tool_name:
                        break
            noted_tools.append(tool_name or "a tool")
        if not noted_tools:
            return
        # A separate system message, not an edit to the tool result: tool
        # output is often JSON and must stay parseable (the same position
        # the tools-mode synthesis paths already use for their manifests).
        messages.append({
            "role": "system",
            "content": _UNSEEN_NOTE.format(
                tools=", ".join(dict.fromkeys(noted_tools)),
            ),
        })
        logger.info(
            "Noted %d unseen tool-returned image(s) after the tool results "
            "(model does not support vision)",
            len(noted_tools),
        )


def _count_injected_images(message: Dict[str, Any]) -> int:
    """How many images the (already trimmed) synthetic message carried.

    The demotion note reports the full original count, so read it from the
    intro text block before it is replaced.
    """
    blocks = message.get("content")
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                marker = " returned "
                if marker in text:
                    segment = text.split(marker, 1)[1]
                    digits = segment.split(" ", 1)[0]
                    if digits.isdigit():
                        return int(digits)
    return 0
