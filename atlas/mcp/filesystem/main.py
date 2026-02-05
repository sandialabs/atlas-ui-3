#!/usr/bin/env python3
"""
Filesystem MCP Server using FastMCP
Provides file system read/write operations through MCP protocol.
"""

from pathlib import Path
from typing import Any, Dict

from fastmcp import FastMCP

# Initialize the MCP server
mcp = FastMCP("Filesystem")

# Base path for file operations (security constraint)
BASE_PATH = Path(".").resolve()


def _safe_path(path: str) -> Path:
    """Ensure path is within base directory for security."""
    requested_path = Path(path)
    if requested_path.is_absolute():
        full_path = requested_path
    else:
        full_path = BASE_PATH / requested_path
    
    resolved_path = full_path.resolve()
    
    # Ensure the path is within BASE_PATH
    try:
        resolved_path.relative_to(BASE_PATH)
    except ValueError:
        raise PermissionError("Access denied: path outside base directory")
    
    return resolved_path


@mcp.tool
def read_file(path: str) -> Dict[str, Any]:
    """
    Read the complete contents of a text file from the secure filesystem.

    This tool provides safe file reading capabilities with security restrictions:
    - Only allows access to files within the designated base directory
    - Supports both absolute and relative paths (relative paths are resolved from base)
    - Automatically handles UTF-8 text encoding
    - Returns file metadata along with content

    **Security Features:**
    - Path traversal protection (no access outside base directory)
    - File existence validation
    - Error handling for permission and access issues

    **Use Cases:**
    - Reading configuration files, logs, documentation
    - Loading data files for processing
    - Inspecting file contents for analysis

    Args:
        path: File path to read (string). Can be relative to base directory or absolute within allowed area.
        
    Returns:
        Dictionary containing:
        - success: boolean indicating operation success
        - content: string with complete file contents (UTF-8)
        - path: resolved relative path from base directory
        - size: file size in bytes
        Or error message if operation fails
    """
    try:
        file_path = _safe_path(path)
        if not file_path.exists():
            return {"results": {"error": f"File not found: {path}"}}
        
        if file_path.is_dir():
            return {"results": {"error": f"Path is a directory: {path}"}}
        
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        return {
            "results": {
                "content": content,
                "size": len(content),
                "path": str(file_path.relative_to(BASE_PATH))
            }
        }
    except PermissionError as e:
        return {"results": {"error": str(e)}}
    except Exception as e:
        return {"results": {"error": f"Error reading file: {str(e)}"}}


@mcp.tool
def write_file(path: str, content: str) -> Dict[str, Any]:
    """
    Write text content to a file, creating the file and any necessary parent directories.

    This tool provides safe file writing capabilities with automatic directory creation:
    - Creates parent directories if they don't exist
    - Overwrites existing files completely with new content
    - Uses UTF-8 encoding for text files
    - Security restrictions apply (no access outside base directory)

    **Security Features:**
    - Path traversal protection
    - Safe directory creation with proper permissions
    - Error handling for permission and access issues

    **Use Cases:**
    - Creating configuration files, reports, logs
    - Saving processed data, analysis results
    - Generating documentation or output files
    - Creating backup copies of modified content

    Args:
        path: File path to write (string). Can be relative to base directory or absolute within allowed area.
        content: Text content to write to the file (string, UTF-8 encoded)
        
    Returns:
        Dictionary containing:
        - success: boolean indicating operation success
        - path: resolved relative path from base directory
        - size: number of characters written
        Or error message if operation fails
    """
    try:
        file_path = _safe_path(path)
        
        # Create parent directories if they don't exist
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        
        return {
            "results": {
                "success": True,
                "path": str(file_path.relative_to(BASE_PATH)),
                "size": len(content)
            }
        }
    except PermissionError as e:
        return {"results": {"error": str(e)}}
    except Exception as e:
        return {"results": {"error": f"Error writing file: {str(e)}"}}


