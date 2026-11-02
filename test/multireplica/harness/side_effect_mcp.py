"""Stdio MCP server with one tool whose executions the harness can count.

``side_effect(key, seconds)`` appends one line to ``<HARNESS_SIDE_EFFECT_DIR>/<key>.log``
recording which replica ran it, then waits ``seconds``. Every replica runs its
own copy of this server, but they all share the directory, so a test can prove
how many times a tool really executed across the cluster -- for example that a
run interrupted by a replica crash is never replayed.
"""

import json
import os
import time

from fastmcp import FastMCP
from side_effect_keys import log_name  # sibling module; this file runs as a script

mcp = FastMCP("harness")


@mcp.tool
def side_effect(key: str, seconds: float = 0.0) -> str:
    """Record one execution under ``key``, then wait ``seconds``.

    Args:
        key: Name of the counter to record the execution under.
        seconds: How long to wait after recording, to keep the run busy.
    """
    directory = os.environ["HARNESS_SIDE_EFFECT_DIR"]
    path = os.path.join(directory, log_name(key))
    line = json.dumps({"replica": os.environ.get("HARNESS_REPLICA", "?"), "pid": os.getpid(), "t": time.time()})
    # O_APPEND keeps concurrent replicas from overwriting each other. A short
    # write is retried until the whole record is out; the reader ignores a
    # line that has no newline yet, so a torn record is never miscounted.
    data = (line + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        while data:
            data = data[os.write(fd, data):]
    finally:
        os.close(fd)
    time.sleep(max(0.0, min(float(seconds), 60.0)))
    return f"recorded {key}"


if __name__ == "__main__":
    mcp.run()
