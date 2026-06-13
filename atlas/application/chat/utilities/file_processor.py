"""
File management utilities - pure functions for session file operations.

This module provides stateless utility functions for handling files within
chat sessions, including user uploads and tool-generated artifacts.
"""

import asyncio
import base64
import logging
from io import BytesIO
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from atlas.core.capabilities import create_download_url
from atlas.modules.file_storage.content_extractor import get_content_extractor

if TYPE_CHECKING:
    from atlas.interfaces.events import EventPublisher

logger = logging.getLogger(__name__)

# Raster MIME types eligible for vision model input.
# SVG is excluded — it's vector XML, not useful for LLM vision, and could
# contain embedded scripts (though <img> tags neutralize them).
_LLM_READY_IMAGE_MIME_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
})
_TIFF_IMAGE_MIME_TYPES = frozenset({"image/tiff"})
_VISION_IMAGE_MIME_TYPES = _LLM_READY_IMAGE_MIME_TYPES | _TIFF_IMAGE_MIME_TYPES

# Hard limits to prevent unbounded memory growth from vision images stored
# in session context.  base64 ≈ 4/3 × raw, so 20 MB b64 ≈ 15 MB raw.
_MAX_VISION_IMAGE_B64_BYTES = 20 * 1024 * 1024  # 20 MB base64
_MAX_VISION_IMAGES_PER_REQUEST = 10

# Native PDF document input (LiteLLM "file" content block -> Bedrock document).
_PDF_MIME_TYPE = "application/pdf"
# base64 ≈ 4/3 × raw, so 20 MB b64 ≈ 15 MB raw.  This is the upper bound;
# the real ceiling is Bedrock's 20 MB *total request payload* limit, so very
# large PDFs may still be rejected once history/prompt are added.
_MAX_PDF_B64_BYTES = 20 * 1024 * 1024  # 20 MB base64
# Bedrock Converse accepts up to 5 documents per request.
_MAX_PDF_DOCUMENTS_PER_REQUEST = 5
# Claude on Bedrock caps PDFs at 100 pages per request (200k-context models).
_MAX_PDF_PAGES = 100
# Bedrock enforces a hard ~20 MB limit on the *entire* request payload (all
# inline documents + images + system prompt + history).  The per-document caps
# above do not protect against several mid-sized PDFs summing past that ceiling,
# so cap the aggregate inline base64 payload conservatively below 20 MB to leave
# headroom for the prompt and conversation history.  PDFs that would push the
# request over this budget are demoted to their text-extraction fallback.
_MAX_TOTAL_INLINE_B64_BYTES = 18 * 1024 * 1024  # 18 MB base64 aggregate

# Type hint for update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


def _scale_single_channel_to_8bit(image):
    """Normalize high-precision grayscale image data into an 8-bit PNG band."""
    from PIL import Image

    extrema = image.getextrema()
    if not extrema or isinstance(extrema[0], tuple):
        return image.convert("RGB")

    low, high = extrema
    if high <= low:
        return Image.new("L", image.size, 0)

    scale = 255.0 / (high - low)
    # Pillow's Image.point() cannot be used to downscale the I/I;16/F
    # high-precision modes here: it probes the callable with an
    # ImagePointTransform that only supports affine arithmetic (no
    # int()/min()/max() clamping) and, even when given an arithmetic-only
    # lambda, ignores the requested "L" target mode. Scale the pixels into
    # an 8-bit band by hand instead.
    source = (
        image.get_flattened_data()
        if hasattr(image, "get_flattened_data")
        else image.getdata()
    )
    scaled = bytes(max(0, min(255, int((value - low) * scale))) for value in source)
    return Image.frombytes("L", image.size, scaled)


def _prepare_image_for_png(image):
    """Convert TIFF frame modes, including high-bit-depth data, to PNG-safe modes."""
    high_precision_modes = {"I;16", "I;16B", "I;16L", "I;16N", "I", "F"}
    if image.mode in high_precision_modes:
        return _scale_single_channel_to_8bit(image)
    if image.mode == "P":
        return image.convert("RGBA" if "transparency" in image.info else "RGB")
    if image.mode in {"RGB", "RGBA", "L", "LA"}:
        return image
    return image.convert("RGB")


