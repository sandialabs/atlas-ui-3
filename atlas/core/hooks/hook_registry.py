"""The hook bus: registration, ordered dispatch, and composition rules.

``HookRegistry`` is the inbound mirror of ``EventPublisher``. Where the publisher
fans events *out* to the UI with no return influence, the registry runs a
blocking interceptor chain whose return values can modify, block, or escalate
the behavior at the call site.

Composition rules (deterministic, most-restrictive-wins):

1. Handlers run sequentially in ``(priority, registration order)``.
2. ``MODIFY`` patches are piped: each handler sees the previous handler's edits.
3. The first ``DENY`` short-circuits the chain -- nothing after it runs.
4. ``REQUIRE_APPROVAL`` is sticky but does not short-circuit; a later ``DENY``
   still wins.
5. A handler that raises or exceeds its timeout is either skipped (fail-open) or
   converted into a ``DENY`` (fail-closed), per :data:`DEFAULT_FAIL_OPEN` or the
   registration's ``fail_open`` override.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from atlas.core.log_sanitizer import sanitize_for_logging

from .hook_types import (
    DEFAULT_DENY_USER_MESSAGE,
    DEFAULT_FAIL_OPEN,
    HookDecision,
    HookEvent,
    HookPoint,
    HookResult,
)

logger = logging.getLogger(__name__)

DEFAULT_HOOK_TIMEOUT_SECONDS = 5.0

#: A handler takes the event and returns a HookResult (or None for CONTINUE).
HookHandler = Callable[[HookEvent], Any]


@dataclass(frozen=True)
class HookRegistration:
    """One plugin handler bound to one hook point."""

    hook_point: HookPoint
    handler: HookHandler
    name: str
    priority: int = 100
    fail_open: bool = False
    timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS
    #: Monotonic counter making the sort stable within a priority band.
    sequence: int = 0


@dataclass
class HookChainResult:
    """The composed outcome of running every handler for one hook point."""

    decision: HookDecision = HookDecision.CONTINUE
    reason: Optional[str] = None
    user_message: Optional[str] = None
    #: Handler names that returned something other than CONTINUE, for auditing.
    contributors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    #: True once any accepted patch has been applied to the event. Tracked
    #: independently of ``decision`` because a chain that both patches and
    #: escalates composes to REQUIRE_APPROVAL -- call sites still have to read
    #: the patched event, so this must not be inferred from the decision.
    modified: bool = False
    #: The specific mutable-field names that were changed by an accepted
    #: MODIFY patch. Call sites with more than one mutable field consult this
    #: instead of ``modified`` so that an in-place mutation on one field (which
    #: never went through ``validate_patch``) is not swept in by an accepted
    #: patch on a different field.
    patched_fields: Set[str] = field(default_factory=set)

    @property
    def denied(self) -> bool:
        return self.decision == HookDecision.DENY

    @property
    def approval_required(self) -> bool:
        return self.decision == HookDecision.REQUIRE_APPROVAL


class HookRegistry:
    """Holds registrations and dispatches events to them."""

    def __init__(self, default_timeout_seconds: float = DEFAULT_HOOK_TIMEOUT_SECONDS) -> None:
        self._registrations: Dict[HookPoint, List[HookRegistration]] = {
            point: [] for point in HookPoint
        }
        self._default_timeout = default_timeout_seconds
        self._sequence = 0
        self._loaded_plugins: set[str] = set()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        hook_point: HookPoint,
        handler: HookHandler,
        name: Optional[str] = None,
        priority: int = 100,
        fail_open: Optional[bool] = None,
        timeout_seconds: Optional[float] = None,
    ) -> HookRegistration:
        """Subscribe *handler* to *hook_point*.

        Args:
            hook_point: Where the handler fires.
            handler: Async (preferred) or sync callable taking the event.
            name: Identifier used in logs and audit records. Defaults to the
                handler's qualified name.
            priority: Lower runs first. Handlers sharing a priority run in
                registration order.
            fail_open: Override the hook point's default failure mode. ``True``
                skips the handler on error; ``False`` converts an error to DENY.
            timeout_seconds: Per-handler timeout, enforced for sync and async
                handlers alike (a sync handler runs on a worker thread). Note
                that a timed-out sync handler's thread cannot be killed; it is
                abandoned, so the chain proceeds but the thread runs to
                completion. Async handlers remain the documented norm.
        """
        if not callable(handler):
            raise TypeError(f"Hook handler for {hook_point.value} is not callable: {handler!r}")

        self._sequence += 1
        registration = HookRegistration(
            hook_point=hook_point,
            handler=handler,
            name=name or getattr(handler, "__qualname__", repr(handler)),
            priority=priority,
            fail_open=DEFAULT_FAIL_OPEN[hook_point] if fail_open is None else fail_open,
            timeout_seconds=self._default_timeout if timeout_seconds is None else timeout_seconds,
            sequence=self._sequence,
        )
        bucket = self._registrations[hook_point]
        bucket.append(registration)
        bucket.sort(key=lambda r: (r.priority, r.sequence))
        logger.info(
            "Registered hook %s -> %s (priority=%d, fail_open=%s)",
            hook_point.value,
            sanitize_for_logging(registration.name),
            registration.priority,
            registration.fail_open,
        )
        return registration

    @property
    def default_timeout_seconds(self) -> float:
        """Timeout applied to registrations that do not specify their own."""
        return self._default_timeout

    @default_timeout_seconds.setter
    def default_timeout_seconds(self, value: float) -> None:
        if value <= 0:
            raise ValueError("Hook timeout must be positive")
        self._default_timeout = value

    def unregister(self, registration: HookRegistration) -> bool:
        """Remove a previously returned registration. Returns True if present."""
        bucket = self._registrations[registration.hook_point]
        if registration in bucket:
            bucket.remove(registration)
            return True
        return False

    def clear(self) -> None:
        """Drop every registration (used by tests and by config reloads)."""
        for bucket in self._registrations.values():
            bucket.clear()
        self._loaded_plugins.clear()

    def is_plugin_loaded(self, spec: str) -> bool:
        """True when *spec* has already registered against this registry.

        The registry outlives the objects that load into it -- ``AppFactory`` is
        constructed more than once in a live process -- so the loader consults
        this to keep a second pass from double-registering every handler.
        """
        return spec in self._loaded_plugins

    def mark_plugin_loaded(self, spec: str) -> None:
        """Record that *spec* has registered its handlers."""
        self._loaded_plugins.add(spec)

    def has_hooks(self, hook_point: HookPoint) -> bool:
        return bool(self._registrations[hook_point])

    def registrations(self, hook_point: HookPoint) -> List[HookRegistration]:
        return list(self._registrations[hook_point])

    def describe(self) -> Dict[str, List[str]]:
        """Return ``{hook_point: [handler names in run order]}`` for diagnostics."""
        return {
            point.value: [r.name for r in bucket]
            for point, bucket in self._registrations.items()
            if bucket
        }

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, event: HookEvent) -> HookChainResult:
        """Run every handler registered for ``event``'s hook point.

        The event is mutated in place by accepted ``MODIFY`` patches, so callers
        read the post-hook values straight off the event object.
        """
        hook_point = event.HOOK_POINT
        # Snapshot: register() sorts the bucket in place, so a handler that
        # registers another handler mid-chain would otherwise reorder the list
        # we are iterating and cause skipped or repeated invocations.
        registrations = list(self._registrations[hook_point])
        chain = HookChainResult()
        if not registrations:
            return chain

        approval_required = False

        for registration in registrations:
            result = await self._invoke(registration, event)

            if result is None or result.decision == HookDecision.CONTINUE:
                if result is not None and result.metadata:
                    chain.metadata[registration.name] = result.metadata
                continue

            chain.contributors.append(registration.name)
            if result.metadata:
                chain.metadata[registration.name] = result.metadata

            if result.decision == HookDecision.DENY:
                chain.decision = HookDecision.DENY
                chain.reason = result.reason or f"blocked by hook {registration.name}"
                # ``reason`` is operator-facing and routinely names internal
                # rules or quotes the offending input. ``HookResult`` is public,
                # so a plugin can build a DENY directly without going through
                # ``HookResult.deny()``; falling back to the generic message
                # keeps that path from leaking the reason into the chat.
                chain.user_message = result.user_message or DEFAULT_DENY_USER_MESSAGE
                logger.warning(
                    "Hook %s denied %s: %s",
                    sanitize_for_logging(registration.name),
                    hook_point.value,
                    sanitize_for_logging(chain.reason),
                )
                return chain

            if result.decision == HookDecision.REQUIRE_APPROVAL:
                approval_required = True
                if not chain.reason:
                    chain.reason = result.reason or f"approval forced by hook {registration.name}"
                continue

            if result.decision == HookDecision.MODIFY:
                patch_fields = self._apply_modify(registration, event, result)
                if patch_fields is not None and not isinstance(patch_fields, str):
                    # Accepted patch: record which fields were touched so call
                    # sites with multiple mutable fields only consume the ones
                    # that were validated, not ones a handler mutated in place
                    # behind a CONTINUE.
                    chain.patched_fields.update(patch_fields)
                    chain.modified = True
                elif isinstance(patch_fields, str):
                    # Rejected patch: treat exactly like a handler failure. The
                    # handler contributed nothing, so its audit trail comes off
                    # too -- otherwise metadata records a handler that was
                    # effectively skipped.
                    if registration.fail_open:
                        chain.contributors.pop()
                        chain.metadata.pop(registration.name, None)
                        continue
                    chain.decision = HookDecision.DENY
                    chain.reason = f"hook {registration.name} returned an invalid patch: {patch_fields}"
                    chain.user_message = DEFAULT_DENY_USER_MESSAGE
                    return chain

        if approval_required:
            chain.decision = HookDecision.REQUIRE_APPROVAL
        elif chain.modified:
            chain.decision = HookDecision.MODIFY
        return chain

    async def _invoke(
        self, registration: HookRegistration, event: HookEvent
    ) -> Optional[HookResult]:
        """Run one handler with timeout and error isolation.

        Returns the handler's result, ``None`` when it fails open, or a synthetic
        ``DENY`` when it fails closed.
        """
        try:
            if inspect.iscoroutinefunction(registration.handler):
                outcome = await asyncio.wait_for(
                    registration.handler(event), timeout=registration.timeout_seconds
                )
            else:
                # A sync handler called inline would run unbounded on the event
                # loop: no timeout can interrupt it and every other turn on the
                # process stalls behind it. Run it on a worker thread so the
                # same timeout applies to sync and async plugins alike.
                outcome = await asyncio.wait_for(
                    asyncio.to_thread(registration.handler, event),
                    timeout=registration.timeout_seconds,
                )
                if inspect.isawaitable(outcome):
                    # A non-coroutine callable that returns an awaitable (a
                    # partial over an async function, an object with an async
                    # __call__). Await it under the same budget.
                    outcome = await asyncio.wait_for(
                        outcome, timeout=registration.timeout_seconds
                    )
        except asyncio.TimeoutError:
            return self._on_failure(
                registration,
                f"timed out after {registration.timeout_seconds}s",
            )
        except asyncio.CancelledError:
            # Turn cancellation (client disconnect, shutdown) is not a plugin
            # fault -- propagate so the surrounding task unwinds normally.
            raise
        except Exception as exc:  # noqa: BLE001 - plugins are third-party code
            logger.error(
                "Hook %s (%s) raised: %s",
                sanitize_for_logging(registration.name),
                registration.hook_point.value,
                exc,
                exc_info=True,
            )
            return self._on_failure(registration, f"raised {type(exc).__name__}")

        if outcome is None:
            return None
        if not isinstance(outcome, HookResult):
            return self._on_failure(
                registration,
                f"returned {type(outcome).__name__}, expected HookResult or None",
            )
        return outcome

    def _on_failure(self, registration: HookRegistration, detail: str) -> Optional[HookResult]:
        if registration.fail_open:
            logger.warning(
                "Hook %s (%s) %s; failing open and continuing",
                sanitize_for_logging(registration.name),
                registration.hook_point.value,
                detail,
            )
            return None
        logger.error(
            "Hook %s (%s) %s; failing closed and denying",
            sanitize_for_logging(registration.name),
            registration.hook_point.value,
            detail,
        )
        return HookResult.deny(
            reason=f"hook {registration.name} {detail}",
            user_message="This request was blocked because a policy plugin could not complete.",
        )

    @staticmethod
    def _apply_modify(
        registration: HookRegistration, event: HookEvent, result: HookResult
    ) -> Optional[str | Set[str]]:
        """Validate and apply a MODIFY patch.

        Returns the set of patched field names on success, or an error string
        on rejection.
        """
        patch = result.payload
        if not isinstance(patch, dict) or not patch:
            return "MODIFY requires a non-empty dict payload"

        allowed = set(event.MUTABLE_FIELDS)
        illegal = sorted(set(patch) - allowed)
        if illegal:
            return f"fields {illegal} are not mutable (allowed: {sorted(allowed)})"

        error = event.validate_patch(patch)
        if error:
            return error

        event.apply_patch(patch)
        logger.info(
            "Hook %s modified %s fields %s",
            sanitize_for_logging(registration.name),
            event.HOOK_POINT.value,
            sorted(patch),
        )
        return set(patch)


_registry: Optional[HookRegistry] = None


def get_hook_registry() -> HookRegistry:
    """Return the process-wide hook registry, creating it on first use.

    A module-level singleton matches the existing ``get_approval_manager()``
    pattern and is what lets the stateless helpers in ``tool_executor`` reach the
    bus without threading a new dependency through every call site.
    """
    global _registry
    if _registry is None:
        _registry = HookRegistry()
    return _registry


def reset_hook_registry() -> None:
    """Drop the singleton. Intended for tests and full config reloads."""
    global _registry
    _registry = None


def hook(
    hook_point: HookPoint,
    name: Optional[str] = None,
    priority: int = 100,
    fail_open: Optional[bool] = None,
    timeout_seconds: Optional[float] = None,
) -> Callable[[HookHandler], HookHandler]:
    """Decorator registering a function on the global registry.

    ``@hook(HookPoint.PRE_TOOL_USE)`` above an ``async def`` is the shorthand
    plugins use inside their ``register()`` module.
    """

    def decorator(handler: HookHandler) -> HookHandler:
        get_hook_registry().register(
            hook_point,
            handler,
            name=name,
            priority=priority,
            fail_open=fail_open,
            timeout_seconds=timeout_seconds,
        )
        return handler

    return decorator
