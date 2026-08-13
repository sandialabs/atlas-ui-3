"""Loading operator-installed hook plugins into the registry.

Plugins are trusted, in-process, server-side Python. Discovery is an explicit
config allow-list rather than automatic scanning, so nothing runs that an
operator did not name:

    FEATURE_HOOKS_ENABLED=true
    HOOK_PLUGINS=my_org.atlas_plugins.audit,my_org.atlas_plugins.pii:register

Each entry is ``module`` (whose ``register`` attribute is used) or
``module:attribute``. The target must be a callable taking the registry.

Loading is **fail-fast**: a plugin that cannot be imported or that raises during
registration aborts startup. A governance control that silently fails to load is
indistinguishable from a control that was never configured, which is exactly the
failure mode this system exists to prevent.
"""

from __future__ import annotations

import importlib
import logging
import re
from typing import Any, List, Optional

from atlas.core.log_sanitizer import sanitize_for_logging

from .hook_registry import HookRegistry, get_hook_registry

logger = logging.getLogger(__name__)

DEFAULT_REGISTER_ATTR = "register"


class HookPluginLoadError(RuntimeError):
    """Raised when a configured hook plugin cannot be loaded or registered."""


def parse_plugin_specs(raw: Optional[str]) -> List[str]:
    """Split a comma/whitespace separated plugin list into normalized specs.

    Duplicates are collapsed, preserving first-seen order. A spec never contains
    whitespace (it is an import path, optionally ``:attr``), so splitting on any
    run of commas and whitespace accepts the multi-line and space-separated
    forms an operator is likely to write in a ``.env`` or compose file.
    """
    if not raw:
        return []
    specs: List[str] = []
    for chunk in re.split(r"[,\s]+", raw):
        spec = chunk.strip()
        if spec and spec not in specs:
            specs.append(spec)
    return specs


def normalize_plugin_spec(spec: str) -> str:
    """Return the canonical ``module:attr`` form of *spec*.

    ``pkg.plugin`` and ``pkg.plugin:register`` name the same entry point, so
    they must collapse to one key for the already-loaded check.
    """
    module_path, _, attr = spec.partition(":")
    module_path = module_path.strip()
    attr = attr.strip() or DEFAULT_REGISTER_ATTR
    if not module_path:
        raise HookPluginLoadError(f"Invalid hook plugin spec: {spec!r}")
    return f"{module_path}:{attr}"


def load_plugin(spec: str, registry: HookRegistry) -> Any:
    """Import a single plugin spec and call its register function."""
    module_path, _, attr = normalize_plugin_spec(spec).partition(":")

    try:
        module = importlib.import_module(module_path)
    except Exception as exc:
        raise HookPluginLoadError(f"Could not import hook plugin {module_path!r}: {exc}") from exc

    register_fn = getattr(module, attr, None)
    if register_fn is None:
        raise HookPluginLoadError(
            f"Hook plugin {module_path!r} has no attribute {attr!r}"
        )
    if not callable(register_fn):
        raise HookPluginLoadError(
            f"Hook plugin entry point {module_path}:{attr} is not callable"
        )

    try:
        register_fn(registry)
    except Exception as exc:
        raise HookPluginLoadError(
            f"Hook plugin {module_path}:{attr} failed during registration: {exc}"
        ) from exc

    logger.info("Loaded hook plugin %s", sanitize_for_logging(f"{module_path}:{attr}"))
    return register_fn


def load_plugins_from_settings(app_settings: Any, registry: Optional[HookRegistry] = None) -> int:
    """Load every plugin named by ``AppSettings``. Returns how many newly loaded.

    Idempotent: a spec already registered against *registry* is skipped, so
    repeated calls (see ``AppFactory``) do not double-register handlers.

    Does nothing when ``FEATURE_HOOKS_ENABLED`` is false, so the hook bus is
    inert (and free) in deployments that do not use it.
    """
    registry = registry or get_hook_registry()

    if not getattr(app_settings, "feature_hooks_enabled", False):
        if parse_plugin_specs(getattr(app_settings, "hook_plugins", "")):
            logger.warning(
                "HOOK_PLUGINS is set but FEATURE_HOOKS_ENABLED is false; no plugins loaded"
            )
        return 0

    timeout = getattr(app_settings, "hook_timeout_seconds", None)
    if timeout:
        registry.default_timeout_seconds = float(timeout)

    specs = parse_plugin_specs(getattr(app_settings, "hook_plugins", ""))
    if not specs:
        logger.info("Hook system enabled with no plugins configured")
        return 0

    loaded = 0
    for spec in specs:
        canonical = normalize_plugin_spec(spec)
        # AppFactory is constructed more than once in a live process (the module
        # -level instance plus AtlasClient.initialize()), and the registry is a
        # process-wide singleton. Without this, every handler would register --
        # and therefore run -- once per construction.
        if registry.is_plugin_loaded(canonical):
            logger.debug("Hook plugin %s already loaded; skipping", sanitize_for_logging(canonical))
            continue
        load_plugin(canonical, registry)
        registry.mark_plugin_loaded(canonical)
        loaded += 1

    logger.info("Hook system enabled: %d plugin(s), hooks=%s", loaded, registry.describe())
    return loaded
