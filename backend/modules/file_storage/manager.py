"""
File management utilities for handling files across the application.

This module provides utilities for:
- Content type detection
- File categorization  
- File metadata management
- Integration with S3 storage
"""

import logging
from typing import Dict, List, Optional, Any
from .s3_client import S3StorageClient

logger = logging.getLogger(__name__)


class FileManager:
    """Centralized file management with S3 integration."""
    
    def __init__(self, s3_client: Optional[S3StorageClient] = None):
        """Initialize with optional S3 client dependency injection."""
        self.s3_client = s3_client or S3StorageClient()
    
    def get_content_type(self, filename: str) -> str:
        """Determine content type based on filename."""
        extension = filename.lower().split('.')[-1] if '.' in filename else ''
        
        content_types = {
            'txt': 'text/plain',
            'md': 'text/markdown',
            'json': 'application/json',
            'csv': 'text/csv',
            'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'pdf': 'application/pdf',
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'gif': 'image/gif',
            'py': 'text/x-python',
            'js': 'application/javascript',
            'html': 'text/html',
            'css': 'text/css'
        }
        
        return content_types.get(extension, 'application/octet-stream')
    
    def categorize_file_type(self, filename: str) -> str:
        """Categorize file based on extension."""
        extension = filename.lower().split('.')[-1] if '.' in filename else ''
        
        code_extensions = {'py', 'js', 'jsx', 'ts', 'tsx', 'html', 'css', 'java', 'cpp', 'c', 'rs', 'go', 'php', 'rb', 'swift'}
        image_extensions = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'svg', 'webp'}
        data_extensions = {'csv', 'json', 'xlsx', 'xls', 'xml'}
        document_extensions = {'pdf', 'doc', 'docx', 'txt', 'md', 'rtf'}
        
        if extension in code_extensions:
            return 'code'
        elif extension in image_extensions:
            return 'image'
        elif extension in data_extensions:
            return 'data'
        elif extension in document_extensions:
            return 'document'
        else:
            return 'other'
    
    def get_file_extension(self, filename: str) -> str:
        """Extract file extension from filename."""
        return '.' + filename.split('.')[-1] if '.' in filename else ''
    
    def get_canvas_file_type(self, file_ext: str) -> str:
        """Determine canvas display type based on file extension."""
        image_exts = {'.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico'}
        text_exts = {'.txt', '.md', '.rst', '.csv', '.json', '.xml', '.yaml', '.yml', 
                    '.py', '.js', '.css', '.ts', '.jsx', '.tsx', '.vue', '.sql'}
        
        if file_ext in image_exts:
            return 'image'
        elif file_ext == '.pdf':
            return 'pdf'
        elif file_ext in {'.html', '.htm'}:
            return 'html'
        elif file_ext in text_exts:
            return 'text'
        else:
            return 'other'
    
    def should_display_in_canvas(self, filename: str) -> bool:
        """Check if file should be displayed in canvas based on file type."""
        canvas_extensions = {
            # Images
            '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico',
            # Documents
            '.pdf', '.html', '.htm',
            # Text/code files
            '.txt', '.md', '.rst', '.csv', '.json', '.xml', '.yaml', '.yml',
            '.py', '.js', '.css', '.ts', '.jsx', '.tsx', '.vue', '.sql'
        }
        
        file_ext = self.get_file_extension(filename).lower()
        return file_ext in canvas_extensions
    
    async def upload_file(
        self,
        user_email: str,
        filename: str,
        content_base64: str,
        source_type: str = "user",
        tags: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """Upload a file with automatic content type detection."""
        content_type = self.get_content_type(filename)
        
        return await self.s3_client.upload_file(
            user_email=user_email,
            filename=filename,
            content_base64=content_base64,
            content_type=content_type,
            tags=tags,
            source_type=source_type
        )
    
    async def upload_multiple_files(
        self,
        user_email: str,
        files: Dict[str, str],
        source_type: str = "user"
    ) -> Dict[str, str]:
        """Upload multiple files and return filename -> s3_key mapping."""
        uploaded_files = {}
        
        for filename, base64_content in files.items():
            try:
                file_metadata = await self.upload_file(
                    user_email=user_email,
                    filename=filename,
                    content_base64=base64_content,
                    source_type=source_type
                )
                uploaded_files[filename] = file_metadata["key"]
                logger.info(f"File uploaded: {filename} -> {file_metadata['key']}")
            except Exception as exc:
                logger.error(f"Failed to upload file {filename}: {exc}")
                raise
        
        return uploaded_files
    
    def organize_files_metadata(self, file_references: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Organize files metadata by category for UI display."""
        files_metadata = []
        
        for filename, file_metadata in file_references.items():
            # Determine source type from tags or metadata
            tags = file_metadata.get("tags", {})
            source_type = tags.get("source", "uploaded")
            source_tool = tags.get("source_tool", None)
            
            file_info = {
                'filename': filename,
                's3_key': file_metadata.get("key", ""),
                'size': file_metadata.get("size", 0),
                'type': self.categorize_file_type(filename),
                'source': source_type,
                'source_tool': source_tool,
                'extension': filename.split('.')[-1] if '.' in filename else '',
                'last_modified': file_metadata.get("last_modified"),
                'content_type': file_metadata.get("content_type", "application/octet-stream"),
                'can_display_in_canvas': self.should_display_in_canvas(filename)
            }
            files_metadata.append(file_info)
        
        # Group by category
        categorized = {
            'code': [f for f in files_metadata if f['type'] == 'code'],
            'image': [f for f in files_metadata if f['type'] == 'image'], 
            'data': [f for f in files_metadata if f['type'] == 'data'],
            'document': [f for f in files_metadata if f['type'] == 'document'],
            'other': [f for f in files_metadata if f['type'] == 'other']
        }
        
        return {
            'total_files': len(files_metadata),
            'files': files_metadata,
            'categories': categorized
        }

    async def upload_files_from_base64(
        self,
        files: List[Dict[str, Any]],
        user_email: str,
        source_type: str = "tool"
    ) -> Dict[str, Dict[str, Any]]:
        """Upload multiple base64 files and return filename -> metadata mapping.

        Args:
            files: List of dicts { filename, content, mime_type? }
            user_email: Owner for auth/partitioning
            source_type: "user" or "tool"

        Returns:
            Dict mapping filename -> metadata dict compatible with organize_files_metadata
        """
        uploaded_refs: Dict[str, Dict[str, Any]] = {}
        for f in files:
            try:
                filename = f.get("filename")
                content_b64 = f.get("content")
                mime_type = f.get("mime_type") or self.get_content_type(filename or "")
                if not filename or not content_b64:
                    logger.warning("Skipping upload: missing filename or content")
                    continue
                meta = await self.s3_client.upload_file(
                    user_email=user_email,
                    filename=filename,
                    content_base64=content_b64,
                    content_type=mime_type,
                    tags={"source": source_type},
                    source_type=source_type,
                )
                # Normalize minimal reference for session context
                uploaded_refs[filename] = {
                    "key": meta.get("key"),
                    "content_type": meta.get("content_type", mime_type),
                    "size": meta.get("size", 0),
                    "source": source_type,
                    "last_modified": meta.get("last_modified"),
                    "tags": {"source": source_type},
                }
            except Exception as e:
                logger.error(f"Failed to upload artifact {f.get('filename')}: {e}")
        return uploaded_refs
    
    def get_canvas_displayable_files(
        self, 
        result_dict: Dict[str, Any], 
        uploaded_files: Dict[str, str]
    ) -> List[Dict]:
        """Extract files from tool result that should be displayed in canvas."""
        canvas_files = []
        
        # Check returned_files array (preferred format)
        if "returned_files" in result_dict and isinstance(result_dict["returned_files"], list):
            for file_info in result_dict["returned_files"]:
                if isinstance(file_info, dict) and "filename" in file_info:
                    filename = file_info["filename"]
                    
                    if self.should_display_in_canvas(filename) and filename in uploaded_files:
                        canvas_files.append({
                            "filename": filename,
                            "type": self.get_canvas_file_type(self.get_file_extension(filename).lower()),
                            "s3_key": uploaded_files[filename],
                            "size": file_info.get("size", 0),
                            "source": "tool_generated"
                        })
        
        # Check legacy single file format
        elif "returned_file_name" in result_dict and "returned_file_base64" in result_dict:
            filename = result_dict["returned_file_name"]
            
            if self.should_display_in_canvas(filename) and filename in uploaded_files:
                canvas_files.append({
                    "filename": filename,
                    "type": self.get_canvas_file_type(self.get_file_extension(filename).lower()),
                    "s3_key": uploaded_files[filename],
                    "size": 0,  # Size not available in legacy format
                    "source": "tool_generated"
                })
        
        logger.info(f"Found {len(canvas_files)} canvas-displayable files: {[f['filename'] for f in canvas_files]}")
        return canvas_files
    
    async def get_file_content(self, user_email: str, filename: str, s3_key: str) -> Optional[str]:
        """Get base64 content of a file by S3 key."""
        try:
            file_data = await self.s3_client.get_file(user_email, s3_key)
            if file_data:
                return file_data["content_base64"]
            else:
                logger.warning(f"File not found in S3: {s3_key}")
                return None
        except Exception as exc:
            logger.error(f"Error getting file content for {filename}: {exc}")
            return None