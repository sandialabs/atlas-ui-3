"""
File management utilities - pure functions for session file operations.

This module provides stateless utility functions for handling files within
chat sessions, including user uploads and tool-generated artifacts.
"""

import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from atlas.core.capabilities import create_download_url
from atlas.modules.file_storage.content_extractor import get_content_extractor

if TYPE_CHECKING:
    from atlas.interfaces.events import EventPublisher

logger = logging.getLogger(__name__)

# Raster MIME types eligible for vision model input.
# SVG is excluded — it's vector XML, not useful for LLM vision, and could
# contain embedded scripts (though <img> tags neutralize them).
_VISION_IMAGE_MIME_TYPES = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
})

# Hard limits to prevent unbounded memory growth from vision images stored
# in session context.  base64 ≈ 4/3 × raw, so 20 MB b64 ≈ 15 MB raw.
_MAX_VISION_IMAGE_B64_BYTES = 20 * 1024 * 1024  # 20 MB base64
_MAX_VISION_IMAGES_PER_REQUEST = 10

# Type hint for update callback
UpdateCallback = Callable[[Dict[str, Any]], Awaitable[None]]


async def handle_session_files(
    session_context: Dict[str, Any],
    user_email: Optional[str],
    files_map: Optional[Dict[str, Any]],
    file_manager,
    update_callback: Optional[UpdateCallback] = None,
    model_supports_vision: bool = False,
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

    Returns:
        Updated session context with file references
    """
    # Always clear stale vision image data from prior turns, even when no
    # new files are being uploaded.  Without this, old images silently
    # reattach on every subsequent message in the session.
    updated_context = dict(session_context)
    session_files_ctx = updated_context.setdefault("files", {})
    for existing_ref in session_files_ctx.values():
        existing_ref.pop("image_b64", None)
        existing_ref.pop("image_mime_type", None)

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

                # For vision-capable models, store raw image data so the message
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
                    if event_publisher:
                        try:
                            await event_publisher.publish_warning(message=warning_msg)
                        except Exception:
                            logger.exception("Failed to send vision capability warning")
                    elif update_callback:
                        try:
                            await update_callback({"type": "warning", "message": warning_msg})
                        except Exception:
                            logger.exception("Failed to send vision capability warning")
                if model_supports_vision and mime_type in _VISION_IMAGE_MIME_TYPES:
                    b64_len = len(b64)
                    if b64_len > _MAX_VISION_IMAGE_B64_BYTES:
                        logger.warning(
                            "Vision image %s too large (%d bytes b64, limit %d) — "
                            "sending as text manifest entry instead",
                            filename, b64_len, _MAX_VISION_IMAGE_B64_BYTES,
                        )
                    else:
                        file_ref["image_b64"] = b64
                        file_ref["image_mime_type"] = mime_type
                        logger.debug(
                            "Stored vision image data for %s (%s, %d bytes base64)",
                            filename,
                            mime_type,
                            b64_len,
                        )

                # Attempt content extraction if enabled and mode requests it
                if extract_mode in ("full", "preview") and extractor.is_enabled():
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
