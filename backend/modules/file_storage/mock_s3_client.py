"""
Mock S3 Storage Client using FastAPI TestClient.

This client provides the same interface as S3StorageClient but uses the
S3 mock server via TestClient, eliminating the need for Docker/MinIO in development.
"""

import base64
import hashlib
import logging
import time
import uuid
from typing import Dict, List, Optional, Any

from core.utils import sanitize_for_logging


logger = logging.getLogger(__name__)


class MockS3StorageClient:
    """Mock S3 client using FastAPI TestClient for in-process testing."""

    def __init__(
        self,
        s3_bucket_name: str = None,
    ):
        """Initialize the mock S3 client."""
        from modules.config import config_manager

        self.bucket_name = s3_bucket_name or config_manager.app_settings.s3_bucket_name
        self.endpoint_url = "in-process-mock"  # For health check compatibility
        self.region = "us-east-1"  # For health check compatibility
        self._client = None  # Lazy initialization

        logger.info(f"MockS3StorageClient initialized with bucket: {self.bucket_name}")

    @property
    def client(self):
        """Lazy-load the TestClient to avoid circular imports."""
        if self._client is None:
            from fastapi.testclient import TestClient
            import sys
            import importlib.util
            from pathlib import Path

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
        safe_filename = filename.replace(" ", "_").replace("/", "_")

        if source_type == "tool":
            return f"users/{user_email}/generated/{timestamp}_{unique_id}_{safe_filename}"
        else:
            return f"users/{user_email}/uploads/{timestamp}_{unique_id}_{safe_filename}"

    def _calculate_etag(self, content_bytes: bytes) -> str:
        """Calculate ETag for file content."""
        return hashlib.md5(content_bytes).hexdigest()

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

            # Convert tags to query param format
            tag_param = "&".join([f"{k}={v}" for k, v in file_tags.items()])

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

            result = {
                "key": s3_key,
                "filename": filename,
                "size": len(content_bytes),
                "content_type": content_type,
                "last_modified": None,  # Mock doesn't need exact timestamp
                "etag": etag,
                "tags": file_tags,
                "user_email": user_email
            }

            logger.info(f"File uploaded successfully: {sanitize_for_logging(s3_key)} for user {sanitize_for_logging(user_email)}")
            return result

        except Exception as e:
            logger.error(f"Error uploading file to mock S3: {str(e)}")
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
        try:
            # Verify user has access to this file
            if not file_key.startswith(f"users/{user_email}/"):
                logger.warning(f"Access denied: {sanitize_for_logging(user_email)} attempted to access {sanitize_for_logging(file_key)}")
                raise Exception("Access denied to file")

            # Get object via TestClient
            response = self.client.get(f"/{self.bucket_name}/{file_key}")

            if response.status_code == 404:
                logger.warning(f"File not found: {sanitize_for_logging(file_key)} for user {sanitize_for_logging(user_email)}")
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
                    pass

            # Extract filename from metadata headers
            filename = response.headers.get("x-amz-meta-original_filename", file_key.split('/')[-1])

            result = {
                "key": file_key,
                "filename": filename,
                "content_base64": content_base64,
                "content_type": response.headers.get("Content-Type", "application/octet-stream"),
                "size": len(content_bytes),
                "last_modified": None,
                "etag": response.headers.get("ETag", "").strip('"'),
                "tags": tags
            }

            logger.info(f"File retrieved successfully: {sanitize_for_logging(file_key)} for user {sanitize_for_logging(user_email)}")
            return result

        except Exception as e:
            logger.error(f"Error getting file from mock S3: {str(e)}")
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
        try:
            # Determine prefix
            prefix = f"users/{user_email}/"
            if file_type == "tool":
                prefix = f"users/{user_email}/generated/"
            elif file_type == "user":
                prefix = f"users/{user_email}/uploads/"

            # List via TestClient
            response = self.client.get(
                f"/{self.bucket_name}",
                params={"list-type": "2", "prefix": prefix, "max-keys": str(limit)}
            )

            if response.status_code != 200:
                raise Exception(f"List failed: {response.text}")

            # Parse XML response
            import xml.etree.ElementTree as ET
            root = ET.fromstring(response.text)
            ns = {'s3': 'http://s3.amazonaws.com/doc/2006-03-01/'}
            contents = root.findall(".//s3:Contents", ns) or root.findall(".//Contents")

            files = []
            for content in contents:
                key_elem = content.find("s3:Key", ns)
                if key_elem is None:
                    key_elem = content.find("Key")
                size_elem = content.find("s3:Size", ns) or content.find("Size")
                etag_elem = content.find("s3:ETag", ns) or content.find("ETag")

                if key_elem is None:
                    continue

                key = key_elem.text
                size = int(size_elem.text) if size_elem is not None else 0
                etag = etag_elem.text.strip('"') if etag_elem is not None else ""

                # Get metadata via HEAD
                head_response = self.client.head(f"/{self.bucket_name}/{key}")
                filename = head_response.headers.get("x-amz-meta-original_filename", key.split('/')[-1])
                content_type = head_response.headers.get("Content-Type", "application/octet-stream")

                files.append({
                    "key": key,
                    "filename": filename,
                    "size": size,
                    "content_type": content_type,
                    "last_modified": None,
                    "etag": etag,
                    "tags": {},
                    "user_email": user_email
                })

            logger.info(f"Listed {len(files)} files for user {sanitize_for_logging(user_email)}")
            return files

        except Exception as e:
            logger.error(f"Error listing files from mock S3: {str(e)}")
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
        try:
            # Verify user has access to this file
            if not file_key.startswith(f"users/{user_email}/"):
                logger.warning(f"Access denied for deletion: {sanitize_for_logging(user_email)} attempted to delete {sanitize_for_logging(file_key)}")
                raise Exception("Access denied to delete file")

            # Delete via TestClient
            response = self.client.delete(f"/{self.bucket_name}/{file_key}")

            if response.status_code == 404:
                logger.warning(f"File not found for deletion: {sanitize_for_logging(file_key)} for user {sanitize_for_logging(user_email)}")
                return False

            if response.status_code != 204:
                raise Exception(f"Delete failed: {response.text}")

            logger.info(f"File deleted successfully: {sanitize_for_logging(file_key)} for user {sanitize_for_logging(user_email)}")
            return True

        except Exception as e:
            logger.error(f"Error deleting file from mock S3: {str(e)}")
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
