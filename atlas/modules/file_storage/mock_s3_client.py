"""
Mock S3 Storage Client using FastAPI TestClient.

This client provides the same interface as S3StorageClient but uses the
S3 mock server via TestClient, eliminating the need for Docker/MinIO in development.
"""

import base64
import hashlib
import logging
import re
import time
import uuid
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from atlas.core.log_sanitizer import sanitize_for_logging
from atlas.core.metrics_logger import log_metric
from atlas.core.telemetry import (
    ERROR_MESSAGE_MAX_CHARS,
    hash_short,
    preview,
    safe_label,
    set_attrs,
    start_span,
)
from atlas.modules.file_storage.listing import (
    filename_from_key,
    select_file_page,
    tags_with_source,
    user_prefix,
)
from atlas.modules.file_storage.timestamps import iso_timestamp

logger = logging.getLogger(__name__)


def _category_from_key(key: str) -> str:
    """Derive the ``category`` span attribute from an S3 key."""
    if not key:
        return "other"
    if "/generated/" in key:
        return "generated"
    if "/uploads/" in key:
        return "uploads"
    return "other"


_STORAGE_BACKEND = "mock"
# The in-process mock returns one page and does not issue continuation tokens.
# Ask for a wide page so the explorer can order the library before slicing.
_MOCK_LIST_MAX_KEYS = 10000


