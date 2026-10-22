"""Resolve a model's effective context window (issue #877).

Atlas model names are operator-chosen keys and frequently aliases: a custom
endpoint, a LiteLLM proxy alias, or a gateway model id. The window therefore
comes from, in order:

1. ``context_window`` on the model's config (also settable per gateway model).
2. LiteLLM's bundled model map, looked up by the configured model id with and
   without its provider prefix. The map is read directly rather than through
   ``litellm.get_model_info``, which prints to stderr on a miss; nothing here
   touches the network.
3. ``CONTEXT_FALLBACK_WINDOW_TOKENS``, reported as ``"fallback"`` so the UI can
   say the limit is assumed rather than known.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from atlas.modules.config.models import lookup_model_config

logger = logging.getLogger(__name__)

DEFAULT_FALLBACK_WINDOW = 128000
# Never reserve more than this share of the window for the reply: a model
# configured with max_tokens close to its window would otherwise leave no room
# for any input at all.
MAX_OUTPUT_RESERVE_SHARE = 0.25
DEFAULT_OUTPUT_RESERVE = 4096
# Headroom for estimation error and provider-side framing tokens.
SAFETY_MARGIN_SHARE = 0.05

SOURCE_CONFIG = "config"
SOURCE_LITELLM = "litellm"
SOURCE_FALLBACK = "fallback"


@dataclass(frozen=True)
class ContextWindow:
    """A model's window and the share of it a request may fill."""

    total: int
    source: str
    output_reserve: int

    @property
    def safety_margin(self) -> int:
        return int(self.total * SAFETY_MARGIN_SHARE)

    @property
    def usable(self) -> int:
        """Input tokens a request may use after reply and safety headroom."""
        return max(1, self.total - self.output_reserve - self.safety_margin)

    @property
    def known(self) -> bool:
        return self.source != SOURCE_FALLBACK


def _candidate_ids(model_id: str) -> Iterable[str]:
    yield model_id
    if "/" in model_id:
        # "openai/gpt-4o" -> "gpt-4o"; a proxy path like "azure/team/gpt-4o"
        # is tried by its last segment too.
        yield model_id.split("/", 1)[1]
        yield model_id.rsplit("/", 1)[1]


def _litellm_window(model_id: str) -> Optional[int]:
    try:
        import litellm
        model_map = getattr(litellm, "model_cost", None) or {}
    except Exception:  # pragma: no cover - litellm is a hard dependency
        return None
    for candidate in _candidate_ids(model_id):
        info = model_map.get(candidate)
        if not isinstance(info, dict):
            continue
        window = info.get("max_input_tokens") or info.get("max_tokens")
        if isinstance(window, int) and window > 0:
            return window
    return None


def _fallback_window(app_settings: Any) -> int:
    value = getattr(app_settings, "context_fallback_window_tokens", None)
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_FALLBACK_WINDOW
    return value if value > 0 else DEFAULT_FALLBACK_WINDOW


def _output_reserve(model_config: Any, total: int) -> int:
    configured = getattr(model_config, "max_tokens", None) if model_config is not None else None
    reserve = configured if isinstance(configured, int) and configured > 0 else DEFAULT_OUTPUT_RESERVE
    return min(reserve, int(total * MAX_OUTPUT_RESERVE_SHARE))


def resolve_context_window(llm_config: Any, model_name: str, app_settings: Any = None) -> ContextWindow:
    """Return the effective window for the Atlas model key ``model_name``.

    Never raises: an unknown or misconfigured model degrades to the fallback
    window, because a wrong guess here costs an early compaction at worst,
    while an exception would cost the user their turn.
    """
    model_config = None
    try:
        model_config = lookup_model_config(llm_config, model_name) if llm_config is not None else None
    except Exception:
        logger.debug("Context window lookup failed for a model", exc_info=True)

    configured = getattr(model_config, "context_window", None) if model_config is not None else None
    if isinstance(configured, int) and configured > 0:
        return ContextWindow(configured, SOURCE_CONFIG, _output_reserve(model_config, configured))

    model_id = getattr(model_config, "model_name", None) if model_config is not None else None
    for candidate in (model_id, model_name):
        if isinstance(candidate, str) and candidate:
            window = _litellm_window(candidate)
            if window:
                return ContextWindow(window, SOURCE_LITELLM, _output_reserve(model_config, window))

    total = _fallback_window(app_settings)
    return ContextWindow(total, SOURCE_FALLBACK, _output_reserve(model_config, total))
