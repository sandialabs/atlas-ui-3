"""File storage module for the chat backend.

This module provides:
- S3 storage client for file operations
- File management utilities
- Content type detection and categorization
- CLI tools for file operations
"""

from .s3_client import S3StorageClient
from .manager import FileManager

# Create default instances
s3_client = S3StorageClient()
file_manager = FileManager(s3_client)

__all__ = [
    "S3StorageClient",
    "FileManager",
    "s3_client",
    "file_manager",
]