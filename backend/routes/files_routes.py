"""
Files API routes for S3 file management.

Provides REST API endpoints for file operations including upload, download,
list, delete, and user statistics. Integrates with S3 storage backend.
"""

import logging
from typing import List, Dict, Any, Optional
import re
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi import Query
import base64
from pydantic import BaseModel

from core.utils import get_current_user
from infrastructure.app_factory import app_factory
from core.capabilities import verify_file_token

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["files"])


class FileUploadRequest(BaseModel):
    filename: str
    content_base64: str
    content_type: Optional[str] = "application/octet-stream"
    tags: Optional[Dict[str, str]] = {}


class FileResponse(BaseModel):
    key: str
    filename: str
    size: int
    content_type: str
    last_modified: str
    etag: str
    tags: Dict[str, str]
    user_email: str


class FileContentResponse(BaseModel):
    key: str
    filename: str
    content_base64: str
    content_type: str
    size: int
    last_modified: str
    etag: str
    tags: Dict[str, str]


@router.post("/files", response_model=FileResponse)
async def upload_file(
    request: FileUploadRequest,
    current_user: str = Depends(get_current_user)
) -> FileResponse:
    """Upload a file to S3 storage."""
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
        
        return FileResponse(**result)
        
    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


# Place health endpoint before dynamic /files/{file_key} routes to avoid capture
@router.get("/files/healthz")
async def files_health_check():
    """Health check for files service."""
    s3_client = app_factory.get_file_storage()
    return {
        "status": "healthy",
        "service": "files-api",
        "s3_config": {
            "endpoint": s3_client.base_url if hasattr(s3_client, 'base_url') else "unknown",
            "use_mock": s3_client.use_mock if hasattr(s3_client, 'use_mock') else False
        }
    }


@router.get("/files/{file_key}", response_model=FileContentResponse)
async def get_file(
    file_key: str,
    current_user: str = Depends(get_current_user)
) -> FileContentResponse:
    """Get a file from S3 storage."""
    try:
        s3_client = app_factory.get_file_storage()
        result = await s3_client.get_file(current_user, file_key)
        
        if not result:
            raise HTTPException(status_code=404, detail="File not found")
            
        return FileContentResponse(**result)
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting file: {str(e)}")
        if "Access denied" in str(e):
            raise HTTPException(status_code=403, detail="Access denied")
        raise HTTPException(status_code=500, detail=f"Failed to get file: {str(e)}")


@router.get("/files", response_model=List[FileResponse])
async def list_files(
    current_user: str = Depends(get_current_user),
    file_type: Optional[str] = None,
    limit: int = 100
) -> List[FileResponse]:
    """List files for the current user."""
    try:
        s3_client = app_factory.get_file_storage()
        result = await s3_client.list_files(
            user_email=current_user,
            file_type=file_type,
            limit=limit
        )
        
        return [FileResponse(**file_data) for file_data in result]
        
    except Exception as e:
        logger.error(f"Error listing files: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to list files: {str(e)}")


@router.delete("/files/{file_key}")
async def delete_file(
    file_key: str,
    current_user: str = Depends(get_current_user)
) -> Dict[str, str]:
    """Delete a file from S3 storage."""
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


@router.get("/files/healthz")
async def files_health_check():
    """Health check for files service."""
    s3_client = app_factory.get_file_storage()
    return {
        "status": "healthy",
    "service": "files-api",
        "s3_config": {
            "endpoint": s3_client.base_url if hasattr(s3_client, 'base_url') else "unknown",
            "use_mock": s3_client.use_mock if hasattr(s3_client, 'use_mock') else False
        }
    }


@router.get("/files/download/{file_key:path}")
async def download_file(
    file_key: str,
    token: str | None = Query(default=None, description="Capability token for headless access"),
    current_user: str = Depends(get_current_user)
):
    """Download a file by key as raw bytes.

    Returns a binary response with appropriate content type and filename.
    This endpoint is used by the frontend CanvasPanel and can also be used by tools.
    """
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
