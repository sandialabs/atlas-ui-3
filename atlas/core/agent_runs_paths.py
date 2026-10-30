"""URL prefix of the service-facing agent-runs API.

Lives in core so the middleware can recognize these paths without importing
the routes layer (and, through it, the whole run-launch stack).
"""

AGENT_RUNS_PREFIX = "/api/agent-runs"


def is_agent_runs_path(path: str) -> bool:
    return path == AGENT_RUNS_PREFIX or path.startswith(AGENT_RUNS_PREFIX + "/")
