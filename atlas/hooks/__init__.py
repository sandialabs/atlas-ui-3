"""Config-driven hook system (GH #713).

Operator-installed executables (bash, Python, anything) registered in
``config/hooks.json`` run as subprocesses at chat lifecycle chokepoints. The
event payload arrives as JSON on stdin; the hook's JSON on stdout (plus exit
code) can allow, modify, block, or escalate the operation. See ``docs/hooks.md``.

Public API:
    HookEvent          -- lifecycle event enum (also the config key / wire name)
    HookOutcome        -- aggregated result of running all hooks for an event
    HookManager        -- loads config and dispatches events (rarely used directly)
    get_hook_manager() -- process-wide singleton accessor (call sites use this)
    HookBlockedError   -- raised when a hook denies an operation with no graceful
                           in-band return value (e.g. PreLlmCall)
"""

from .manager import (
    HookBlockedError,
    HookManager,
    HookOutcome,
    get_hook_manager,
    set_hook_manager_for_testing,
)
from .models import HookConfig, HookDecision, HookEvent, HooksConfig, default_on_error

__all__ = [
    "HookBlockedError",
    "HookConfig",
    "HookDecision",
    "HooksConfig",
    "HookEvent",
    "HookManager",
    "HookOutcome",
    "default_on_error",
    "get_hook_manager",
    "set_hook_manager_for_testing",
]
