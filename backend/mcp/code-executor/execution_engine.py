#!/usr/bin/env python3
"""
Execution engine module for code executor.
Handles safe subprocess execution of Python scripts with resource limits.
"""

import json
import logging
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Dict, Any

logger = logging.getLogger(__name__)


def execute_code_safely(script_path: Path, timeout: int = 30) -> Dict[str, Any]:
    """
    Execute Python script safely with resource limits.
    
    Args:
        script_path: Path to the script to execute
        timeout: Maximum execution time in seconds
        
    Returns:
        Execution results
    """
    try:
        logger.info(f"Executing script: {script_path} with timeout: {timeout}s")
        
        # Execute with subprocess for isolation
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=script_path.parent
        )
        
        logger.info(f"Script execution completed with return code: {result.returncode}")
        
        if result.returncode == 0:
            # Parse the JSON output from the script
            try:
                execution_result = json.loads(result.stdout.strip())
                if not execution_result.get("success", True):
                    logger.warning(f"Code execution failed: {execution_result.get('stderr', 'Unknown error')}")
                    if execution_result.get("error_traceback"):
                        logger.error(f"User code traceback:\n{execution_result['error_traceback']}")
                return execution_result
            except json.JSONDecodeError as e:
                error_msg = "Failed to parse execution output"
                logger.error(f"{error_msg}: {str(e)}")
                logger.error(f"Raw stdout: {result.stdout}")
                logger.error(f"Raw stderr: {result.stderr}")
                return {
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "success": False,
                    "error": error_msg,
                    "error_type": "JSONDecodeError"
                }
        else:
            error_msg = f"Script execution failed with return code {result.returncode}"
            logger.error(error_msg)
            logger.error(f"stdout: {result.stdout}")
            logger.error(f"stderr: {result.stderr}")
            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "success": False,
                "error": error_msg,
                "error_type": "SubprocessError"
            }
    
    except subprocess.TimeoutExpired as e:
        error_msg = f"Code execution timed out after {timeout} seconds"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {
            "stdout": "",
            "stderr": "",
            "success": False,
            "error": error_msg,
            "error_type": "TimeoutError"
        }
    except Exception as e:
        error_msg = f"Execution error: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {
            "stdout": "",
            "stderr": "",
            "success": False,
            "error": error_msg,
            "error_type": type(e).__name__
        }
