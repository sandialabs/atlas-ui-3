#!/usr/bin/env python3
"""
Security checking module for code executor.
Contains AST-based security analysis for Python code.
"""

import ast
import logging
import traceback
from typing import List

logger = logging.getLogger(__name__)


class CodeSecurityError(Exception):
    """Raised when code fails security checks."""
    pass


class SecurityChecker(ast.NodeVisitor):
    """AST visitor to check for dangerous code patterns."""
    
    def __init__(self):
        self.violations = []
        self.imported_modules = set()
        
        # Dangerous modules that should never be imported
        self.forbidden_modules = {
            'os', 'sys', 'subprocess', 'socket', 'urllib', 'urllib2', 'urllib3',
            'requests', 'http', 'ftplib', 'smtplib', 'telnetlib', 'webbrowser',
            'ctypes', 'multiprocessing', 'threading', 'asyncio', 'concurrent',
            'pickle', 'dill', 'shelve', 'dbm', 'sqlite3', 'pymongo',
            'paramiko', 'fabric', 'pexpect', 'pty', 'tty',
            'importlib', '__builtin__', 'builtins', 'imp'
        }
        
        # Allowed safe modules for data analysis
        self.allowed_modules = {
            'numpy', 'np', 'pandas', 'pd', 'matplotlib', 'plt', 'seaborn', 'sns',
            'scipy', 'sklearn', 'PIL', 'pillow', 'openpyxl',
            'json', 'csv', 'datetime', 'math', 'statistics', 'random', 're',
            'collections', 'itertools', 'functools', 'operator', 'copy',
            'decimal', 'fractions', 'pathlib', 'typing'
        }
        
        # Dangerous function names
        self.forbidden_functions = {
            'eval', 'exec', 'compile', '__import__', 'getattr', 'setattr', 'delattr',
            'hasattr', 'callable', 'isinstance', 'issubclass', 'super', 'globals',
            'locals', 'vars', 'dir', 'help', 'input', 'raw_input', 'exit', 'quit'
        }

    def visit_Import(self, node):
        """Check import statements."""
        for alias in node.names:
            module_name = alias.name.split('.')[0]
            self.imported_modules.add(module_name)
            
            if module_name in self.forbidden_modules:
                self.violations.append(f"Forbidden module import: {module_name}")
            elif module_name not in self.allowed_modules:
                self.violations.append(f"Unauthorized module import: {module_name}")
        
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        """Check from...import statements."""
        if node.module:
            module_name = node.module.split('.')[0]
            self.imported_modules.add(module_name)
            
            if module_name in self.forbidden_modules:
                self.violations.append(f"Forbidden module import: {module_name}")
            elif module_name not in self.allowed_modules:
                self.violations.append(f"Unauthorized module import: {module_name}")
        
        self.generic_visit(node)

    def visit_Call(self, node):
        """Check function calls."""
        # Check for dangerous built-in functions
        if isinstance(node.func, ast.Name):
            if node.func.id in self.forbidden_functions:
                self.violations.append(f"Forbidden function call: {node.func.id}")
        
        # Check for file operations outside working directory
        elif isinstance(node.func, ast.Attribute):
            if (isinstance(node.func.value, ast.Name) and 
                node.func.value.id == 'open' or node.func.attr == 'open'):
                # Allow open() but we'll validate paths at runtime
                pass
        
        self.generic_visit(node)

    def visit_With(self, node):
        """Check with statements (often used for file operations)."""
        for item in node.items:
            if isinstance(item.context_expr, ast.Call):
                if (isinstance(item.context_expr.func, ast.Name) and 
                    item.context_expr.func.id == 'open'):
                    # Allow open() but validate paths at runtime
                    pass
        
        self.generic_visit(node)

    def visit_Attribute(self, node):
        """Check attribute access."""
        # Check for dangerous attribute access patterns
        if isinstance(node.value, ast.Name):
            if (node.value.id == '__builtins__' or 
                node.attr.startswith('__') and node.attr.endswith('__')):
                self.violations.append(f"Forbidden attribute access: {node.value.id}.{node.attr}")
        
        self.generic_visit(node)


def check_code_security(code: str) -> List[str]:
    """
    Check Python code for security violations using AST parsing.
    
    Args:
        code: Python code to check
        
    Returns:
        List of security violations (empty if safe)
    """
    try:
        tree = ast.parse(code)
        checker = SecurityChecker()
        checker.visit(tree)
        return checker.violations
    except SyntaxError as e:
        error_msg = f"Syntax error: {str(e)}"
        logger.warning(f"Code syntax error: {error_msg}")
        return [error_msg]
    except Exception as e:
        error_msg = f"Security check error: {str(e)}"
        logger.error(f"Unexpected error during security check: {error_msg}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return [error_msg]
