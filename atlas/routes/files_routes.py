"""
Files API routes for S3 file management.

Provides REST API endpoints for file operations including upload, download,
list, delete, and user statistics. Integrates with S3 storage backend.
"""

import base64
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from atlas.core.capabilities import verify_file_token
from atlas.core.log_sanitizer import get_current_user
from atlas.core.metrics_logger import log_metric
from atlas.infrastructure.app_factory import app_factory
from atlas.modules.file_storage.listing import FILE_CATEGORIES
from atlas.modules.file_storage.timestamps import iso_timestamp

logger = logging.getLogger(__name__)

BYTES_PER_MIB = 1024 * 1024


def _normalize_file_key(raw_key: str) -> str:
    """Single-pass percent-decode to undo proxy-introduced double-encoding.

    Some reverse proxies (nginx with certain rewrite rules) re-encode characters
    that the frontend already encoded — e.g. @ (%40) becomes %2540 on the wire.
    Starlette decodes one layer, leaving residual %40 in the handler argument.
    This applies one additional unquote pass to recover the true S3 key.

    Safety: the S3 client enforces a users/{email}/ prefix on every key, and S3
    keys are opaque (no ".." resolution), so double-decoding cannot escape the
    user's prefix. See atlas/modules/file_storage/s3_client.py get_file/delete_file.
    """
    return unquote(raw_key)


def get_max_file_upload_size_bytes() -> int:
    """Return the configured maximum user-uploaded file size in bytes."""
    settings = app_factory.get_config_manager().app_settings
    return settings.max_file_upload_size_mb * BYTES_PER_MIB


def get_file_upload_limit_config() -> Dict[str, int]:
    """Return upload limit metadata for API clients."""
    max_size_bytes = get_max_file_upload_size_bytes()
    return {
        "max_file_size_mb": max_size_bytes // BYTES_PER_MIB,
        "max_file_size_bytes": max_size_bytes,
    }