def _http_date_to_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an HTTP-date (the ``Last-Modified`` header) into a datetime."""
    if not value:
        return None
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def _iso_to_datetime(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp (the listing's ``LastModified``)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class MockS3StorageClient:
    """Mock S3 client using FastAPI TestClient for in-process testing."""

    def __init__(
        self,
        s3_bucket_name: str = None,
    ):
        """Initialize the mock S3 client."""
        from atlas.modules.config import config_manager

        self.bucket_name = s3_bucket_name or config_manager.app_settings.s3_bucket_name
        self.endpoint_url = "in-process-mock"  # For health check compatibility
        self.region = "us-east-1"  # For health check compatibility
        self._client = None  # Lazy initialization

        logger.info(f"MockS3StorageClient initialized with bucket: {self.bucket_name}")

    @property
    def client(self):
        """Lazy-load the TestClient to avoid circular imports."""
        if self._client is None:
            import importlib.util
            import sys
            from pathlib import Path

            from fastapi.testclient import TestClient

            # Get the S3 mock path
            mock_path = Path(__file__).parent.parent.parent.parent / "mocks" / "s3-mock"
            main_py_path = mock_path / "main.py"

            # Add mock directory to sys.path for relative imports (storage, s3_xml)
            mock_path_str = str(mock_path)
            if mock_path_str not in sys.path:
                sys.path.insert(0, mock_path_str)

            # Import the main.py module explicitly with a unique name
            spec = importlib.util.spec_from_file_location("s3_mock_app", main_py_path)
            s3_mock_module = importlib.util.module_from_spec(spec)

            # Add to sys.modules so relative imports work
            sys.modules['s3_mock_app'] = s3_mock_module
            spec.loader.exec_module(s3_mock_module)

            self._client = TestClient(s3_mock_module.get_app())
            logger.info("TestClient for S3 mock initialized")

        return self._client

    def _generate_s3_key(self, user_email: str, filename: str, source_type: str = "user") -> str:
        """Generate an S3-style key with user isolation."""
        timestamp = int(time.time())
        unique_id = str(uuid.uuid4())[:8]
        safe_filename = re.sub(r"[^\w.\-]+", "_", filename)

        if source_type == "tool":
            return f"users/{user_email}/generated/{timestamp}_{unique_id}_{safe_filename}"
        else:
            return f"users/{user_email}/uploads/{timestamp}_{unique_id}_{safe_filename}"

    def _calculate_etag(self, content_bytes: bytes) -> str:
        """Calculate ETag for file content."""
        return hashlib.md5(content_bytes, usedforsecurity=False).hexdigest()

    async def upload_file(
        self,
        user_email: str,
        filename: str,
        content_base64: str,
        content_type: str = "application/octet-stream",
        tags: Optional[Dict[str, str]] = None,
        source_type: str = "user"
    ) -> Dict[str, Any]:
        """
        Upload a file to mock S3 storage.

        Args:
            user_email: Email of the user uploading the file
            filename: Original filename
            content_base64: Base64 encoded file content
            content_type: MIME type of the file
            tags: Additional metadata tags
            source_type: Type of file ("user" or "tool")

        Returns:
            Dictionary containing file metadata including the S3 key
        """
        start_ns = time.monotonic_ns()
        span_attrs = {
            "user_hash": hash_short(user_email),
            "filename": safe_label(filename),
            "content_type": content_type,
            "source_type": source_type,
            "storage_backend": _STORAGE_BACKEND,
        }
        content_bytes: bytes = b""
        s3_key: Optional[str] = None
        with start_span("file.upload", span_attrs) as span:
            try:
                # Decode base64 content
                content_bytes = base64.b64decode(content_base64)

                # Generate S3 key
                s3_key = self._generate_s3_key(user_email, filename, source_type)

                # Prepare tags
                file_tags = tags or {}
                file_tags["source"] = source_type
                file_tags["user_email"] = user_email
                file_tags["original_filename"] = filename

                # Convert tags to query param format (URL-encode values for safety)
                tag_param = "&".join([f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in file_tags.items()])

                # Upload via TestClient
                headers = {
                    "Content-Type": content_type,
                    "x-amz-meta-user_email": user_email,
                    "x-amz-meta-original_filename": filename,
                    "x-amz-meta-source_type": source_type
                }

                response = self.client.put(
                    f"/{self.bucket_name}/{s3_key}",
                    content=content_bytes,
                    headers=headers,
                    params={"tagging": tag_param}
                )

                if response.status_code != 200:
                    raise Exception(f"Upload failed: {response.text}")

                etag = response.headers.get("ETag", "").strip('"')

                # Read the object back, the way the real client does after a
                # PutObject, so the reported timestamp is the stored object's
                # modification time and not the moment of the response.
                head_response = self.client.head(f"/{self.bucket_name}/{s3_key}")
                last_modified = iso_timestamp(
                    _http_date_to_datetime(head_response.headers.get("Last-Modified"))
                )

                result = {
                    "key": s3_key,
                    "filename": filename,
                    "size": len(content_bytes),
                    "content_type": content_type,
                    "last_modified": last_modified,
                    "etag": etag,
                    "tags": file_tags,
                    "user_email": user_email
                }

                category = _category_from_key(s3_key)
                logger.info(
                    "File uploaded successfully: category=%s, size=%d bytes, content_type=%s, user=%s",
                    category,
                    len(content_bytes),
                    sanitize_for_logging(content_type),
                    sanitize_for_logging(user_email),
                )
                logger.debug("Uploaded file key (sanitized): %s", sanitize_for_logging(s3_key))

                log_metric("file_stored", user_email, file_size=len(content_bytes), content_type=content_type, category=category)

                set_attrs(span, {
                    "key_hash": hash_short(s3_key),
                    "file_size": len(content_bytes),
                    "category": category,
                    "success": True,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                })
                return result

            except Exception as e:
                set_attrs(span, {
                    "key_hash": hash_short(s3_key) if s3_key else None,
                    "file_size": len(content_bytes),
                    "category": _category_from_key(s3_key) if s3_key else "other",
                    "success": False,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(e).__name__,
                    "error_message": preview(str(e), max_chars=ERROR_MESSAGE_MAX_CHARS),
                })
                logger.error("Error uploading file to mock S3: %s", sanitize_for_logging(str(e)))
                raise

    async def get_file(self, user_email: str, file_key: str) -> Dict[str, Any]:
        """
        Get a file from mock S3 storage.

        Args:
            user_email: Email of the user requesting the file
            file_key: S3 key of the file to retrieve

        Returns:
            Dictionary containing file data and metadata
        """
        start_ns = time.monotonic_ns()
        span_attrs = {
            "user_hash": hash_short(user_email),
            "key_hash": hash_short(file_key),
            "category": _category_from_key(file_key),
            "storage_backend": _STORAGE_BACKEND,
        }
        with start_span("file.download", span_attrs) as span:
            try:
                # Verify user has access to this file
                if not file_key.startswith(f"users/{user_email}/"):
                    logger.warning(f"Access denied: {sanitize_for_logging(user_email)} attempted to access {sanitize_for_logging(file_key)}")
                    set_attrs(span, {
                        "access_denied": True,
                        "success": False,
                        "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    })
                    raise Exception("Access denied to file")

                # Get object via TestClient
                response = self.client.get(f"/{self.bucket_name}/{file_key}")

                if response.status_code == 404:
                    logger.warning(f"File not found: {sanitize_for_logging(file_key)} for user {sanitize_for_logging(user_email)}")
                    set_attrs(span, {
                        "not_found": True,
                        "success": False,
                        "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                        "error_type": "NotFound",
                    })
                    return None

                if response.status_code != 200:
                    raise Exception(f"Get failed: {response.text}")

                # Read file content
                content_bytes = response.content
                content_base64 = base64.b64encode(content_bytes).decode()

                # Get tags
                tags_response = self.client.get(f"/{self.bucket_name}/{file_key}", params={"tagging": ""})
                tags = {}
                if tags_response.status_code == 200:
                    # Parse XML tags (simplified - just extract from response)
                    import xml.etree.ElementTree as ET
                    try:
                        root = ET.fromstring(tags_response.text)
                        for tag_elem in root.findall(".//Tag"):
                            key_elem = tag_elem.find("Key")
                            value_elem = tag_elem.find("Value")
                            if key_elem is not None and value_elem is not None:
                                tags[key_elem.text] = value_elem.text
                    except ET.ParseError:
                        # Failed to parse tags XML; tags will be left empty. This is non-fatal as tags are optional.
                        logger.warning(f"Failed to parse tags XML for file {sanitize_for_logging(file_key)}", exc_info=True)

                # Extract filename from metadata headers
                filename = response.headers.get("x-amz-meta-original_filename", file_key.split('/')[-1])

                # The mock server reports the object's modification time the
                # way S3 does, in the Last-Modified header.
                last_modified = iso_timestamp(
                    _http_date_to_datetime(response.headers.get("Last-Modified"))
                )

                result = {
                    "key": file_key,
                    "filename": filename,
                    "content_base64": content_base64,
                    "content_type": response.headers.get("Content-Type", "application/octet-stream"),
                    "size": len(content_bytes),
                    "last_modified": last_modified,
                    "etag": response.headers.get("ETag", "").strip('"'),
                    "tags": tags
                }

                category = _category_from_key(file_key)
                logger.info(
                    "File retrieved successfully: category=%s, size=%d bytes, content_type=%s, user=%s",
                    category,
                    len(content_bytes),
                    sanitize_for_logging(response.headers.get("Content-Type", "application/octet-stream")),
                    sanitize_for_logging(user_email),
                )
                logger.debug("Retrieved file key (sanitized): %s", sanitize_for_logging(file_key))

                set_attrs(span, {
                    "filename": safe_label(filename),
                    "content_type": response.headers.get("Content-Type", "application/octet-stream"),
                    "file_size": len(content_bytes),
                    "success": True,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                })
                return result

            except Exception as e:
                set_attrs(span, {
                    "success": False,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(e).__name__,
                    "error_message": preview(str(e), max_chars=ERROR_MESSAGE_MAX_CHARS),
                })
                logger.error("Error getting file from mock S3: %s", sanitize_for_logging(str(e)))
                raise

    async def list_files(
        self,
        user_email: str,
        file_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        query: Optional[str] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List one page of files for a user. See ``list_file_page``."""
        page = await self.list_file_page(
            user_email=user_email,
            file_type=file_type,
            limit=limit,
            offset=offset,
            query=query,
            category=category,
        )
        return page["files"]

    async def list_file_page(
        self,
        user_email: str,
        file_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        query: Optional[str] = None,
        category: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        List one page of files for a user.

        The mock listing is filtered and ordered before each object is read,
        matching the S3 client. ``total`` counts matches before paging.

        Args:
            user_email: Email of the user
            file_type: Optional filter by file type ("user" or "tool")
            limit: Maximum number of files to return
            offset: How many matches to skip
            query: Optional case-insensitive substring of the stored file name
            category: Optional explorer folder (image, code, data, document, other)

        Returns:
            ``{"files", "total", "offset", "limit"}``.
        """
        start_ns = time.monotonic_ns()
        span_attrs = {
            "user_hash": hash_short(user_email),
            "file_type": file_type if file_type is not None else "null",
            "limit": int(limit) if limit is not None else 0,
            "storage_backend": _STORAGE_BACKEND,
        }
        with start_span("storage.list", span_attrs) as span:
            try:
                prefix = user_prefix(user_email, file_type)

                response = self.client.get(
                    f"/{self.bucket_name}",
                    params={
                        "list-type": "2",
                        "prefix": prefix,
                        "max-keys": str(_MOCK_LIST_MAX_KEYS),
                    },
                )

                if response.status_code != 200:
                    raise Exception(f"List failed: {response.text}")

                import xml.etree.ElementTree as ET
                root = ET.fromstring(response.text)
                ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
                contents = root.findall(".//s3:Contents", ns) or root.findall(".//Contents")

                listed = []
                for content in contents:
                    key_elem = content.find("s3:Key", ns)
                    if key_elem is None:
                        key_elem = content.find("Key")
                    size_elem = content.find("s3:Size", ns)
                    if size_elem is None:
                        size_elem = content.find("Size")
                    etag_elem = content.find("s3:ETag", ns)
                    if etag_elem is None:
                        etag_elem = content.find("ETag")
                    modified_elem = content.find("s3:LastModified", ns)
                    if modified_elem is None:
                        modified_elem = content.find("LastModified")

                    if key_elem is None or not key_elem.text:
                        continue

                    listed.append({
                        "key": key_elem.text,
                        "size": int(size_elem.text) if size_elem is not None and size_elem.text else 0,
                        "etag": etag_elem.text.strip('"') if etag_elem is not None and etag_elem.text else "",
                        "last_modified": modified_elem.text if modified_elem is not None else None,
                    })

                page, total = select_file_page(
                    listed,
                    limit=limit,
                    offset=offset,
                    query=query,
                    category=category,
                )

                files = []
                for entry in page:
                    key = entry["key"]
                    # The listing reports the modification time as an
                    # ISO-8601 string; it is parsed and re-formatted so every
                    # path of this client speaks the same format.
                    last_modified = iso_timestamp(_iso_to_datetime(entry.get("last_modified")))

                    head_response = self.client.head(f"/{self.bucket_name}/{key}")
                    filename = head_response.headers.get(
                        "x-amz-meta-original_filename",
                        filename_from_key(key),
                    )
                    content_type = head_response.headers.get("Content-Type", "application/octet-stream")
                    if not last_modified:
                        last_modified = iso_timestamp(
                            _http_date_to_datetime(head_response.headers.get("Last-Modified"))
                        )

                    files.append({
                        "key": key,
                        "filename": filename,
                        "size": entry["size"],
                        "content_type": content_type,
                        "last_modified": last_modified,
                        "etag": entry["etag"],
                        "tags": tags_with_source({}, key),
                        "user_email": user_email,
                    })

                # Inline CR/LF removal: CodeQL py/log-injection traces this
                # barrier and does not treat sanitize_for_logging as one.
                safe_user_email = str(user_email).replace("\r", "").replace("\n", "")
                logger.info(
                    "Listed %s of %s files for user %s",
                    len(files),
                    total,
                    safe_user_email,
                )

                set_attrs(span, {
                    "num_results": len(files),
                    "total_bytes": sum(int(f.get('size', 0) or 0) for f in files),
                    "success": True,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                })
                return {
                    "files": files,
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                }

            except Exception as e:
                set_attrs(span, {
                    "success": False,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(e).__name__,
                    "error_message": preview(str(e), max_chars=ERROR_MESSAGE_MAX_CHARS),
                })
                logger.error("Error listing files from mock S3: %s", sanitize_for_logging(str(e)))
                raise

    async def delete_file(self, user_email: str, file_key: str) -> bool:
        """
        Delete a file from mock S3 storage.

        Args:
            user_email: Email of the user deleting the file
            file_key: S3 key of the file to delete

        Returns:
            True if deletion was successful
        """
        start_ns = time.monotonic_ns()
        span_attrs = {
            "user_hash": hash_short(user_email),
            "key_hash": hash_short(file_key),
            "category": _category_from_key(file_key),
            "storage_backend": _STORAGE_BACKEND,
        }
        with start_span("storage.delete", span_attrs) as span:
            try:
                # Verify user has access to this file
                if not file_key.startswith(f"users/{user_email}/"):
                    logger.warning(f"Access denied for deletion: {sanitize_for_logging(user_email)} attempted to delete {sanitize_for_logging(file_key)}")
                    set_attrs(span, {
                        "access_denied": True,
                        "success": False,
                        "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    })
                    raise Exception("Access denied to delete file")

                # Delete via TestClient
                response = self.client.delete(f"/{self.bucket_name}/{file_key}")

                if response.status_code == 404:
                    logger.warning(f"File not found for deletion: {sanitize_for_logging(file_key)} for user {sanitize_for_logging(user_email)}")
                    set_attrs(span, {
                        "not_found": True,
                        "success": False,
                        "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                        "error_type": "NotFound",
                    })
                    return False

                if response.status_code != 204:
                    raise Exception(f"Delete failed: {response.text}")

                logger.info(f"File deleted successfully: {sanitize_for_logging(file_key)} for user {sanitize_for_logging(user_email)}")
                set_attrs(span, {
                    "success": True,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                })
                return True

            except Exception as e:
                set_attrs(span, {
                    "success": False,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(e).__name__,
                    "error_message": preview(str(e), max_chars=ERROR_MESSAGE_MAX_CHARS),
                })
                logger.error("Error deleting file from mock S3: %s", sanitize_for_logging(str(e)))
                raise

    async def get_user_stats(self, user_email: str) -> Dict[str, Any]:
        """
        Get file statistics for a user.

        Args:
            user_email: Email of the user

        Returns:
            Dictionary containing file statistics
        """
        try:
            # List all user files
            files = await self.list_files(user_email, limit=1000)

            total_size = 0
            upload_count = 0
            generated_count = 0

            for file_data in files:
                total_size += file_data['size']

                # Determine type from key path
                if "/generated/" in file_data['key']:
                    generated_count += 1
                else:
                    upload_count += 1

            result = {
                "total_files": len(files),
                "total_size": total_size,
                "upload_count": upload_count,
                "generated_count": generated_count
            }

            logger.info(f"Got file stats for user {sanitize_for_logging(user_email)}: {result}")
            return result

        except Exception as e:
            logger.error(f"Error getting user stats from mock S3: {str(e)}")
            raise