@mcp.tool
def list_directory(path: str = ".") -> Dict[str, Any]:
    """
    List all files and subdirectories within a specified directory with detailed metadata.

    This tool provides comprehensive directory browsing capabilities:
    - Lists both files and subdirectories with type identification
    - Provides metadata including size, modification time, and permissions
    - Supports recursive directory exploration within security boundaries
    - Defaults to current working directory if no path specified

    **Security Features:**
    - Path traversal protection (no access outside base directory)
    - Directory existence validation
    - Safe handling of permission-restricted items

    **Returned Information:**
    - Item name and type (file/directory)
    - File size (for files)
    - Last modification timestamp
    - Basic permission information

    **Use Cases:**
    - Exploring project structure and organization
    - Finding specific files or directories
    - Checking file metadata before processing
    - Navigating filesystem for file operations

    Args:
        path: Directory path to list (string, defaults to current directory)
        
    Returns:
        Dictionary containing:
        - path: resolved directory path
        - items: list of dictionaries with file/directory information
        Each item includes: name, type, size (if file), modified_time, is_directory
        Or error message if operation fails
    """
    try:
        dir_path = _safe_path(path)
        if not dir_path.exists():
            return {"results": {"error": f"Directory not found: {path}"}}
        
        if not dir_path.is_dir():
            return {"results": {"error": f"Path is not a directory: {path}"}}
        
        items = []
        for item in dir_path.iterdir():
            items.append({
                "name": item.name,
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else None
            })
        
        return {
            "results": {
                "path": str(dir_path.relative_to(BASE_PATH)),
                "items": sorted(items, key=lambda x: (x["type"], x["name"]))
            }
        }
    except PermissionError as e:
        return {"results": {"error": str(e)}}
    except Exception as e:
        return {"results": {"error": f"Error listing directory: {str(e)}"}}


@mcp.tool
def create_directory(path: str) -> Dict[str, Any]:
    """
    Create a new directory, including any necessary parent directories in the path.

    This tool provides comprehensive directory creation capabilities:
    - Creates the target directory and all parent directories if they don't exist
    - Uses safe permissions and follows system defaults
    - Handles existing directories gracefully (no error if already exists)
    - Security restrictions apply (no access outside base directory)

    **Security Features:**
    - Path traversal protection
    - Safe directory creation with proper permissions
    - Error handling for permission and access issues

    **Use Cases:**
    - Setting up project folder structures
    - Creating organized storage for different file types
    - Preparing directories for batch file operations
    - Establishing workspace organization

    Args:
        path: Directory path to create (string). Can be relative to base or absolute within allowed area.
        
    Returns:
        Dictionary containing:
        - success: boolean indicating operation success
        - path: resolved relative path from base directory
        Or error message if operation fails
    """
    try:
        dir_path = _safe_path(path)
        dir_path.mkdir(parents=True, exist_ok=True)
        
        return {
            "results": {
                "success": True,
                "path": str(dir_path.relative_to(BASE_PATH))
            }
        }
    except PermissionError as e:
        return {"results": {"error": str(e)}}
    except Exception as e:
        return {"results": {"error": f"Error creating directory: {str(e)}"}}


@mcp.tool
def delete_file(path: str) -> Dict[str, Any]:
    """
    Permanently delete a single file from the filesystem with safety validations.

    This tool provides secure file deletion capabilities:
    - Only deletes files (not directories - use appropriate directory removal tools)
    - Validates file existence before attempting deletion
    - Provides clear error messages for different failure conditions
    - Security restrictions apply (no access outside base directory)

    **Security Features:**
    - Path traversal protection
    - File vs directory validation (prevents accidental directory deletion)
    - File existence verification
    - Error handling for permission issues

    **Important Notes:**
    - This operation is permanent and cannot be undone
    - Only works on files, not directories
    - Will fail safely if target is a directory or doesn't exist

    **Use Cases:**
    - Cleaning up temporary or outdated files
    - Removing processed files after successful operations
    - Managing storage space by removing unnecessary files
    - Maintaining clean project directories

    Args:
        path: File path to delete (string). Can be relative to base or absolute within allowed area.
        
    Returns:
        Dictionary containing:
        - success: boolean indicating operation success
        - path: resolved relative path of deleted file
        Or error message if operation fails (file not found, is directory, permission denied)
    """
    try:
        file_path = _safe_path(path)
        if not file_path.exists():
            return {"results": {"error": f"File not found: {path}"}}
        
        if file_path.is_dir():
            return {"results": {"error": f"Path is a directory (use rmdir): {path}"}}
        
        file_path.unlink()
        return {
            "results": {
                "success": True,
                "path": str(file_path.relative_to(BASE_PATH))
            }
        }
    except PermissionError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"Error deleting file: {str(e)}"}


@mcp.tool
def file_exists(path: str) -> Dict[str, Any]:
    """
    Check if a file or directory exists.
    
    Args:
        path: Path to check
        
    Returns:
        Dictionary with existence status and file type information
    """
    try:
        file_path = _safe_path(path)
        return {
            "results": {
                "exists": file_path.exists(),
                "is_file": file_path.is_file(),
                "is_directory": file_path.is_dir(),
                "path": str(file_path.relative_to(BASE_PATH))
            }
        }
    except PermissionError as e:
        return {"results": {"error": str(e)}}
    except Exception as e:
        return {"results": {"error": f"Error checking file: {str(e)}"}}


if __name__ == "__main__":
    mcp.run()