"""
S3 Client for file storage operations.

This module provides a client interface to interact with S3-compatible storage
(MinIO or AWS S3) using boto3.
"""

import base64
import logging
import re
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

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


_STORAGE_BACKEND = "s3"


class S3StorageClient:
    """Client for interacting with S3-compatible storage (MinIO/AWS S3)."""

    def __init__(
        self,
        s3_endpoint: str = None,
        s3_bucket_name: str = None,
        s3_access_key: str = None,
        s3_secret_key: str = None,
        s3_region: str = None,
        s3_timeout: int = None,
        s3_use_ssl: bool = None
    ):
        """Initialize the S3 client with configuration."""
        # Allow dependency injection for testing
        if any(param is None for param in [s3_endpoint, s3_bucket_name, s3_access_key, s3_secret_key, s3_region, s3_timeout, s3_use_ssl]):
            from atlas.modules.config import config_manager
            config = config_manager.app_settings
            s3_endpoint = s3_endpoint or config.s3_endpoint
            s3_bucket_name = s3_bucket_name or config.s3_bucket_name
            s3_access_key = s3_access_key or config.s3_access_key
            s3_secret_key = s3_secret_key or config.s3_secret_key
            s3_region = s3_region or config.s3_region
            s3_timeout = s3_timeout or config.s3_timeout
            s3_use_ssl = s3_use_ssl if s3_use_ssl is not None else config.s3_use_ssl

        self.endpoint_url = s3_endpoint
        self.bucket_name = s3_bucket_name
        self.region = s3_region
        self.timeout = s3_timeout

        # Create boto3 S3 client
        self.s3_client = boto3.client(
            's3',
            endpoint_url=self.endpoint_url,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
            region_name=self.region,
            use_ssl=s3_use_ssl,
            config=Config(
                signature_version='s3v4',
                connect_timeout=s3_timeout,
                read_timeout=s3_timeout,
                retries={'max_attempts': 3}
            )
        )

        logger.info(f"S3Client initialized with endpoint: {self.endpoint_url}, bucket: {self.bucket_name}")
        self._ensure_bucket()

    def _ensure_bucket(self):
        """Create the S3 bucket if it does not already exist."""
        try:
            self.s3_client.head_bucket(Bucket=self.bucket_name)
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code in ('404', 'NoSuchBucket'):
                logger.info(f"Bucket '{self.bucket_name}' not found, creating it")
                try:
                    self.s3_client.create_bucket(Bucket=self.bucket_name)
                    logger.info(f"Bucket '{self.bucket_name}' created successfully")
                except ClientError as create_err:
                    logger.warning(f"Failed to create bucket '{self.bucket_name}': {create_err}")
            else:
                logger.warning(f"Could not check bucket '{self.bucket_name}': {e}")
        except Exception as e:
            logger.warning(f"Could not reach S3 endpoint to check bucket '{self.bucket_name}': {e}")

    def _generate_s3_key(self, user_email: str, filename: str, source_type: str = "user") -> str:
        """Generate an S3-style key with user isolation."""
        timestamp = int(time.time())
        unique_id = str(uuid.uuid4())[:8]
        safe_filename = re.sub(r"[^\w.\-]+", "_", filename)

        if source_type == "tool":
            # Tool-generated files go in a special directory
            return f"users/{user_email}/generated/{timestamp}_{unique_id}_{safe_filename}"
        else:
            # User-uploaded files
            return f"users/{user_email}/uploads/{timestamp}_{unique_id}_{safe_filename}"



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
        Upload a file to S3 storage.

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

                # Convert tags to S3 tag format (URL-encode values for safety)
                tag_set = "&".join([f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in file_tags.items()])

                # Upload to S3
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=s3_key,
                    Body=content_bytes,
                    ContentType=content_type,
                    Tagging=tag_set,
                    Metadata={
                        "user_email": user_email,
                        "original_filename": filename,
                        "source_type": source_type
                    }
                )

                # Get object metadata for response
                response = self.s3_client.head_object(
                    Bucket=self.bucket_name,
                    Key=s3_key
                )

                result = {
                    "key": s3_key,
                    "filename": filename,
                    "size": len(content_bytes),
                    "content_type": content_type,
                    "last_modified": response['LastModified'],
                    "etag": response['ETag'].strip('"'),
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

            except ClientError as e:
                safe_error = preview(
                    e.response.get('Error', {}).get('Message', str(e)),
                    max_chars=ERROR_MESSAGE_MAX_CHARS,
                )
                set_attrs(span, {
                    "key_hash": hash_short(s3_key) if s3_key else None,
                    "file_size": len(content_bytes),
                    "category": _category_from_key(s3_key) if s3_key else "other",
                    "success": False,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(e).__name__,
                    "error_message": safe_error,
                })
                logger.error("S3 upload failed: %s", sanitize_for_logging(safe_error or ""))
                raise Exception("S3 upload failed") from e
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
                logger.error("Error uploading file to S3: %s", sanitize_for_logging(str(e)))
                raise

    async def get_file(self, user_email: str, file_key: str) -> Dict[str, Any]:
        """
        Get a file from S3 storage.

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
                # Verify user has access to this file (check if key starts with user's prefix)
                if not file_key.startswith(f"users/{user_email}/"):
                    logger.warning(
                        "Access denied: user=%s attempted to access key=%s",
                        sanitize_for_logging(user_email),
                        sanitize_for_logging(file_key.split('/')[-1]),
                    )
                    # Persist the access-denied signal BEFORE raising so the
                    # span survives in spans.jsonl even on failure.
                    set_attrs(span, {
                        "access_denied": True,
                        "success": False,
                        "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    })
                    raise Exception("Access denied to file")

                # Get object from S3
                response = self.s3_client.get_object(
                    Bucket=self.bucket_name,
                    Key=file_key
                )

                # Read file content
                content_bytes = response['Body'].read()
                content_base64 = base64.b64encode(content_bytes).decode()

                # Get tags
                try:
                    tags_response = self.s3_client.get_object_tagging(
                        Bucket=self.bucket_name,
                        Key=file_key
                    )
                    tags = {tag['Key']: tag['Value'] for tag in tags_response.get('TagSet', [])}
                except Exception:
                    tags = {}

                # Extract filename from metadata or key
                metadata = response.get('Metadata', {})
                filename = metadata.get('original_filename', file_key.split('/')[-1])

                result = {
                    "key": file_key,
                    "filename": filename,
                    "content_base64": content_base64,
                    "content_type": response['ContentType'],
                    "size": len(content_bytes),
                    "last_modified": response['LastModified'],
                    "etag": response['ETag'].strip('"'),
                    "tags": tags
                }

                category = _category_from_key(file_key)
                logger.info(
                    "File retrieved successfully: category=%s, size=%d bytes, content_type=%s, user=%s",
                    category,
                    len(content_bytes),
                    sanitize_for_logging(response['ContentType']),
                    sanitize_for_logging(user_email),
                )
                logger.debug("Retrieved file key (sanitized): %s", sanitize_for_logging(file_key))

                set_attrs(span, {
                    "filename": safe_label(filename),
                    "content_type": response.get('ContentType'),
                    "file_size": len(content_bytes),
                    "success": True,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                })
                return result

            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    set_attrs(span, {
                        "not_found": True,
                        "success": False,
                        "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                        "error_type": "NoSuchKey",
                    })
                    logger.warning(f"File not found: {sanitize_for_logging(file_key)} for user {sanitize_for_logging(user_email)}")
                    return None
                else:
                    safe_error = preview(
                        e.response.get('Error', {}).get('Message', str(e)),
                        max_chars=ERROR_MESSAGE_MAX_CHARS,
                    )
                    set_attrs(span, {
                        "success": False,
                        "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                        "error_type": type(e).__name__,
                        "error_message": safe_error,
                    })
                    logger.error("S3 get failed: %s", sanitize_for_logging(safe_error or ""))
                    raise Exception("S3 get failed") from e
            except Exception as e:
                set_attrs(span, {
                    "success": False,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(e).__name__,
                    "error_message": preview(str(e), max_chars=ERROR_MESSAGE_MAX_CHARS),
                })
                logger.error("Error getting file from S3: %s", sanitize_for_logging(str(e)))
                raise

    async def list_files(
        self,
        user_email: str,
        file_type: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        List files for a user.

        Args:
            user_email: Email of the user
            file_type: Optional filter by file type ("user" or "tool")
            limit: Maximum number of files to return

        Returns:
            List of file metadata dictionaries
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
                # List objects with user's prefix
                prefix = f"users/{user_email}/"
                if file_type == "tool":
                    prefix = f"users/{user_email}/generated/"
                elif file_type == "user":
                    prefix = f"users/{user_email}/uploads/"

                response = self.s3_client.list_objects_v2(
                    Bucket=self.bucket_name,
                    Prefix=prefix,
                    MaxKeys=limit
                )

                files = []
                for obj in response.get('Contents', []):
                    # Get tags for each object
                    try:
                        tags_response = self.s3_client.get_object_tagging(
                            Bucket=self.bucket_name,
                            Key=obj['Key']
                        )
                        tags = {tag['Key']: tag['Value'] for tag in tags_response.get('TagSet', [])}
                    except Exception:
                        tags = {}

                    # Get metadata
                    try:
                        head_response = self.s3_client.head_object(
                            Bucket=self.bucket_name,
                            Key=obj['Key']
                        )
                        metadata = head_response.get('Metadata', {})
                        content_type = head_response.get('ContentType', 'application/octet-stream')
                        filename = metadata.get('original_filename', obj['Key'].split('/')[-1])
                    except Exception:
                        content_type = 'application/octet-stream'
                        filename = obj['Key'].split('/')[-1]

                    files.append({
                        "key": obj['Key'],
                        "filename": filename,
                        "size": obj['Size'],
                        "content_type": content_type,
                        "last_modified": obj['LastModified'],
                        "etag": obj['ETag'].strip('"'),
                        "tags": tags,
                        "user_email": user_email
                    })

                # Sort by last modified, newest first
                files.sort(key=lambda f: f['last_modified'], reverse=True)

                logger.info(f"Listed {len(files)} files for user {sanitize_for_logging(user_email)}")

                set_attrs(span, {
                    "num_results": len(files),
                    "total_bytes": sum(int(f.get('size', 0) or 0) for f in files),
                    "success": True,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                })
                return files

            except ClientError as e:
                safe_error = preview(
                    e.response.get('Error', {}).get('Message', str(e)),
                    max_chars=ERROR_MESSAGE_MAX_CHARS,
                )
                set_attrs(span, {
                    "success": False,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(e).__name__,
                    "error_message": safe_error,
                })
                logger.error("S3 list failed: %s", sanitize_for_logging(safe_error or ""))
                raise Exception("S3 list failed") from e
            except Exception as e:
                set_attrs(span, {
                    "success": False,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(e).__name__,
                    "error_message": preview(str(e), max_chars=ERROR_MESSAGE_MAX_CHARS),
                })
                logger.error("Error listing files from S3: %s", sanitize_for_logging(str(e)))
                raise

    async def delete_file(self, user_email: str, file_key: str) -> bool:
        """
        Delete a file from S3 storage.

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
                    # Persist the access-denied signal BEFORE raising so the
                    # span survives in spans.jsonl even on failure.
                    set_attrs(span, {
                        "access_denied": True,
                        "success": False,
                        "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    })
                    raise Exception("Access denied to delete file")

                # Delete object from S3
                self.s3_client.delete_object(
                    Bucket=self.bucket_name,
                    Key=file_key
                )

                logger.info(f"File deleted successfully: {sanitize_for_logging(file_key)} for user {sanitize_for_logging(user_email)}")
                set_attrs(span, {
                    "success": True,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                })
                return True

            except ClientError as e:
                if e.response['Error']['Code'] == 'NoSuchKey':
                    set_attrs(span, {
                        "not_found": True,
                        "success": False,
                        "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                        "error_type": "NoSuchKey",
                    })
                    logger.warning(f"File not found for deletion: {sanitize_for_logging(file_key)} for user {sanitize_for_logging(user_email)}")
                    return False
                else:
                    safe_error = preview(
                        e.response.get('Error', {}).get('Message', str(e)),
                        max_chars=ERROR_MESSAGE_MAX_CHARS,
                    )
                    set_attrs(span, {
                        "success": False,
                        "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                        "error_type": type(e).__name__,
                        "error_message": safe_error,
                    })
                    logger.error("S3 delete failed: %s", sanitize_for_logging(safe_error or ""))
                    raise Exception("S3 delete failed") from e
            except Exception as e:
                set_attrs(span, {
                    "success": False,
                    "duration_ms": (time.monotonic_ns() - start_ns) // 1_000_000,
                    "error_type": type(e).__name__,
                    "error_message": preview(str(e), max_chars=ERROR_MESSAGE_MAX_CHARS),
                })
                logger.error("Error deleting file from S3: %s", sanitize_for_logging(str(e)))
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

                if file_data.get('tags', {}).get('source') == 'tool':
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
            logger.error(f"Error getting user stats from S3: {str(e)}")
            raise
