"""Entry point used to apply Landlock in the child, then exec the target.

Invoked as::

    python -m atlas.modules.process_manager._sandbox_launch WORKDIR CMD [ARGS...]

We route sandboxed launches through this wrapper instead of using
``preexec_fn`` because uvloop (which backs the FastAPI app) has known
interactions with ``preexec_fn`` that can surface as ``PermissionError``
on process creation. The wrapper keeps the path platform-agnostic: the
restriction is installed after uv-spawn's exec replaces the process
image with Python, and before the second ``execvp`` hands control to
the user's actual command.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    if len(sys.argv) < 3:
        sys.stderr.write(
            "usage: _sandbox_launch WORKDIR CMD [ARGS...]\n"
        )
        return 2

    workdir = sys.argv[1]
    argv = sys.argv[2:]

    # Late import so failures surface to the caller as regular stderr,
    # not at package import time on kernels without Landlock.
    from atlas.modules.process_manager.landlock import restrict_to_workdir

    try:
        restrict_to_workdir(workdir)
    except Exception as e:
        sys.stderr.write(f"sandbox setup failed: {e}\n")
        return 1

    try:
        os.execvp(argv[0], argv)
    except FileNotFoundError as e:
        sys.stderr.write(f"command not found: {e}\n")
        return 127
    except PermissionError as e:
        sys.stderr.write(f"permission denied: {e}\n")
        return 126
    # execvp does not return on success
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
