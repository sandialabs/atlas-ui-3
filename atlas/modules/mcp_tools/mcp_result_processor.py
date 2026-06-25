"""Tool-result normalization and artifact extraction for MCPToolManager.

Turns a FastMCP CallToolResult (or dict) into our result contract and pulls
out v2 artifacts / display config / image content. No base64 payloads are
inlined into the textual result to avoid prompt bloat.
"""
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ResultProcessorMixin:
    """Normalize MCP tool results and extract v2 artifact components."""

    def _normalize_mcp_tool_result(self, raw_result: Any) -> Dict[str, Any]:
        """Normalize a FastMCP CallToolResult (or similar object) into our contract.

        Returns a dict shaped like:
        {
          "results": <payload or string>,
          "meta_data": {...optional...},
          "returned_file_names": [...optional...],
          "returned_file_count": N (if file contents present)
        }

        Notes:
        - We never inline base64 file contents here to avoid prompt bloat.
        - Handles legacy key forms (result, meta-data, metadata).
        - Falls back to stringifying the raw result if structured extraction fails.
        """
        normalized: Dict[str, Any] = {}
        structured: Dict[str, Any] = {}

        # Attempt extraction in priority order
        try:
            if hasattr(raw_result, "data") and raw_result.data:  # type: ignore[attr-defined]
                # FastMCP 3.x validated/deserialized structured content (highest fidelity)
                structured = raw_result.data if isinstance(raw_result.data, dict) else {"results": raw_result.data}  # type: ignore[attr-defined]
            elif hasattr(raw_result, "structured_content") and raw_result.structured_content:  # type: ignore[attr-defined]
                structured = raw_result.structured_content  # type: ignore[attr-defined]
            else:
                # Fallback: extract text content from content array
                if hasattr(raw_result, "content"):
                    contents = getattr(raw_result, "content")
                    if contents:
                        # Collect all text from TextContent items
                        text_parts = []
                        for item in contents:
                            if hasattr(item, "type") and getattr(item, "type") == "text":
                                text = getattr(item, "text", None)
                                if text:
                                    text_parts.append(text)

                        if text_parts:
                            combined_text = "\n".join(text_parts)
                            # Try to parse as JSON if it looks like JSON
                            if combined_text.strip().startswith(("{", "[")):
                                try:
                                    logger.info("MCP tool result normalization: using content text JSON fallback for structured extraction")
                                    structured = json.loads(combined_text)
                                except Exception:  # pragma: no cover - defensive
                                    # Not valid JSON, use as plain text result
                                    structured = {"results": combined_text}
                            else:
                                # Plain text - use as results directly
                                structured = {"results": combined_text}
        except Exception as parse_err:  # pragma: no cover - defensive
            logger.debug(f"Non-fatal parse issue extracting structured tool result: {parse_err}")

        if isinstance(structured, dict):
            # Support both correct and legacy key forms
            results_payload = structured.get("results") or structured.get("result")
            meta_payload = (
                structured.get("meta_data")
                or structured.get("meta-data")
                or structured.get("metadata")
            )
            returned_file_names = structured.get("returned_file_names")
            returned_file_contents = structured.get("returned_file_contents")

            if results_payload is not None:
                normalized["results"] = results_payload
            if meta_payload is not None:
                try:
                    # Heuristic to prevent very large meta blobs
                    if len(json.dumps(meta_payload)) < 4000:
                        normalized["meta_data"] = meta_payload
                    else:
                        normalized["meta_data_truncated"] = True
                except Exception:  # pragma: no cover
                    normalized["meta_data_parse_error"] = True
            if returned_file_names:
                normalized["returned_file_names"] = returned_file_names
            if returned_file_contents:
                normalized["returned_file_count"] = (
                    len(returned_file_contents) if isinstance(returned_file_contents, (list, tuple)) else 1
                )

            # Phase 5 fallback: if no explicit results key, treat *entire* structured dict (minus large/base64 fields) as results
            if "results" not in normalized:
                # Prune potentially huge / sensitive keys before fallback
                prune_keys = {"returned_file_contents"}
                pruned = {k: v for k, v in structured.items() if k not in prune_keys}
                try:
                    serialized = json.dumps(pruned)
                    if len(serialized) <= 8000:  # size guard
                        normalized["results"] = pruned
                    else:
                        normalized["results_summary"] = {
                            "keys": list(pruned.keys()),
                            "omitted_due_to_size": len(serialized)
                        }
                except Exception:  # pragma: no cover
                    # Fallback to string repr if serialization fails
                    normalized.setdefault("results", str(pruned))

        if not normalized:
            normalized = {"results": str(raw_result)}
        return normalized

    def _extract_v2_components(self, raw_result: Any, tool_name: str):
        """Extract v2 MCP components (artifacts, display config, metadata).

        Supports dict or FastMCP result objects and converts inline
        ImageContent blocks into image artifacts. Returns a 3-tuple of
        ``(artifacts, display_config, meta_data)``.
        """
        artifacts: List[Dict[str, Any]] = []
        display_config: Optional[Dict[str, Any]] = None
        meta_data: Optional[Dict[str, Any]] = None

        try:
            if isinstance(raw_result, dict):
                structured = raw_result
            else:
                structured = {}
                if hasattr(raw_result, "data") and raw_result.data:  # type: ignore[attr-defined]
                    dt = raw_result.data  # type: ignore[attr-defined]
                    if isinstance(dt, dict):
                        structured = dt
                elif hasattr(raw_result, "structured_content") and raw_result.structured_content:  # type: ignore[attr-defined]
                    sc = raw_result.structured_content  # type: ignore[attr-defined]
                    if isinstance(sc, dict):
                        structured = sc
                else:
                    # Fallback: parse first textual content if JSON-like
                    # This handles MCP responses that return data only in content[0].text
                    if hasattr(raw_result, "content"):
                        contents = getattr(raw_result, "content")
                        if contents and len(contents) > 0 and hasattr(contents[0], "text"):
                            first_text = getattr(contents[0], "text")
                            if isinstance(first_text, str) and first_text.strip().startswith("{"):
                                try:
                                    structured = json.loads(first_text)
                                except Exception:
                                    pass

            if isinstance(structured, dict) and structured:
                # Extract artifacts
                raw_artifacts = structured.get("artifacts")
                if isinstance(raw_artifacts, list):
                    for art in raw_artifacts:
                        if isinstance(art, dict):
                            name = art.get("name")
                            b64 = art.get("b64")
                            if name and b64:
                                artifacts.append(art)

                # Extract display
                disp = structured.get("display")
                if isinstance(disp, dict):
                    display_config = disp

                # Extract metadata
                md = structured.get("meta_data")
                if isinstance(md, dict):
                    meta_data = md

            # Extract ImageContent from the content array
            # Allowlist of safe image MIME types
            ALLOWED_IMAGE_MIMES = {
                "image/png", "image/jpeg", "image/gif",
                "image/svg+xml", "image/webp", "image/bmp"
            }

            if hasattr(raw_result, "content"):
                contents = getattr(raw_result, "content")
                if isinstance(contents, list):
                    image_counter = 0
                    for item in contents:
                        # Check if this is an ImageContent object
                        if hasattr(item, "type") and getattr(item, "type") == "image":
                            data = getattr(item, "data", None)
                            mime_type = getattr(item, "mimeType", None)

                            # Validate mime type against allowlist
                            if mime_type and mime_type not in ALLOWED_IMAGE_MIMES:
                                logger.warning(
                                    f"Skipping ImageContent with unsupported mime type: {mime_type}"
                                )
                                continue

                            # Validate base64 data
                            if data:
                                try:
                                    import base64
                                    base64.b64decode(data, validate=True)
                                except Exception:
                                    logger.warning(
                                        "Skipping ImageContent with invalid base64 data"
                                    )
                                    continue

                            if data and mime_type:
                                # Generate a filename based on image counter and mime type
                                # Use mcp_image_ prefix to avoid collisions with structured artifacts
                                ext = mime_type.split("/")[-1] if "/" in mime_type else "bin"
                                filename = f"mcp_image_{image_counter}.{ext}"

                                # Create artifact in the expected format
                                artifact = {
                                    "name": filename,
                                    "b64": data,
                                    "mime": mime_type,
                                    "viewer": "image",
                                    "description": f"Image returned by {tool_name}"
                                }
                                artifacts.append(artifact)
                                logger.debug(f"Extracted ImageContent as artifact: {filename} ({mime_type})")

                                # If no display config exists and this is the first image, auto-open canvas
                                if not display_config and image_counter == 0:
                                    display_config = {
                                        "primary_file": filename,
                                        "open_canvas": True
                                    }

                                image_counter += 1
        except Exception:
            logger.warning("Error extracting v2 MCP components from tool result", exc_info=True)

        return artifacts, display_config, meta_data
