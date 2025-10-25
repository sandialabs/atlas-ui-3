#!/usr/bin/env python3
"""
Execution environment management module for code executor.
Handles creation, cleanup, and file operations for isolated execution environments.
"""

import base64
import binascii
import logging
import os
import shutil
import tempfile
import traceback
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CodeExecutionError(Exception):
    """Raised when code execution fails."""
    pass


def create_execution_environment() -> Path:
    """Create a secure execution environment with UUID-based directory."""
    try:
        exec_id = str(uuid.uuid4())
        base_dir = Path(tempfile.gettempdir()) / "secure_code_exec"
        exec_dir = base_dir / exec_id
        
        # Create directory structure
        base_dir.mkdir(exist_ok=True)
        exec_dir.mkdir(exist_ok=True)
        
        logger.info(f"Created execution environment: {exec_dir}")
        return exec_dir
    except Exception as e:
        error_msg = f"Failed to create execution environment: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise CodeExecutionError(error_msg)


def cleanup_execution_environment(exec_dir: Optional[Path]):
    """Clean up the execution environment."""
    try:
        if exec_dir and exec_dir.exists():
            shutil.rmtree(exec_dir)
            logger.info(f"Cleaned up execution environment: {exec_dir}")
    except Exception as e:
        logger.warning(f"Failed to cleanup execution environment {exec_dir}: {str(e)}")
        logger.warning(f"Traceback: {traceback.format_exc()}")


def save_file_to_execution_dir(filename: str, file_data_base64: str, exec_dir: Path) -> str:
    """
    Save a base64-encoded file to the execution directory.
    
    Args:
        filename: Name of the file
        file_data_base64: Base64-encoded file data
        exec_dir: Execution directory
        
    Returns:
        The filename that was saved
    """
    try:
        logger.info(f"Saving file {filename} to execution directory: {exec_dir}")
        
        # Decode the base64 data
        file_data = base64.b64decode(file_data_base64)
        
        # Ensure filename is safe (no path traversal)
        safe_filename = os.path.basename(filename)
        file_path = exec_dir / safe_filename
        
        # Write the file
        with open(file_path, 'wb') as f:
            f.write(file_data)
        
        logger.info(f"Successfully saved file: {safe_filename} ({len(file_data)} bytes)")
        return safe_filename
        
    except binascii.Error as e:
        error_msg = f"Invalid base64 data for file {filename}: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise ValueError(error_msg)
    except Exception as e:
        error_msg = f"Failed to save file {filename}: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise ValueError(error_msg)