def _base64_decoded_size(content_base64: str) -> int:
    """Estimate decoded byte size from a base64 string without materializing bytes."""
    if not isinstance(content_base64, str):
        raise ValueError("Invalid base64 content")

    normalized = "".join(content_base64.split())
    if not normalized:
        return 0

    padding = len(normalized) - len(normalized.rstrip("="))
    return (len(normalized) * 3 // 4) - padding


def validate_base64_file_size(content_base64: str, *, max_size_bytes: Optional[int] = None) -> int:
    """Validate base64 content against the configured upload limit and return decoded size."""
    try:
        content_size = _base64_decoded_size(content_base64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid base64 content") from exc

    limit = max_size_bytes or get_max_file_upload_size_bytes()
    if content_size > limit:
        max_size_mb = limit // BYTES_PER_MIB
        raise HTTPException(status_code=413, detail=f"File too large. Maximum size is {max_size_mb}MB")

    return content_size


def find_oversized_inline_file(files: Any) -> Optional[tuple[str, int]]:
    """Return the first oversized WebSocket inline file, if any."""
    if not isinstance(files, dict):
        return None

    max_size = get_max_file_upload_size_bytes()
    for filename, file_data in files.items():
        if isinstance(file_data, str):
            content_base64 = file_data
        elif isinstance(file_data, dict):
            content_base64 = file_data.get("content", "")
        else:
            raise HTTPException(status_code=400, detail="Invalid base64 content")

        try:
            content_size = _base64_decoded_size(content_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid base64 content") from exc

        if content_size > max_size:
            return str(filename), content_size
    return None

router = APIRouter(prefix="/api", tags=["files"])

# Separate router for MCP file downloads (/mcp/files/download/...).
# This path is designed to bypass nginx auth_request so MCP servers can
# authenticate solely via HMAC capability tokens in the query string.
mcp_files_router = APIRouter(prefix="/mcp", tags=["mcp-files"])


class FileUploadRequest(BaseModel):
    filename: str
    content_base64: str
    content_type: Optional[str] = "application/octet-stream"
    tags: Optional[Dict[str, str]] = Field(default_factory=dict)


class FileResponse(BaseModel):
    key: str
    filename: str
    size: int
    content_type: str
    last_modified: Optional[str] = None
    etag: str
    tags: Dict[str, str]
    user_email: str


class FileContentResponse(BaseModel):
    key: str
    filename: str
    content_base64: str
    content_type: str
    size: int
    last_modified: Optional[str] = None
    etag: str
    tags: Dict[str, str]


def _file_metadata(metadata: Dict[str, Any]) -> Dict[str, Any]:
    """Return storage metadata shaped for the file response models.

    The storage backends report ``last_modified`` in whatever shape their
    client library uses -- boto3 hands back a ``datetime`` -- while the API
    contract is a string. Normalizing here keeps every file route consistent
    and stops a ``datetime`` from failing response validation (which answers
    HTTP 500 even though the file was stored).
    """
    payload = dict(metadata)
    payload["last_modified"] = iso_timestamp(payload.get("last_modified"))
    return payload


@router.get("/files/healthz")
async def files_health_check():
    """Health check for files service.

    Note: Declared before the dynamic /files/{file_key} route to avoid path capture.
    """
    s3_client = app_factory.get_file_storage()
    return {
        "status": "healthy",
        "service": "files-api",
        "s3_config": {
            "endpoint": s3_client.endpoint_url if hasattr(s3_client, 'endpoint_url') else "unknown",
            "bucket": s3_client.bucket_name if hasattr(s3_client, 'bucket_name') else "unknown"
        }
    }


@router.post("/files", response_model=FileResponse)
async def upload_file(
    request: FileUploadRequest,
    current_user: str = Depends(get_current_user)
) -> FileResponse:
    """Upload a file to S3 storage."""
    content_size = validate_base64_file_size(request.content_base64)

    try:
        s3_client = app_factory.get_file_storage()
        result = await s3_client.upload_file(
            user_email=current_user,
            filename=request.filename,
            content_base64=request.content_base64,
            content_type=request.content_type,
            tags=request.tags,
            source_type=request.tags.get("source", "user") if request.tags else "user"
        )

        log_metric("file_upload", current_user, file_size=content_size, content_type=request.content_type)

        return FileResponse(**_file_metadata(result))

    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}")

        log_metric("error", current_user, error_type="file_upload_failed")

        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@router.get("/files", response_model=List[FileResponse])
async def list_files(
    response: Response,
    current_user: str = Depends(get_current_user),
    file_type: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    q: Optional[str] = Query(default=None, max_length=200),
    category: Optional[str] = Query(default=None),
) -> List[FileResponse]:
    """List one page of files for the current user.

    The body stays a list. ``X-Total-Count`` is the number of matches before
    ``offset`` and ``limit`` are applied, so a library can be browsed one page
    at a time. ``file_type`` is ``user`` (uploads) or ``tool`` (generated).
    ``category`` is an explorer folder: image, code, data, document, or other.
    ``q`` is a case-insensitive substring of the stored file name.
    """
    if category is not None and category not in FILE_CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown file category")

    try:
        s3_client = app_factory.get_file_storage()
        list_page = getattr(s3_client, "list_file_page", None)
        if list_page is not None:
            page = await list_page(
                user_email=current_user,
                file_type=file_type,
                limit=limit,
                offset=offset,
                query=q,
                category=category,
            )
            result = page["files"]
            total = int(page["total"])
        else:
            # Storage doubles that predate paging still answer the first page.
            result = await s3_client.list_files(
                user_email=current_user,
                file_type=file_type,
                limit=limit,
            )
            total = len(result)

        response.headers["X-Total-Count"] = str(total)
        # _file_metadata normalizes last_modified to an ISO format string
        return [FileResponse(**_file_metadata(file_data)) for file_data in result]

    except Exception as e:
        logger.error(f"Error listing files: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")


@router.get("/users/{user_email}/files/stats")
async def get_user_file_stats(
    user_email: str,
    current_user: str = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get file statistics for a user."""
    # Users can only see their own stats
    if current_user != user_email:
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        s3_client = app_factory.get_file_storage()
        result = await s3_client.get_user_stats(current_user)
        return result

    except Exception as e:
        logger.error(f"Error getting user stats: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get stats: {str(e)}")


async def _handle_file_download(file_key: str, token: str | None, current_user: str) -> Response:
    """Shared download logic for both /api/ and /mcp/ file download endpoints."""
    file_key = _normalize_file_key(file_key)
    try:
        s3_client = app_factory.get_file_storage()

        # If token provided, validate and override current_user
        if token:
            claims = verify_file_token(token)
            if not claims or claims.get("k") != file_key:
                raise HTTPException(status_code=403, detail="Invalid token")
            current_user = claims.get("u") or current_user

        result = await s3_client.get_file(current_user, file_key)
        if not result:
            raise HTTPException(status_code=404, detail="File not found")

        try:
            raw = base64.b64decode(result["content_base64"]) if result.get("content_base64") else b""
        except Exception:
            raise HTTPException(status_code=500, detail="Corrupted file content")

        # Sanitize filename for header safety
        fn = result.get('filename', 'download') or 'download'
        # Remove control characters and dangerous bytes
        fn = re.sub(r"[\r\n\t\x00-\x1f\x7f]", "_", fn)
        # Keep it reasonably short
        if len(fn) > 150:
            fn = fn[:150]

        content_type = result.get("content_type", "application/octet-stream") or "application/octet-stream"

        # Default to attachment to reduce XSS risk; allow inline only for a small allowlist
        inline_allow = (
            content_type.startswith("image/")
            or content_type.startswith("text/plain")
            or content_type in ("application/pdf",)
        )
        disposition = "inline" if inline_allow else "attachment"

        headers = {
            "Content-Disposition": f"{disposition}; filename=\"{fn}\"",
            "X-Content-Type-Options": "nosniff",
        }

        return Response(content=raw, media_type=content_type, headers=headers)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error downloading file: {str(e)}")
        if "Access denied" in str(e):
            raise HTTPException(status_code=403, detail="Access denied")
        raise HTTPException(status_code=500, detail=f"Failed to download file: {str(e)}")


@router.get("/files/download/{file_key:path}")
async def download_file(
    file_key: str,
    token: str | None = Query(default=None, description="Capability token for headless access"),
    current_user: str = Depends(get_current_user)
):
    """Download a file by key as raw bytes (browser path).

    This endpoint is used by the frontend CanvasPanel and browser requests.
    In production, nginx applies auth_request to inject X-User-Email.
    Also accepts capability tokens for backward compatibility.
    """
    return await _handle_file_download(file_key, token, current_user)


# NOTE: The path-capturing routes below MUST be declared after all specific
# /files/... routes (healthz, download, list, stats) so that FastAPI matches
# those fixed prefixes first before falling through to the greedy {file_key:path}.
@router.get("/files/{file_key:path}", response_model=FileContentResponse)
async def get_file(
    file_key: str,
    current_user: str = Depends(get_current_user)
) -> FileContentResponse:
    """Get a file from S3 storage."""
    file_key = _normalize_file_key(file_key)
    try:
        s3_client = app_factory.get_file_storage()
        result = await s3_client.get_file(current_user, file_key)

        if not result:
            raise HTTPException(status_code=404, detail="File not found")

        return FileContentResponse(**_file_metadata(result))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting file: {str(e)}")
        if "Access denied" in str(e):
            raise HTTPException(status_code=403, detail="Access denied")
        raise HTTPException(status_code=500, detail=f"Failed to get file: {str(e)}")


@router.delete("/files/{file_key:path}")
async def delete_file(
    file_key: str,
    current_user: str = Depends(get_current_user)
) -> Dict[str, str]:
    """Delete a file from S3 storage."""
    file_key = _normalize_file_key(file_key)
    try:
        s3_client = app_factory.get_file_storage()
        success = await s3_client.delete_file(current_user, file_key)

        if not success:
            raise HTTPException(status_code=404, detail="File not found")

        return {"message": "File deleted successfully", "key": file_key}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting file: {str(e)}")
        if "Access denied" in str(e):
            raise HTTPException(status_code=403, detail="Access denied")
        raise HTTPException(status_code=500, detail=f"Failed to delete file: {str(e)}")


@mcp_files_router.get("/files/download/{file_key:path}")
async def mcp_download_file(
    file_key: str,
    token: str | None = Query(default=None, description="Capability token for headless access"),
    current_user: str = Depends(get_current_user)
):
    """Download a file by key as raw bytes (MCP server path).

    This endpoint is used by MCP servers and other non-browser clients.
    In production, nginx skips auth_request for /mcp/ paths so that
    HMAC capability tokens can authenticate without session cookies.
    """
    return await _handle_file_download(file_key, token, current_user)
