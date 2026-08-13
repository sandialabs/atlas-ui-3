"""Server-side plugin / event-hook system for the chat lifecycle.

Plugins subscribe to lifecycle hook points and return a :class:`HookResult` that
can modify, block, or escalate the behavior at that point. See
``docs/developer/hook-plugin-system.md`` for the full contract.
"""

from .hook_context import (
    HookTurnContext,
    current_hook_context,
    hook_turn,
)
from .hook_loader import (
    HookPluginLoadError,
    load_plugin,
    load_plugins_from_settings,
    parse_plugin_specs,
)
from .hook_registry import (
    DEFAULT_HOOK_TIMEOUT_SECONDS,
    HookChainResult,
    HookRegistration,
    HookRegistry,
    get_hook_registry,
    hook,
    reset_hook_registry,
)
from .hook_types import (
    ALLOWED_MESSAGE_ROLES,
    DEFAULT_DENY_USER_MESSAGE,
    DEFAULT_FAIL_OPEN,
    HookDecision,
    HookEvent,
    HookPoint,
    HookResult,
    PermissionRequestEvent,
    PostToolUseEvent,
    PreLlmCallEvent,
    PreToolUseEvent,
    RagCallEvent,
    RagResponseEvent,
    SessionStartEvent,
    UserPromptSubmitEvent,
)

__all__ = [
    "ALLOWED_MESSAGE_ROLES",
    "DEFAULT_DENY_USER_MESSAGE",
    "DEFAULT_FAIL_OPEN",
    "DEFAULT_HOOK_TIMEOUT_SECONDS",
    "HookChainResult",
    "HookDecision",
    "HookEvent",
    "HookPluginLoadError",
    "HookPoint",
    "HookRegistration",
    "HookRegistry",
    "HookResult",
    "HookTurnContext",
    "PermissionRequestEvent",
    "PostToolUseEvent",
    "PreLlmCallEvent",
    "PreToolUseEvent",
    "RagCallEvent",
    "RagResponseEvent",
    "SessionStartEvent",
    "UserPromptSubmitEvent",
    "current_hook_context",
    "get_hook_registry",
    "hook",
    "hook_turn",
    "load_plugin",
    "load_plugins_from_settings",
    "parse_plugin_specs",
    "reset_hook_registry",
]