def _convert_tiff_to_png_b64(image_b64: str) -> str:
    """Convert a TIFF image payload to PNG base64 for LLM vision APIs."""
    try:
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError("Pillow is required to convert TIFF images for vision input") from exc

    raw = base64.b64decode(image_b64, validate=True)
    with Image.open(BytesIO(raw)) as image:
        image.seek(0)
        prepared = _prepare_image_for_png(image.copy())
        output = BytesIO()
        prepared.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode()


def _count_pdf_pages(pdf_b64: str) -> Optional[int]:
    """Return the page count of a base64-encoded PDF, or None if undeterminable.

    Best-effort: a parse failure or a missing pypdf dependency returns None so
    the caller can fall back to sending the document without a page guard.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        logger.debug("pypdf not installed; skipping PDF page count check")
        return None
    try:
        raw = base64.b64decode(pdf_b64, validate=True)
        reader = PdfReader(BytesIO(raw))
        return len(reader.pages)
    except Exception:
        logger.debug("Could not count PDF pages; skipping page guard", exc_info=True)
        return None


async def _publish_warning(
    message: str,
    event_publisher: Optional["EventPublisher"],
    update_callback: Optional[UpdateCallback],
) -> None:
    """Surface a user-facing warning via the event publisher or update callback."""
    if event_publisher:
        try:
            await event_publisher.publish_warning(message=message)
        except Exception:
            logger.exception("Failed to send warning event")
    elif update_callback:
        try:
            await update_callback({"type": "warning", "message": message})
        except Exception:
            logger.exception("Failed to send warning event")


def _normalize_vision_image_for_llm(filename: str, image_b64: str, mime_type: str) -> Optional[tuple[str, str]]:
    """
    Return base64 data and MIME type that can be embedded in an LLM vision request.

    TIFF uploads are accepted by the UI and storage layer, but common LLM vision
    APIs expect PNG/JPEG/WebP/GIF-style payloads. Convert TIFFs to PNG while
    preserving the user's original file reference in session storage.
    """
    if mime_type in _TIFF_IMAGE_MIME_TYPES:
        try:
            return _convert_tiff_to_png_b64(image_b64), "image/png"
        except Exception:
            logger.exception("Failed to convert TIFF image %s to PNG for vision input", filename)
            return None
    return image_b64, mime_type


async def handle_session_files(
    session_context: Dict[str, Any],
    user_email: Optional[str],
    files_map: Optional[Dict[str, Any]],
    file_manager,
    update_callback: Optional[UpdateCallback] = None,
    model_supports_vision: bool = False,
    model_supports_pdf: bool = False,
    event_publisher: Optional["EventPublisher"] = None,
) -> Dict[str, Any]:
    """
    Handle user file ingestion and return updated session context.

    Pure function that processes files and returns new context without mutations.

    Args:
        session_context: Current session context
        user_email: User email for file storage
        files_map: Map of filename to file data. Can be:
            - str: base64 content (legacy format)
            - dict: {"content": base64, "extract": bool} (new format with extraction flag)
        file_manager: File manager instance
        update_callback: Optional callback for emitting updates
        model_supports_vision: When True, image files have their base64 data stored
            in the session context for direct inclusion in LLM vision messages.
        model_supports_pdf: When True, PDF files have their base64 data stored
            in the session context for direct inclusion as LLM document blocks.

    Returns:
        Updated session context with file references
    """
    # Always clear stale vision/PDF data from prior turns, even when no
    # new files are being uploaded.  Without this, old attachments silently
    # reattach on every subsequent message in the session.
    updated_context = dict(session_context)
    session_files_ctx = updated_context.setdefault("files", {})
    for existing_ref in session_files_ctx.values():
        existing_ref.pop("image_b64", None)
        existing_ref.pop("image_mime_type", None)
        existing_ref.pop("pdf_b64", None)
        existing_ref.pop("pdf_mime_type", None)

    if not files_map or not file_manager or not user_email:
        return updated_context

    # Get content extractor
    extractor = get_content_extractor()
    default_extract_mode = extractor.get_default_behavior() if extractor.is_enabled() else "none"

    try:
        uploaded_refs: Dict[str, Dict[str, Any]] = {}
        for filename, file_data in files_map.items():
            try:
                # Handle both legacy (string) and new (dict) formats
                if isinstance(file_data, str):
                    b64 = file_data
                    extract_mode = default_extract_mode
                else:
                    b64 = file_data.get("content", "")
                    # New extractMode field takes priority, then legacy extract bool
                    if "extractMode" in file_data:
                        extract_mode = file_data["extractMode"]
                    elif "extract" in file_data:
                        extract_mode = "full" if file_data["extract"] else "none"
                    else:
                        extract_mode = default_extract_mode

                meta = await file_manager.upload_file(
                    user_email=user_email,
                    filename=filename,
                    content_base64=b64,
                    source_type="user",
                    tags={"source": "user"}
                )

                # Store minimal reference in session context
                file_ref = {
                    "key": meta.get("key"),
                    "content_type": meta.get("content_type"),
                    "size": meta.get("size"),
                    "source": "user",
                    "last_modified": meta.get("last_modified"),
                }

                # Store the extraction mode for build_files_manifest
                file_ref["extract_mode"] = extract_mode

                # For vision-capable models, store LLM-ready image data so the message
                # builder can embed it as an inline image content block.
                # Warn the user if they uploaded an image but the model can't process it.
                mime_type = meta.get("content_type", "")
                if not model_supports_vision and mime_type in _VISION_IMAGE_MIME_TYPES:
                    logger.info(
                        "Image %s uploaded but model does not support vision; "
                        "image will be listed in text manifest only",
                        filename,
                    )
                    warning_msg = (
                        f"The current model does not support image/vision input. "
                        f"The image '{filename}' will be listed as a file reference "
                        f"but cannot be visually analyzed. Switch to a vision-capable "
                        f"model to use image analysis."
                    )
                    await _publish_warning(warning_msg, event_publisher, update_callback)
                if model_supports_vision and mime_type in _VISION_IMAGE_MIME_TYPES:
                    b64_len = len(b64)
                    if b64_len > _MAX_VISION_IMAGE_B64_BYTES:
                        logger.warning(
                            "Vision image %s too large (%d bytes b64, limit %d) — "
                            "sending as text manifest entry instead",
                            filename, b64_len, _MAX_VISION_IMAGE_B64_BYTES,
                        )
                    else:
                        normalized = _normalize_vision_image_for_llm(filename, b64, mime_type)
                        if normalized is None:
                            # Conversion failed (e.g. an unreadable or corrupt
                            # TIFF). Surface a warning so the user knows the
                            # image won't be analyzed, rather than silently
                            # dropping it from the vision payload.
                            warning_msg = (
                                f"The image '{filename}' could not be prepared for "
                                f"vision analysis and will be listed as a file "
                                f"reference only."
                            )
                            await _publish_warning(warning_msg, event_publisher, update_callback)
                        else:
                            normalized_b64, normalized_mime_type = normalized
                            normalized_b64_len = len(normalized_b64)
                            if normalized_b64_len > _MAX_VISION_IMAGE_B64_BYTES:
                                logger.warning(
                                    "Vision image %s too large after normalization "
                                    "(%d bytes b64, limit %d) — sending as text manifest entry instead",
                                    filename, normalized_b64_len, _MAX_VISION_IMAGE_B64_BYTES,
                                )
                            else:
                                file_ref["image_b64"] = normalized_b64
                                file_ref["image_mime_type"] = normalized_mime_type
                                logger.debug(
                                    "Stored vision image data for %s (%s, %d bytes base64)",
                                    filename,
                                    normalized_mime_type,
                                    normalized_b64_len,
                                )

                # For PDF-capable models, store the raw base64 so the message
                # builder can embed it as an inline document content block.
                # Warn if a PDF is uploaded to a model that can't process it.
                if not model_supports_pdf and mime_type == _PDF_MIME_TYPE:
                    logger.info(
                        "PDF %s uploaded but model does not support native PDF input; "
                        "falling back to text extraction / manifest only",
                        filename,
                    )
                if model_supports_pdf and mime_type == _PDF_MIME_TYPE:
                    b64_len = len(b64)
                    # Cheap size check first — reject oversized PDFs before
                    # spending a base64 decode + pypdf parse on the page count.
                    if b64_len > _MAX_PDF_B64_BYTES:
                        logger.warning(
                            "PDF %s too large (%d bytes b64, limit %d) — "
                            "falling back to text extraction instead",
                            filename, b64_len, _MAX_PDF_B64_BYTES,
                        )
                        warning_msg = (
                            f"The PDF '{filename}' is too large to send to the model "
                            f"directly and will be text-extracted instead."
                        )
                        await _publish_warning(warning_msg, event_publisher, update_callback)
                    else:
                        # Page counting decodes + parses with pypdf; run it off
                        # the event loop so a large PDF doesn't block other I/O.
                        page_count = await asyncio.to_thread(_count_pdf_pages, b64)
                        if page_count is not None and page_count > _MAX_PDF_PAGES:
                            logger.warning(
                                "PDF %s has %d pages (limit %d) — falling back to text "
                                "extraction instead",
                                filename, page_count, _MAX_PDF_PAGES,
                            )
                            warning_msg = (
                                f"The PDF '{filename}' has {page_count} pages, over the "
                                f"{_MAX_PDF_PAGES}-page limit for direct analysis; it will "
                                f"be text-extracted instead."
                            )
                            await _publish_warning(warning_msg, event_publisher, update_callback)
                        else:
                            file_ref["pdf_b64"] = b64
                            file_ref["pdf_mime_type"] = _PDF_MIME_TYPE
                            logger.debug(
                                "Stored PDF document data for %s (%d bytes base64, %s pages)",
                                filename, b64_len,
                                page_count if page_count is not None else "unknown",
                            )

                # Attempt content extraction if enabled and mode requests it.
                #
                # For natively-sent PDFs we STILL extract text (rather than
                # skipping it).  The extracted text is excluded from the manifest
                # on the turn the PDF is sent natively (the message builder sets
                # exclude_pdf_documents), so it does not duplicate tokens, but it
                # provides a durable fallback so the document content survives:
                #   - follow-up turns, after pdf_b64 is cleared at the top of the
                #     next turn, and
                #   - count/payload demotion below, which drops pdf_b64.
                # Without this, demoted or second-turn PDFs would collapse to a
                # name-only manifest entry with zero content.
                is_native_pdf = bool(file_ref.get("pdf_b64"))
                effective_extract_mode = extract_mode
                if is_native_pdf and extract_mode not in ("full", "preview"):
                    # Force a full extraction purely as the fallback, even if the
                    # user chose extractMode "none".  No-op when extraction is
                    # globally disabled (handled by is_enabled() below), in which
                    # case the PDF simply has no text fallback.
                    effective_extract_mode = "full"
                if effective_extract_mode in ("full", "preview") and extractor.is_enabled():
                    extraction_result = await extractor.extract_content(
                        filename=filename,
                        content_base64=b64,
                        mime_type=meta.get("content_type"),
                    )
                    if extraction_result.success:
                        file_ref["extracted_content"] = extraction_result.content
                        file_ref["extracted_preview"] = extraction_result.preview
                        if extraction_result.metadata:
                            file_ref["extraction_metadata"] = extraction_result.metadata
                        if is_native_pdf and file_ref.get("extract_mode") not in ("full", "preview"):
                            # The manifest keys display off extract_mode; make sure
                            # the forced fallback is actually surfaced on later turns.
                            file_ref["extract_mode"] = effective_extract_mode
                        logger.info(f"Extracted content from {filename}: {len(extraction_result.preview or '')} chars preview")
                    else:
                        logger.debug(f"Content extraction skipped for {filename}: {extraction_result.error}")

                session_files_ctx[filename] = file_ref
                uploaded_refs[filename] = meta
            except Exception as e:
                logger.error(f"Failed uploading user file {filename}: {e}")

        # Enforce per-request vision image count limit.  Keep the first N
        # (by insertion order) and demote the rest to text-manifest entries.
        if model_supports_vision:
            vision_count = 0
            for name, ref in session_files_ctx.items():
                if ref.get("image_b64"):
                    vision_count += 1
                    if vision_count > _MAX_VISION_IMAGES_PER_REQUEST:
                        logger.warning(
                            "Vision image count limit (%d) reached — "
                            "demoting %s to text manifest entry",
                            _MAX_VISION_IMAGES_PER_REQUEST, name,
                        )
                        ref.pop("image_b64", None)
                        ref.pop("image_mime_type", None)

        # Enforce per-request PDF limits now that every upload is processed.
        # Two guards, oldest-first preserved (insertion order):
        #   1. Bedrock allows at most 5 inline documents per request.
        #   2. Bedrock caps the *entire* request payload at ~20 MB; several
        #      mid-sized PDFs can blow that even when each is under the per-doc
        #      cap, so we also bound the aggregate inline base64 payload.
        # Demoted PDFs keep the text-extraction fallback stored above, so the
        # manifest still carries their content (it is no longer name-only).
        if model_supports_pdf:
            pdf_count = 0
            # Inline vision images share the same request payload budget.
            total_inline_b64 = sum(
                len(ref["image_b64"])
                for ref in session_files_ctx.values()
                if ref.get("image_b64")
            )
            for name, ref in session_files_ctx.items():
                b64 = ref.get("pdf_b64")
                if not b64:
                    continue
                demote_reason = None
                if pdf_count >= _MAX_PDF_DOCUMENTS_PER_REQUEST:
                    demote_reason = (
                        f"more than {_MAX_PDF_DOCUMENTS_PER_REQUEST} PDFs were attached"
                    )
                elif total_inline_b64 + len(b64) > _MAX_TOTAL_INLINE_B64_BYTES:
                    demote_reason = (
                        "the combined size of attached documents exceeds the "
                        "request payload budget"
                    )
                if demote_reason:
                    logger.warning(
                        "Demoting PDF %s from native document to text fallback: %s",
                        name, demote_reason,
                    )
                    ref.pop("pdf_b64", None)
                    ref.pop("pdf_mime_type", None)
                    fallback = (
                        "its extracted text" if ref.get("extracted_content")
                        else "a file reference (no extractable text)"
                    )
                    await _publish_warning(
                        f"The PDF '{name}' will be sent as {fallback} instead of "
                        f"being read directly because {demote_reason}.",
                        event_publisher, update_callback,
                    )
                else:
                    pdf_count += 1
                    total_inline_b64 += len(b64)

        # Emit files update if successful uploads
        if uploaded_refs and update_callback:
            organized = file_manager.organize_files_metadata(uploaded_refs, user_email=session_context.get("user_email"))
            logger.info(
                "Emitting files_update for user uploads: total=%d",
                len(organized.get('files', [])),
            )
            logger.debug("files_update details (user uploads): names=%s", list(uploaded_refs.keys()))
            await update_callback({
                "type": "intermediate_update",
                "update_type": "files_update",
                "data": organized
            })

    except Exception as e:
        logger.error(f"Error ingesting user files: {e}", exc_info=True)

    return updated_context


async def process_tool_artifacts(
    session_context: Dict[str, Any],
    tool_result,
    file_manager,
    update_callback: Optional[UpdateCallback] = None
) -> Dict[str, Any]:
    """
    Process v2 MCP artifacts produced by a tool and return updated session context.

    Pure function that handles tool files without side effects on input context.
    """
    # Check if there's an iframe display configuration (no artifacts needed)
    has_iframe_display = (
        tool_result.display_config and
        isinstance(tool_result.display_config, dict) and
        tool_result.display_config.get("type") == "iframe" and
        tool_result.display_config.get("url")
    )

    # Early return only if no artifacts AND no iframe display, or no file_manager
    if (not tool_result.artifacts and not has_iframe_display) or not file_manager:
        return session_context

    # Work with a copy to avoid mutations
    updated_context = dict(session_context)

    # Process v2 artifacts (only if we have artifacts)
    if tool_result.artifacts:
        user_email = session_context.get("user_email")
        if not user_email:
            return session_context

        updated_context = await ingest_v2_artifacts(
            session_context=updated_context,
            tool_result=tool_result,
            user_email=user_email,
            file_manager=file_manager,
            update_callback=update_callback
        )

    # Handle canvas file notifications with v2 display config
    # This handles both artifact-based displays and iframe-only displays
    await notify_canvas_files_v2(
        session_context=updated_context,
        tool_result=tool_result,
        file_manager=file_manager,
        update_callback=update_callback
    )

    return updated_context


async def ingest_tool_files(
    session_context: Dict[str, Any],
    tool_result,
    user_email: str,
    file_manager,
    update_callback: Optional[UpdateCallback] = None
) -> Dict[str, Any]:
    """
    Persist tool-produced files into storage and update session context.

    Pure function that returns updated context without mutations.
    """
    if not tool_result.returned_file_names:
        return session_context

    # Work with a copy
    updated_context = dict(session_context)

    # Safety: avoid huge ingestions
    MAX_FILES = 10
    names = tool_result.returned_file_names[:MAX_FILES]
    contents = tool_result.returned_file_contents[:MAX_FILES] if tool_result.returned_file_contents else []

    if contents and len(contents) != len(names):
        logger.warning(
            "ToolResult file arrays length mismatch (names=%d, contents=%d) for tool_call_id=%s",
            len(names), len(contents), tool_result.tool_call_id
        )

    pair_count = min(len(names), len(contents)) if contents else 0
    session_files_ctx = updated_context.setdefault("files", {})
    uploaded_refs: Dict[str, Dict[str, Any]] = {}

    for idx, fname in enumerate(names):
        try:
            if idx < pair_count:
                b64 = contents[idx]
                meta = await file_manager.upload_file(
                    user_email=user_email,
                    filename=fname,
                    content_base64=b64,
                    source_type="tool",
                    tags={"source": "tool"}
                )
                session_files_ctx[fname] = {
                    "key": meta.get("key"),
                    "content_type": meta.get("content_type"),
                    "size": meta.get("size"),
                    "source": "tool",
                    "last_modified": meta.get("last_modified"),
                    "tool_call_id": tool_result.tool_call_id
                }
                uploaded_refs[fname] = meta
            else:
                # Name without content – record reference placeholder only if not existing
                if fname not in session_files_ctx:
                    session_files_ctx[fname] = {"source": "tool", "incomplete": True}
        except Exception as e:
            logger.error(f"Failed uploading tool-produced file {fname}: {e}")

    # Emit files update if successful uploads
    if uploaded_refs and update_callback:
        try:
            organized = file_manager.organize_files_metadata(uploaded_refs, user_email=session_context.get("user_email"))
            logger.info(
                "Emitting files_update for tool uploads: total=%d",
                len(organized.get('files', [])),
            )
            logger.debug("files_update details (tool uploads): names=%s", list(uploaded_refs.keys()))
            await update_callback({
                "type": "intermediate_update",
                "update_type": "files_update",
                "data": organized
            })
        except Exception as e:
            logger.error(f"Failed emitting tool files update: {e}")

    return updated_context


async def notify_canvas_files(
    session_context: Dict[str, Any],
    file_names: List[str],
    file_manager,
    update_callback: Optional[UpdateCallback] = None
) -> None:
    """
    Send canvas files notification for tool-produced files.

    Pure function with no side effects on session context.
    """
    if not update_callback or not file_names or not file_manager:
        return

    try:
        uploaded_refs = {}
        files_ctx = session_context.get("files", {})

        for fname in file_names:
            ref = files_ctx.get(fname)
            if ref and ref.get("key"):
                uploaded_refs[fname] = {
                    "key": ref.get("key"),
                    "size": ref.get("size", 0),
                    "content_type": ref.get("content_type", "application/octet-stream"),
                    "last_modified": ref.get("last_modified"),
                    "tags": {"source": ref.get("source", "tool")}
                }

        if uploaded_refs:
            user_email = session_context.get("user_email")
            canvas_files = []
            for fname, meta in uploaded_refs.items():
                if file_manager.should_display_in_canvas(fname):
                    file_ext = file_manager.get_file_extension(fname).lower()
                    file_key = meta.get("key")
                    entry = {
                        "filename": fname,
                        "type": file_manager.get_canvas_file_type(file_ext),
                        "s3_key": file_key,
                        "size": meta.get("size", 0),
                    }
                    if file_key and user_email:
                        entry["download_url"] = create_download_url(file_key, user_email)
                    canvas_files.append(entry)

            if canvas_files:
                await update_callback({
                    "type": "intermediate_update",
                    "update_type": "canvas_files",
                    "data": {"files": canvas_files}
                })
    except Exception as emit_err:
        logger.warning(f"Non-fatal: failed to emit canvas_files update: {emit_err}")


async def emit_files_update_from_context(
    session_context: Dict[str, Any],
    file_manager,
    update_callback: Optional[UpdateCallback] = None
) -> None:
    """
    Emit a files_update event based on session context files.

    Pure function with no side effects.
    """
    if not file_manager or not update_callback:
        return

    try:
        # Build temp structure expected by organizer
        file_refs: Dict[str, Dict[str, Any]] = {}
        for fname, ref in session_context.get("files", {}).items():
            # Expand to shape similar to S3 metadata for organizer
            file_refs[fname] = {
                "key": ref.get("key"),
                "size": ref.get("size", 0),
                "content_type": ref.get("content_type", "application/octet-stream"),
                "last_modified": ref.get("last_modified"),
                "tags": {"source": ref.get("source", "user")}
            }

        organized = file_manager.organize_files_metadata(file_refs, user_email=session_context.get("user_email"))
        logger.info(
            "Emitting files_update from context: total=%d",
            len(organized.get('files', [])),
        )
        await update_callback({
            "type": "intermediate_update",
            "update_type": "files_update",
            "data": organized
        })
    except Exception as e:
        logger.error(f"Failed emitting files update: {e}")


async def ingest_v2_artifacts(
    session_context: Dict[str, Any],
    tool_result,
    user_email: str,
    file_manager,
    update_callback: Optional[UpdateCallback] = None
) -> Dict[str, Any]:
    """
    Persist v2 MCP artifacts into storage and update session context.

    Pure function that returns updated context without mutations.
    """
    if not tool_result.artifacts:
        return session_context

    # Work with a copy
    updated_context = dict(session_context)

    # Safety: avoid huge ingestions
    MAX_ARTIFACTS = 10
    artifacts = tool_result.artifacts[:MAX_ARTIFACTS]

    try:
        # Prepare files for upload
        files_to_upload = []
        for artifact in artifacts:
            name = artifact.get("name")
            b64_content = artifact.get("b64")
            mime_type = artifact.get("mime")

            if not name or not b64_content:
                logger.warning("Skipping artifact with missing name or content")
                continue

            files_to_upload.append({
                "filename": name,
                "content": b64_content,
                "mime_type": mime_type
            })

        if not files_to_upload:
            return updated_context

        # Upload files to storage
        uploaded_refs = await file_manager.upload_files_from_base64(
            files_to_upload, user_email
        )

        # Add file references to session context
        current_files = updated_context.setdefault("files", {})
        current_files.update(uploaded_refs)

        # Emit files update if successful uploads
        if uploaded_refs and update_callback:
            organized = file_manager.organize_files_metadata(uploaded_refs, user_email=session_context.get("user_email"))
            logger.info(
                "Emitting files_update for v2 artifacts: total=%d",
                len(organized.get('files', [])),
            )
            logger.debug(
                "files_update details (v2 artifacts): names=%s",
                list(uploaded_refs.keys()),
            )
            await update_callback({
                "type": "intermediate_update",
                "update_type": "files_update",
                "data": organized
            })

    except Exception as e:
        logger.error(f"Error ingesting v2 artifacts: {e}", exc_info=True)

    return updated_context


async def notify_canvas_files_v2(
    session_context: Dict[str, Any],
    tool_result,
    file_manager,
    update_callback: Optional[UpdateCallback] = None
) -> None:
    """
    Send v2 canvas files notification with display configuration.

    Pure function with no side effects on session context.
    """
    if not update_callback:
        return

    # Check if there's an iframe display configuration (no artifacts needed)
    has_iframe_display = (
        tool_result.display_config and
        isinstance(tool_result.display_config, dict) and
        tool_result.display_config.get("type") == "iframe" and
        tool_result.display_config.get("url")
    )

    # If no artifacts and no iframe display, nothing to show
    if not tool_result.artifacts and not has_iframe_display:
        return

    try:
        # Get uploaded file references from session context
        uploaded_refs = session_context.get("files", {})
        artifact_names = [artifact.get("name") for artifact in tool_result.artifacts if artifact.get("name")]

        # Handle iframe-only display (no artifacts)
        if has_iframe_display and not artifact_names:
            canvas_update = {
                "type": "intermediate_update",
                "update_type": "canvas_files",
                "data": {
                    "files": [],
                    "display": tool_result.display_config
                }
            }
            logger.info("Emitting canvas_files event for iframe display")
            logger.debug(
                "canvas_files iframe display details: url=%s, title=%s",
                tool_result.display_config.get("url"),
                tool_result.display_config.get("title", "Embedded Content"),
            )
            await update_callback(canvas_update)
            return

        if uploaded_refs and artifact_names:
            user_email = session_context.get("user_email")
            canvas_files = []
            for fname in artifact_names:
                meta = uploaded_refs.get(fname)
                if meta and file_manager.should_display_in_canvas(fname):
                    # Get MIME type from artifact if available
                    artifact = next((a for a in tool_result.artifacts if a.get("name") == fname), {})
                    mime_type = artifact.get("mime")

                    file_ext = file_manager.get_file_extension(fname).lower()
                    file_key = meta.get("key")
                    entry = {
                        "filename": fname,
                        "type": file_manager.get_canvas_file_type(file_ext),
                        "s3_key": file_key,
                        "size": meta.get("size", 0),
                        "mime_type": mime_type
                    }
                    if file_key and user_email:
                        entry["download_url"] = create_download_url(file_key, user_email)
                    canvas_files.append(entry)

            if canvas_files:
                # Reorder files to put primary_file first if provided
                primary = None
                if tool_result.display_config and isinstance(tool_result.display_config, dict):
                    primary = tool_result.display_config.get("primary_file")
                if primary:
                    # stable reorder
                    canvas_files = sorted(
                        canvas_files,
                        key=lambda f: 0 if f.get("filename") == primary else 1
                    )

                # Build canvas update with v2 display configuration
                logger.info("Emitting canvas_files event: count=%d", len(canvas_files))
                logger.debug(
                    "canvas_files details: files=%s, display=%s",
                    [f.get("filename") for f in canvas_files],
                    tool_result.display_config,
                )
                canvas_update = {
                    "type": "intermediate_update",
                    "update_type": "canvas_files",
                    "data": {"files": canvas_files}
                }

                # Add v2 display configuration if present
                if tool_result.display_config:
                    canvas_update["data"]["display"] = tool_result.display_config

                await update_callback(canvas_update)
            else:
                logger.debug("No canvas-displayable artifacts found. artifact_names=%s", artifact_names)

    except Exception as emit_err:
        logger.warning(f"Non-fatal: failed to emit v2 canvas_files update: {emit_err}")


def build_files_manifest(
    session_context: Dict[str, Any],
    exclude_vision_images: bool = False,
    exclude_pdf_documents: bool = False,
) -> Optional[Dict[str, str]]:
    """
    Build ephemeral files manifest for LLM context.

    Pure function that creates manifest from session context.
    Includes extracted content previews when available.

    Args:
        session_context: Session context containing files dict
        exclude_vision_images: When True, skip image files that already have
            ``image_b64`` stored (they will be sent as inline vision blocks
            by the message builder instead).
        exclude_pdf_documents: When True, skip PDF files that already have
            ``pdf_b64`` stored (they will be sent as inline document blocks
            by the message builder instead).
    """
    files_ctx = session_context.get("files", {})
    if not files_ctx:
        return None

    # Build file list with extracted content based on extract_mode
    file_entries = []
    has_full = False
    has_preview = False
    has_none = False
    for name in sorted(files_ctx.keys()):
        file_info = files_ctx[name]

        # Skip image files handled as vision content blocks
        if exclude_vision_images and file_info.get("image_b64"):
            continue

        # Skip PDF files handled as inline document content blocks
        if exclude_pdf_documents and file_info.get("pdf_b64"):
            continue

        entry = f"- {name}"
        mode = file_info.get("extract_mode", "preview")

        # Include extraction metadata if available
        if file_info.get("extraction_metadata"):
            meta = file_info["extraction_metadata"]
            if meta.get("pages"):
                entry += f" ({meta['pages']} pages)"

        if mode == "full" and file_info.get("extracted_content"):
            has_full = True
            content = file_info["extracted_content"]
            entry += (
                f"\n    << content of file {name} >>\n"
                f"    {content}\n"
                f"    << end content of file {name} >>"
            )
        elif mode == "preview" and file_info.get("extracted_preview"):
            has_preview = True
            preview = file_info["extracted_preview"]
            # Limit to 10 lines and 2000 characters to prevent excessive token usage
            lines = preview.split("\n")[:10]
            indented_preview = "\n    ".join(lines)
            if len(indented_preview) > 2000:
                indented_preview = indented_preview[:1997] + "..."
            entry += f"\n    Content preview:\n    {indented_preview}"
        else:
            has_none = True

        file_entries.append(entry)

    if not file_entries:
        return None

    file_list = "\n".join(file_entries)

    # Build context note based on which modes were used
    notes = []
    if has_full:
        notes.append(
            "Files with full content shown above have been fully extracted. "
            "You can reference this content directly."
        )
    if has_preview:
        notes.append(
            "Files with content previews shown above have been partially analyzed. "
            "You can reference preview content directly."
        )
    if has_none:
        notes.append(
            "Files listed by name only can be opened or analyzed on request."
        )
    context_note = f"({' '.join(notes)})" if notes else ""

    return {
        "role": "system",
        "content": (
            "Available session files:\n"
            f"{file_list}\n\n"
            f"{context_note} "
            "The user may refer to these files in their requests as session files or attachments."
        )
    }
