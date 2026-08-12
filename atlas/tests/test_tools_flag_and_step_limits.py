"""Server-side enforcement of the tools feature flag and agent step ceiling.

FEATURE_TOOLS_ENABLED previously only filtered what /api/config advertised, so
a crafted client could still submit selected_tools, selected_prompts or
agent_mode over the WebSocket and have them honoured -- the flag looked like a
security boundary but was purely cosmetic.

agent_max_steps had the same shape: it arrived off the WebSocket frame with no
server-side ceiling, and each step is a metered model call plus its tool calls.

Both are enforced in the orchestrator rather than the WebSocket handler, so
programmatic callers go through the same rule.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from atlas.application.chat.orchestrator import ChatOrchestrator


def _orchestrator(*, tools_enabled=True, agent_max_steps=10):
    orchestrator = ChatOrchestrator.__new__(ChatOrchestrator)
    orchestrator.config_manager = SimpleNamespace(
        app_settings=SimpleNamespace(
            feature_tools_enabled=tools_enabled,
            agent_max_steps=agent_max_steps,
        )
    )
    return orchestrator


# --- tools flag -----------------------------------------------------------

def test_tools_enabled_is_read_from_settings():
    assert _orchestrator(tools_enabled=True)._tools_are_enabled() is True
    assert _orchestrator(tools_enabled=False)._tools_are_enabled() is False


def test_missing_config_manager_does_not_disable_tools():
    """A caller constructed without config must not silently lose tools."""
    orchestrator = ChatOrchestrator.__new__(ChatOrchestrator)
    orchestrator.config_manager = None
    assert orchestrator._tools_are_enabled() is True


# --- agent step ceiling ---------------------------------------------------

@pytest.mark.parametrize(
    "requested,expected",
    [
        (5, 5),            # under the ceiling, honoured
        (10, 10),          # exactly the ceiling
        (50, 10),          # over the ceiling, clamped
        (10_000_000, 10),  # the cost-multiplication case
        (0, 10),           # nonsense, falls back to configured
        (-3, 10),
        (None, 10),
        ("100", 10),       # a string off a crafted frame
        (True, 10),        # bool is an int subclass; must not become 1 step
        (3.9, 10),
    ],
)
def test_agent_steps_are_clamped(requested, expected):
    assert _orchestrator()._clamp_agent_steps(requested) == expected


def test_ceiling_follows_operator_configuration():
    assert _orchestrator(agent_max_steps=3)._clamp_agent_steps(100) == 3
    assert _orchestrator(agent_max_steps=3)._clamp_agent_steps(2) == 2


def test_ceiling_defaults_when_unconfigured():
    orchestrator = ChatOrchestrator.__new__(ChatOrchestrator)
    orchestrator.config_manager = None
    assert orchestrator._clamp_agent_steps(999) == 10
