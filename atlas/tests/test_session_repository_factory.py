"""Tests for the session repository factory (issue #760).

The factory is the extension point that lets a deployment plug in a
distributed session store (Redis, shared DB, etc.) through
``SESSION_REPOSITORY_TYPE``, removing the undocumented sticky-session
requirement the in-memory default imposes on multi-replica deployments.
"""

import pytest

from atlas.infrastructure.sessions.factory import create_session_repository
from atlas.infrastructure.sessions.in_memory_repository import (
    InMemorySessionRepository,
)


def test_default_returns_in_memory_repository():
    repo = create_session_repository()
    assert isinstance(repo, InMemorySessionRepository)


def test_memory_type_returns_in_memory_repository():
    repo = create_session_repository("memory")
    assert isinstance(repo, InMemorySessionRepository)


def test_unknown_type_raises_value_error():
    with pytest.raises(ValueError, match="Unknown SESSION_REPOSITORY_TYPE"):
        create_session_repository("redis")


def test_unknown_type_error_mentions_extension_point():
    """The error must tell the operator where to register a new implementation."""
    with pytest.raises(ValueError, match="create_session_repository"):
        create_session_repository("nope")


def test_app_settings_has_session_repository_type():
    from atlas.modules.config.settings import AppSettings

    settings = AppSettings()
    assert settings.session_repository_type == "memory"
