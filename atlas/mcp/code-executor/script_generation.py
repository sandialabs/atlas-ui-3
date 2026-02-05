#!/usr/bin/env python3
"""
Script generation module for code executor.
Handles creation of safe execution scripts with security overrides.
"""

import logging
import traceback
from pathlib import Path

# Import CodeExecutionError class definition locally to avoid circular imports
class CodeExecutionError(Exception):
    """Raised when code execution fails."""
    pass

logger = logging.getLogger(__name__)


def create_safe_execution_script(code: str, exec_dir: Path) -> Path:
    """
    Create a Python script with the user code wrapped in safety measures.
    
    Args:
        code: User's Python code
        exec_dir: Execution directory
    Returns:
        Path to the created script
    """
    try:
        # Indent each line of user code to fit inside the try block
        indented_code = '\n'.join('    ' + line for line in code.split('\n'))
        
        script_content = f'''#!/usr/bin/env python3
import sys
import os
import json
import traceback
from pathlib import Path

# Change to execution directory
os.chdir(r"{exec_dir}")

# Configure matplotlib for safe plotting
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend that saves to files
matplotlib.rcParams['savefig.directory'] = r"{exec_dir}"  # Default save directory

# Restrict file operations to current directory only
original_open = open

def safe_open(file, mode='r', **kwargs):
    """Override open to restrict file access to execution directory, with exceptions for safe plotting libraries."""
    file_path = Path(file).resolve()
    exec_path = Path(r"{exec_dir}").resolve()
    
    try:
        file_path.relative_to(exec_path)
        # File is in execution directory - always allow
        return original_open(file, mode, **kwargs)
    except ValueError:
        # File is outside execution directory - check if it's an allowed library file
        file_str = str(file_path)
        
        # Allow matplotlib and seaborn configuration and data files (read-only)
        allowed_paths = [
            '/matplotlib/',
            '/seaborn/', 
            '/site-packages/matplotlib/',
            '/site-packages/seaborn/',
            'matplotlib/mpl-data/',
            'matplotlib/backends/',
            'matplotlib/font_manager.py',
            'seaborn/data/',
            'seaborn/_core/',
            'numpy/core/',
            'pandas/io/',
            '/usr/share/fonts/',
            '/usr/local/share/fonts/',
            'fontconfig/',
            '.cache/matplotlib/',
            '/tmp/matplotlib-',
            '/home/.matplotlib/',
            '/.matplotlib/',
        ]
        
        # Check if file path contains any allowed library paths  
        is_allowed_path = any(allowed_path in file_str for allowed_path in allowed_paths)
        
        if not is_allowed_path:
            raise PermissionError(f"File access outside execution directory not allowed: {{file}}")
            
        # Allow read access to all allowed paths
        if 'r' in mode and 'w' not in mode and 'a' not in mode and '+' not in mode:
            return original_open(file, mode, **kwargs)
        
        # Allow write access only to matplotlib cache directories
        if ('.cache/matplotlib/' in file_str or 
            'matplotlib/fontList.cache' in file_str or
            'matplotlib/tex.cache' in file_str or
            '/tmp/matplotlib-' in file_str or
            '/.matplotlib/' in file_str):
            return original_open(file, mode, **kwargs)
            
        # Deny write access to other external files
        if 'w' in mode or 'a' in mode or '+' in mode:
            raise PermissionError(f"Write access outside execution directory not allowed: {{file}}")
            
        return original_open(file, mode, **kwargs)

# Override built-in open
if isinstance(__builtins__, dict):
    __builtins__['open'] = safe_open
else:
    __builtins__.open = safe_open

# Capture output
import io
import sys

stdout_buffer = io.StringIO()
stderr_buffer = io.StringIO()

# Redirect stdout and stderr
old_stdout = sys.stdout
old_stderr = sys.stderr
sys.stdout = stdout_buffer
sys.stderr = stderr_buffer

execution_error = None
error_traceback = None

try:
    # User code starts here (matplotlib/seaborn should now work with plotting)
{indented_code}
    # User code ends here
    
    # Auto-save any open matplotlib figures so they surface in UI even if user didn't call plt.savefig()
    try:
        # Only run if matplotlib/pyplot is available
        if 'matplotlib' in sys.modules:
            import matplotlib.pyplot as plt  # type: ignore
            fig_nums = list(plt.get_fignums())
            if fig_nums:
                for _fig_num in fig_nums:
                    try:
                        fig = plt.figure(_fig_num)
                        out_name = "plot_" + str(_fig_num) + ".png"
                        fig.savefig(out_name)
                    except Exception:
                        # Don't fail user code on save issues
                        pass
                try:
                    plt.close('all')
                except Exception:
                    pass
    except Exception:
        # Silent best-effort; plotting is optional
        pass
    
except Exception as e:
    execution_error = e
    error_traceback = traceback.format_exc()
    print(f"Execution error: {{type(e).__name__}}: {{str(e)}}", file=sys.stderr)
    print(f"Traceback:\\n{{error_traceback}}", file=sys.stderr)

finally:
    # Restore stdout and stderr
    sys.stdout = old_stdout
    sys.stderr = old_stderr
    
    # Output results
    result = {{
        "stdout": stdout_buffer.getvalue(),
        "stderr": stderr_buffer.getvalue(),
        "success": execution_error is None,
        "error_type": type(execution_error).__name__ if execution_error else None,
        "error_traceback": error_traceback
    }}
    
    print(json.dumps(result))
'''
        
        script_path = exec_dir / "exec_script.py"
        with open(script_path, 'w') as f:
            f.write(script_content)
        
        logger.info(f"Created execution script: {script_path}")
        return script_path
    
    except Exception as e:
        error_msg = f"Failed to create execution script: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise CodeExecutionError(error_msg)
