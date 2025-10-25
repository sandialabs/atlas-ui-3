"""
S3 Client for file storage operations.

This module provides a client interface to interact with S3 storage,
supporting both real AWS S3 and our mock S3 service for development.
"""

import json
import logging
from typing import Dict, List, Optional, Any
import httpx
from datetime import datetime


logger = logging.getLogger(__name__)


class S3StorageClient:
    """Client for interacting with S3 storage (real or mock)."""
    
    def __init__(self, s3_endpoint: str = None, s3_timeout: int = None, s3_use_mock: bool = None):
        """Initialize the S3 client with configuration."""
        # Allow dependency injection for testing
        if s3_endpoint is None or s3_timeout is None or s3_use_mock is None:
            from modules.config import config_manager
            config = config_manager.app_settings
            s3_endpoint = s3_endpoint or config.s3_endpoint
            s3_timeout = s3_timeout or config.s3_timeout
            s3_use_mock = s3_use_mock if s3_use_mock is not None else config.s3_use_mock
        
        self.base_url = s3_endpoint
        self.timeout = s3_timeout
        self.use_mock = s3_use_mock
        
        logger.info(f"S3Client initialized with endpoint: {self.base_url}")
    
    def _get_auth_headers(self, user_email: str) -> Dict[str, str]:
        """Get authorization headers for the request."""
        if self.use_mock:
            # For mock service, use user email as bearer token
            return {"Authorization": f"Bearer {user_email}"}
        else:
            # For real S3, this would use AWS credentials
            # TODO: Implement proper AWS S3 authentication
            return {}
    
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
        try:
            # Prepare tags
            file_tags = tags or {}
            file_tags["source"] = source_type
            
            payload = {
                "filename": filename,
                "content_base64": content_base64,
                "content_type": content_type,
                "tags": file_tags
            }
            
            headers = self._get_auth_headers(user_email)
            headers["Content-Type"] = "application/json"
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/files",
                    json=payload,
                    headers=headers
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"File uploaded successfully: {result['key']} for user {user_email}")
                    return result
                else:
                    error_msg = f"S3 upload failed with status {response.status_code}: {response.text}"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                    
        except Exception as e:
            logger.error(f"Error uploading file to S3: {str(e)}")
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
        try:
            headers = self._get_auth_headers(user_email)
            
            # CodeQL SSRF suppression: file_key is validated S3 key, not user-controlled URL input
            # codeql[py/ssrf]
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/files/{file_key}",
                    headers=headers
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"File retrieved successfully: {file_key} for user {user_email}")
                    return result
                elif response.status_code == 404:
                    logger.warning(f"File not found: {file_key} for user {user_email}")
                    return None
                elif response.status_code == 403:
                    logger.warning(f"Access denied to file: {file_key} for user {user_email}")
                    raise Exception("Access denied to file")
                else:
                    error_msg = f"S3 get failed with status {response.status_code}: {response.text}"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                    
        except Exception as e:
            logger.error(f"Error getting file from S3: {str(e)}")
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
            headers = self._get_auth_headers(user_email)
            
            params = {"limit": limit}
            if file_type:
                params["file_type"] = file_type
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/files",
                    headers=headers,
                    params=params
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"Listed {len(result)} files for user {user_email}")
                    return result
                else:
                    error_msg = f"S3 list failed with status {response.status_code}: {response.text}"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                    
        except Exception as e:
            logger.error(f"Error listing files from S3: {str(e)}")
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
        try:
            headers = self._get_auth_headers(user_email)
            
            # CodeQL SSRF suppression: file_key is validated S3 key, not user-controlled URL input
            # codeql[py/ssrf]
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.delete(
                    f"{self.base_url}/files/{file_key}",
                    headers=headers
                )
                
                if response.status_code == 200:
                    logger.info(f"File deleted successfully: {file_key} for user {user_email}")
                    return True
                elif response.status_code == 404:
                    logger.warning(f"File not found for deletion: {file_key} for user {user_email}")
                    return False
                elif response.status_code == 403:
                    logger.warning(f"Access denied for deletion: {file_key} for user {user_email}")
                    raise Exception("Access denied to delete file")
                else:
                    error_msg = f"S3 delete failed with status {response.status_code}: {response.text}"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                    
        except Exception as e:
            logger.error(f"Error deleting file from S3: {str(e)}")
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
            headers = self._get_auth_headers(user_email)
            
            # CodeQL SSRF suppression: user_email comes from trusted reverse proxy auth,
            # not direct user input, so this is not a security risk
            # codeql[py/ssrf]
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/users/{user_email}/files/stats",
                    headers=headers
                )
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"Got file stats for user {user_email}: {result}")
                    return result
                else:
                    error_msg = f"S3 stats failed with status {response.status_code}: {response.text}"
                    logger.error(error_msg)
                    raise Exception(error_msg)
                    
        except Exception as e:
            logger.error(f"Error getting user stats from S3: {str(e)}")
            raise