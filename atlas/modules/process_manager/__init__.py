"""Process manager for the Agent Portal.

Launch, track, stream output from, and cancel host subprocesses.

This is an early/dev version of the Agent Portal. It currently has minimal
governance — any authenticated user can launch any command the backend
process itself can run. Governance (allow-lists, role checks, quotas,
audit logging) will be layered on top later.
"""

from atlas.modules.process_manager.landlock import (
    LandlockUnavailableError,
    restrict_to_workdir,
)
from atlas.modules.process_manager.landlock import (
    is_supported as landlock_is_supported,
)
from atlas.modules.process_manager.manager import (
    ManagedProcess,
    OutputChunk,
    ProcessManager,
    ProcessNotFoundError,
    ProcessStatus,
    get_process_manager,
)

__all__ = [
    "LandlockUnavailableError",
    "ManagedProcess",
    "OutputChunk",
    "ProcessManager",
    "ProcessNotFoundError",
    "ProcessStatus",
    "get_process_manager",
    "landlock_is_supported",
    "restrict_to_workdir",
]
