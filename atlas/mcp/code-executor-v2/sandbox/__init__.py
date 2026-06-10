"""Sandbox primitives for code-executor-v2.

The actual subprocess wrapper (``_sandbox_launch_v2.py``) is a standalone
stdlib-only script; it is not imported as a package member because it is
exec'd by absolute path from the user-controlled cwd.
"""
