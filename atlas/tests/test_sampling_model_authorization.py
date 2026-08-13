"""MCP sampling must apply the same per-model group ACL that chat applies.

The sampling routing context carried no user identity, so the handler picked
whichever model the MCP server asked for -- or simply the first configured one
-- and invoked it. An MCP server available to a user could therefore consume a
model whose groups deny that user, because the listing-layer filtering that
governs the chat UI never touches this path.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from atlas.core.model_access import ModelAccessDecision
from atlas.modules.mcp_tools.mcp_routing import RoutingMixin

MODELS = {"open-model": SimpleNamespace(), "restricted-model": SimpleNamespace()}


class _Routing(RoutingMixin):
    pass


def _mixin():
    return _Routing()


def _access(allowed):
    """check_model_access stand-in permitting only `allowed` model names."""
    async def _check(models, name, user_email, context=None):
        if name in allowed:
            return ModelAccessDecision.ALLOWED
        return ModelAccessDecision.DENIED
    return _check


@pytest.mark.asyncio
async def test_preferred_model_is_used_when_authorized():
    with patch("atlas.core.model_access.check_model_access", _access({"restricted-model"})):
        chosen = await _mixin()._authorized_sampling_model(
            MODELS, preferred="restricted-model",
            user_email="alice@example.com", server_name="demo",
        )
    assert chosen == "restricted-model"


@pytest.mark.asyncio
async def test_unauthorized_preference_falls_back_to_an_authorized_model():
    """The bypass: the server's preference must not override the ACL."""
    with patch("atlas.core.model_access.check_model_access", _access({"open-model"})):
        chosen = await _mixin()._authorized_sampling_model(
            MODELS, preferred="restricted-model",
            user_email="bob@example.com", server_name="demo",
        )
    assert chosen == "open-model"


@pytest.mark.asyncio
async def test_default_pick_is_also_filtered():
    """With no preference the first *authorized* model is used, not the first."""
    with patch("atlas.core.model_access.check_model_access", _access({"restricted-model"})):
        chosen = await _mixin()._authorized_sampling_model(
            MODELS, preferred=None,
            user_email="bob@example.com", server_name="demo",
        )
    assert chosen == "restricted-model"


@pytest.mark.asyncio
async def test_denied_when_no_model_is_authorized():
    """Deny rather than silently downgrade to something unauthorized."""
    with patch("atlas.core.model_access.check_model_access", _access(set())):
        with pytest.raises(PermissionError, match="not authorized"):
            await _mixin()._authorized_sampling_model(
                MODELS, preferred="open-model",
                user_email="mallory@example.com", server_name="demo",
            )


@pytest.mark.asyncio
async def test_policy_errors_fail_closed():
    """An exception resolving policy must not grant access."""
    async def _boom(*args, **kwargs):
        raise RuntimeError("policy backend down")

    with patch("atlas.core.model_access.check_model_access", _boom):
        with pytest.raises(PermissionError):
            await _mixin()._authorized_sampling_model(
                MODELS, preferred="open-model",
                user_email="alice@example.com", server_name="demo",
            )


# --- token ceiling --------------------------------------------------------

@pytest.mark.parametrize(
    "requested,expected",
    [
        (100, 100),      # under the ceiling
        (4096, 4096),    # at it
        (999_999, 4096), # over it: the cost lever
        (0, 4096),
        (-5, 4096),
        (None, 4096),
        ("9999", 4096),
        (True, 4096),
    ],
)
def test_sampling_tokens_are_clamped(requested, expected):
    assert _mixin()._clamp_sampling_tokens(requested) == expected


def test_sampling_routing_context_carries_user_identity():
    """Without this the handler has no identity to check against."""
    from atlas.modules.mcp_tools.mcp_routing import _SamplingRoutingContext

    context = _SamplingRoutingContext(
        "demo", SimpleNamespace(id="tc-1", name="t"), None, "alice@example.com"
    )
    assert context.user_email == "alice@example.com"
