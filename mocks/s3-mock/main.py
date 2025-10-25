"""
Mock S3 Storage Service

This mock provides a persistent S3-compatible storage service for development and testing.
It supports basic S3 operations like PUT, GET, DELETE, and LIST with user-based file isolation.
Files are persisted to disk and survive service restarts.
"""

import base64
import hashlib
import json
import logging
import os
import shutil
import time
import uuid
from typing import Dict, List, Optional, Any
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context to handle startup and shutdown tasks.

    Replaces deprecated on_event handlers for startup/shutdown.
    """
    # Startup
    logger.info("Initializing S3 Mock Storage (lifespan startup)...")
    initialize_storage()
    logger.info(f"S3 Mock Storage initialized with {len(file_storage)} existing files")
    try:
        yield
    finally:
        # Shutdown
        logger.info("Shutting down S3 Mock Storage (lifespan shutdown)...")
        save_metadata()
        logger.info("Metadata saved successfully")


app = FastAPI(title="S3 Mock Service", version="1.0.0", lifespan=lifespan)
security = HTTPBearer(auto_error=False)  # Make auth optional for single-user scenario

# Storage configuration
STORAGE_ROOT = Path("./s3-mock-storage")
METADATA_FILE = STORAGE_ROOT / "metadata.json"

# In-memory cache of metadata (loaded from disk on startup)
file_storage: Dict[str, Dict[str, Any]] = {}  # key -> file_data
user_files: Dict[str, List[str]] = {}  # user_email -> list of file keys


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
    last_modified: datetime
    etag: str
    tags: Dict[str, str]
    user_email: str


class FileContentResponse(BaseModel):
    key: str
    filename: str
    content_base64: str
    content_type: str
    size: int
    last_modified: datetime
    etag: str
    tags: Dict[str, str]


def initialize_storage():
    """Initialize storage directory and load existing metadata."""
    global file_storage, user_files
    
    # Create storage directory if it doesn't exist
    STORAGE_ROOT.mkdir(exist_ok=True)
    
    # Load metadata if it exists
    if METADATA_FILE.exists():
        try:
            with open(METADATA_FILE, 'r') as f:
                data = json.load(f)
                file_storage = data.get('file_storage', {})
                user_files = data.get('user_files', {})
                
                # Convert datetime strings back to datetime objects
                for file_data in file_storage.values():
                    if 'last_modified' in file_data:
                        file_data['last_modified'] = datetime.fromisoformat(file_data['last_modified'])
                        
                logger.info(f"Loaded {len(file_storage)} files from metadata")
        except Exception as e:
            logger.error(f"Error loading metadata: {e}")
            file_storage = {}
            user_files = {}
    else:
        logger.info("No existing metadata found, starting fresh")


def save_metadata():
    """Save metadata to disk."""
    try:
        # Convert datetime objects to strings for JSON serialization
        serializable_storage = {}
        for key, file_data in file_storage.items():
            serialized_data = file_data.copy()
            if 'last_modified' in serialized_data:
                serialized_data['last_modified'] = serialized_data['last_modified'].isoformat()
            serializable_storage[key] = serialized_data
        
        data = {
            'file_storage': serializable_storage,
            'user_files': user_files
        }
        
        with open(METADATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
            
    except Exception as e:
        logger.error(f"Error saving metadata: {e}")


def get_file_path(s3_key: str) -> Path:
    """Get the file system path for an S3 key."""
    # Replace path separators and create safe filename
    safe_key = s3_key.replace('/', '_').replace('\\', '_')
    return STORAGE_ROOT / safe_key


def get_user_from_token(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> str:
    """Extract user email from the authorization token (simplified for mock)."""
    # For single-user scenarios, allow requests without auth and default to a user
    if not credentials or not credentials.credentials:
        return "default@atlas-ui-3.local"  # Default user for single-user scenarios
    
    # In a real implementation, this would validate the JWT and extract user info
    # For mock purposes, we'll just use the token as the user email
    return credentials.credentials  # Using token as user email for simplicity


def generate_s3_key(user_email: str, filename: str, file_type: str = "user") -> str:
    """Generate an S3-style key with user isolation."""
    timestamp = int(time.time())
    unique_id = str(uuid.uuid4())[:8]
    safe_filename = filename.replace(" ", "_").replace("/", "_")
    
    if file_type == "tool":
        # Tool-generated files go in a special directory
        return f"users/{user_email}/generated/{timestamp}_{unique_id}_{safe_filename}"
    else:
        # User-uploaded files
        return f"users/{user_email}/uploads/{timestamp}_{unique_id}_{safe_filename}"


def calculate_etag(content: str) -> str:
    """Calculate ETag for file content."""
    return hashlib.md5(content.encode()).hexdigest()


@app.post("/files", response_model=FileResponse)
async def upload_file(
    request: FileUploadRequest,
    user_email: str = Depends(get_user_from_token)
) -> FileResponse:
    """Upload a file to S3 mock storage."""
    try:
        # Decode base64 content to validate it
        content_bytes = base64.b64decode(request.content_base64)
        
        # Generate S3 key
        file_type = request.tags.get("source", "user") if request.tags else "user"
        s3_key = generate_s3_key(user_email, request.filename, file_type)
        
        # Calculate metadata
        etag = calculate_etag(request.content_base64)
        now = datetime.utcnow()
        
        # Store file data
        file_data = {
            "key": s3_key,
            "filename": request.filename,
            "content_base64": request.content_base64,
            "content_type": request.content_type,
            "size": len(content_bytes),
            "last_modified": now,
            "etag": etag,
            "tags": request.tags or {},
            "user_email": user_email
        }
        
        # Save file to disk
        file_path = get_file_path(s3_key)
        try:
            with open(file_path, 'wb') as f:
                f.write(content_bytes)
            logger.info(f"File saved to disk: {file_path}")
        except Exception as e:
            logger.error(f"Error saving file to disk: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to save file: {str(e)}")
        
        # Store metadata (without content_base64 to save memory)
        file_data_meta = file_data.copy()
        del file_data_meta["content_base64"]  # Don't store content in metadata
        file_storage[s3_key] = file_data_meta
        
        # Update user's file list
        if user_email not in user_files:
            user_files[user_email] = []
        user_files[user_email].append(s3_key)
        
        # Save metadata to disk
        save_metadata()
        
        logger.info(f"File uploaded: {s3_key} by user {user_email}")
        
        return FileResponse(**file_data_meta)
        
    except Exception as e:
        logger.error(f"Error uploading file: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")


@app.get("/files/{file_key:path}", response_model=FileContentResponse)
async def get_file(
    file_key: str,
    user_email: str = Depends(get_user_from_token)
) -> FileContentResponse:
    """Get a file from S3 mock storage."""
    if file_key not in file_storage:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_data = file_storage[file_key]
    
    # Check authorization - user can only access their own files
    if file_data["user_email"] != user_email:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Read file content from disk
    file_path = get_file_path(file_key)
    try:
        with open(file_path, 'rb') as f:
            content_bytes = f.read()
        content_base64 = base64.b64encode(content_bytes).decode()
    except Exception as e:
        logger.error(f"Error reading file from disk: {e}")
        raise HTTPException(status_code=500, detail="Failed to read file")
    
    # Return file data with content
    response_data = file_data.copy()
    response_data["content_base64"] = content_base64
    
    return FileContentResponse(**response_data)


@app.get("/files", response_model=List[FileResponse])
async def list_files(
    user_email: str = Depends(get_user_from_token),
    file_type: Optional[str] = None,
    limit: int = 100
) -> List[FileResponse]:
    """List files for the authenticated user."""
    if user_email not in user_files:
        return []
    
    user_file_keys = user_files[user_email]
    result = []
    
    for key in user_file_keys:
        if key in file_storage:
            file_data = file_storage[key]
            
            # Filter by file type if specified
            if file_type and file_data.get("tags", {}).get("source") != file_type:
                continue
                
            result.append(FileResponse(**file_data))
            
            if len(result) >= limit:
                break
    
    # Sort by last modified, newest first
    result.sort(key=lambda f: f.last_modified, reverse=True)
    
    return result


@app.delete("/files/{file_key:path}")
async def delete_file(
    file_key: str,
    user_email: str = Depends(get_user_from_token)
) -> Dict[str, str]:
    """Delete a file from S3 mock storage."""
    if file_key not in file_storage:
        raise HTTPException(status_code=404, detail="File not found")
    
    file_data = file_storage[file_key]
    
    # Check authorization
    if file_data["user_email"] != user_email:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Delete file from disk
    file_path = get_file_path(file_key)
    try:
        if file_path.exists():
            file_path.unlink()
            logger.info(f"File deleted from disk: {file_path}")
    except Exception as e:
        logger.error(f"Error deleting file from disk: {e}")
        # Continue with metadata cleanup even if file deletion fails
    
    # Remove from storage
    del file_storage[file_key]
    
    # Remove from user's file list
    if user_email in user_files and file_key in user_files[user_email]:
        user_files[user_email].remove(file_key)
    
    # Save updated metadata
    save_metadata()
    
    logger.info(f"File deleted: {file_key} by user {user_email}")
    
    return {"message": "File deleted successfully", "key": file_key}


@app.get("/users/{user_email}/files/stats")
async def get_user_file_stats(
    user_email: str,
    current_user: str = Depends(get_user_from_token)
) -> Dict[str, Any]:
    """Get file statistics for a user."""
    # Users can only see their own stats
    if current_user != user_email:
        raise HTTPException(status_code=403, detail="Access denied")
    
    if user_email not in user_files:
        return {
            "total_files": 0,
            "total_size": 0,
            "upload_count": 0,
            "generated_count": 0
        }
    
    user_file_keys = user_files[user_email]
    total_size = 0
    upload_count = 0
    generated_count = 0
    
    for key in user_file_keys:
        if key in file_storage:
            file_data = file_storage[key]
            total_size += file_data["size"]
            
            if file_data.get("tags", {}).get("source") == "tool":
                generated_count += 1
            else:
                upload_count += 1
    
    return {
        "total_files": len(user_file_keys),
        "total_size": total_size,
        "upload_count": upload_count,
        "generated_count": generated_count
    }


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    storage_size = 0
    file_count = 0
    
    # Calculate storage statistics
    try:
        if STORAGE_ROOT.exists():
            for file_path in STORAGE_ROOT.iterdir():
                if file_path.is_file() and file_path.name != "metadata.json":
                    storage_size += file_path.stat().st_size
                    file_count += 1
    except Exception as e:
        logger.warning(f"Error calculating storage size: {e}")
    
    return {
        "status": "healthy",
        "service": "s3-mock",
        "timestamp": datetime.utcnow(),
        "storage": {
            "root": str(STORAGE_ROOT.absolute()),
            "persistent": True,
            "total_files": len(file_storage),
            "disk_files": file_count,
            "disk_size_bytes": storage_size,
            "metadata_exists": METADATA_FILE.exists()
        },
        "users": {
            "total_users": len(user_files),
            "single_user_mode": True
        }
    }


## Removed deprecated on_event handlers; functionality handled in lifespan above.


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "S3 Mock Storage",
        "version": "1.0.0",
        "description": "Persistent mock S3 service for development and testing",
        "storage_root": str(STORAGE_ROOT.absolute()),
        "persistent": True,
        "single_user_mode": True,
        "endpoints": {
            "upload": "POST /files",
            "get": "GET /files/{key}",
            "list": "GET /files",
            "delete": "DELETE /files/{key}",
            "stats": "GET /users/{email}/files/stats",
            "health": "GET /health"
        }
    }


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.environ.get("PORT", 8003))
    host = os.environ.get("HOST", "127.0.0.1")
    
    logger.info(f"Starting S3 Mock Service on {host}:{port}")
    uvicorn.run(app, host=host, port=port)