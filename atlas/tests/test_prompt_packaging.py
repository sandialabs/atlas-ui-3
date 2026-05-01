"""Regression tests for prompt-template packaging and resolution.

Issue #572: when atlas-chat is installed via pip, the bundled prompt templates
must be available even though the working directory has no ``prompts/`` folder.
PromptProvider resolves the default ``prompt_base_path`` ("prompts") through
``importlib.resources`` so the templates ride along inside the atlas package.
"""
from __future__ import annotations

from importlib.resources import files

import pytest

from atlas.modules.config import ConfigManager
from atlas.modules.prompts.prompt_provider import PromptProvider

EXPECTED_BUNDLED_TEMPLATES = (
    "agent_observe_prompt.md",
    "agent_reason_prompt.md",
    "agent_summary_prompt.md",
    "agent_system_prompt.md",
    "system_prompt.md",
    "tool_synthesis_prompt.md",
)


def test_bundled_prompt_files_are_packaged():
    """Every prompt template the runtime expects must exist as a package resource."""
    root = files("atlas").joinpath("prompts")
    assert root.is_dir(), "atlas/prompts/ resource directory missing from the installed atlas package"
    missing = [name for name in EXPECTED_BUNDLED_TEMPLATES if not root.joinpath(name).is_file()]
    assert not missing, f"Missing bundled prompt templates: {missing}"


@pytest.mark.asyncio
async def test_prompt_provider_loads_bundled_system_prompt(monkeypatch, tmp_path):
    """With the default config and no on-disk prompts/, system prompt still loads."""
    # Run from a directory that has no prompts/ folder to simulate a pip-installed user.
    monkeypatch.chdir(tmp_path)

    config_manager = ConfigManager()
    # Default value is "prompts" — exercise the bundled-resource branch explicitly.
    config_manager.app_settings.prompt_base_path = "prompts"
    config_manager.app_settings.system_prompt_filename = "system_prompt.md"

    provider = PromptProvider(config_manager)
    rendered = provider.get_system_prompt(user_email="user@example.com")

    assert rendered is not None, "Bundled system prompt failed to load"
    assert "user@example.com" in rendered


@pytest.mark.asyncio
async def test_prompt_provider_filesystem_override_still_works(tmp_path):
    """An explicit absolute path should bypass bundled resources."""
    custom_dir = tmp_path / "custom-prompts"
    custom_dir.mkdir()
    (custom_dir / "system_prompt.md").write_text("override for {user_email}")

    config_manager = ConfigManager()
    config_manager.app_settings.prompt_base_path = str(custom_dir)
    config_manager.app_settings.system_prompt_filename = "system_prompt.md"

    provider = PromptProvider(config_manager)
    rendered = provider.get_system_prompt(user_email="alice@example.com")

    assert rendered == "override for alice@example.com"
