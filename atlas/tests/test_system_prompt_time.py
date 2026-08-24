"""Tests for system prompt time injection (issue #823).

Covers the ``enrich_system_prompt_with_time`` helper and the ``MessageBuilder``
integration that appends the current date/time (and, after a long gap, an
elapsed-time note) to the system prompt.
"""
from datetime import datetime, timedelta, timezone

import pytest

from atlas.application.chat.preprocessors.message_builder import MessageBuilder
from atlas.application.chat.preprocessors.system_prompt_time import (
    enrich_system_prompt_with_time,
)
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.modules.config import ConfigManager
from atlas.modules.prompts.prompt_provider import PromptProvider


def _session_with_user_messages(timestamps):
    """Build a session whose history has user messages at the given times."""
    session = Session(user_email="test@example.com")
    for ts in timestamps:
        session.history.add_message(
            Message(role=MessageRole.USER, content="hi", timestamp=ts)
        )
    return session


def test_injects_current_datetime():
    now = datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc)
    session = _session_with_user_messages([now])  # single message: no elapsed note
    result = enrich_system_prompt_with_time(
        "Base prompt.", session, "UTC", 5, now_utc=now
    )
    assert "Base prompt." in result
    assert "Current Date & Time" in result
    assert "2026-08-21 14:30" in result


def test_timezone_is_respected():
    """A non-UTC timezone shifts the displayed wall-clock time."""
    now = datetime(2026, 8, 21, 20, 30, tzinfo=timezone.utc)  # 20:30 UTC
    session = _session_with_user_messages([now])
    result = enrich_system_prompt_with_time(
        "Base.", session, "America/Denver", 0, now_utc=now
    )
    # America/Denver in August is MDT = UTC-6 -> 14:30 local.
    assert "14:30" in result
    assert "MDT" in result


def test_unknown_timezone_falls_back_to_utc():
    now = datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc)
    session = _session_with_user_messages([now])
    result = enrich_system_prompt_with_time(
        "Base.", session, "Mars/Olympus", 0, now_utc=now
    )
    assert "2026-08-21 14:30" in result
    assert "UTC" in result


def test_no_elapsed_note_on_first_turn():
    now = datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc)
    session = _session_with_user_messages([now])  # only one user message
    result = enrich_system_prompt_with_time(
        "Base.", session, "UTC", 5, now_utc=now
    )
    assert "Time Since Previous Message" not in result


def test_no_elapsed_note_when_gap_below_threshold():
    now = datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc)
    session = _session_with_user_messages([now - timedelta(seconds=30), now])
    result = enrich_system_prompt_with_time(
        "Base.", session, "UTC", 5, now_utc=now
    )
    # 30s gap < 5 minutes -> no note (current time still injected).
    assert "Time Since Previous Message" not in result
    assert "Current Date & Time" in result


def test_elapsed_note_when_gap_meets_threshold():
    now = datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc)
    session = _session_with_user_messages([now - timedelta(minutes=10), now])
    result = enrich_system_prompt_with_time(
        "Base.", session, "UTC", 5, now_utc=now
    )
    assert "Time Since Previous Message" in result
    assert "10 minutes" in result


def test_refresh_zero_disables_note_but_keeps_time():
    now = datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc)
    session = _session_with_user_messages([now - timedelta(hours=3), now])
    result = enrich_system_prompt_with_time(
        "Base.", session, "UTC", 0, now_utc=now
    )
    assert "Time Since Previous Message" not in result
    assert "Current Date & Time" in result


def test_empty_prompt_returned_unchanged():
    assert enrich_system_prompt_with_time("", Session(), "UTC", 5) == ""


def test_naive_previous_timestamp_treated_as_utc():
    now = datetime(2026, 8, 21, 14, 30, tzinfo=timezone.utc)
    # Previous user message carries a naive timestamp (as older persisted rows might).
    session = _session_with_user_messages(
        [datetime(2026, 8, 21, 13, 0), now]  # 13:00 naive -> treated as 13:00 UTC
    )
    result = enrich_system_prompt_with_time(
        "Base.", session, "UTC", 5, now_utc=now
    )
    # 90-minute gap >= 5 -> note present.
    assert "Time Since Previous Message" in result
    assert "90 minutes" in result


def test_prompt_provider_output_is_unchanged(tmp_path):
    """The provider stays pure: the time is added by the builder, not the provider."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system_prompt.md").write_text("Assistant for {user_email}.")
    config_manager = ConfigManager()
    config_manager.app_settings.prompt_base_path = str(prompts_dir)
    provider = PromptProvider(config_manager)
    assert provider.get_system_prompt(user_email="a@b.c") == "Assistant for a@b.c."


@pytest.mark.asyncio
async def test_message_builder_injects_time_into_default_prompt(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system_prompt.md").write_text("Assistant for {user_email}.")
    config_manager = ConfigManager()
    config_manager.app_settings.prompt_base_path = str(prompts_dir)
    config_manager.app_settings.system_prompt_timezone = "UTC"
    config_manager.app_settings.system_prompt_time_refresh_minutes = 5

    builder = MessageBuilder(
        prompt_provider=PromptProvider(config_manager),
        config_manager=config_manager,
    )
    session = Session(user_email="test@example.com")
    session.history.add_message(Message(role=MessageRole.USER, content="Hello"))

    messages = await builder.build_messages(
        session=session, include_files_manifest=False, include_system_prompt=True
    )
    assert messages[0]["role"] == "system"
    assert "Assistant for test@example.com." in messages[0]["content"]
    assert "Current Date & Time" in messages[0]["content"]
    # First turn -> no elapsed note.
    assert "Time Since Previous Message" not in messages[0]["content"]


@pytest.mark.asyncio
async def test_message_builder_appends_elapsed_note_after_long_gap(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system_prompt.md").write_text("Assistant for {user_email}.")
    config_manager = ConfigManager()
    config_manager.app_settings.prompt_base_path = str(prompts_dir)
    config_manager.app_settings.system_prompt_timezone = "UTC"
    config_manager.app_settings.system_prompt_time_refresh_minutes = 5

    builder = MessageBuilder(
        prompt_provider=PromptProvider(config_manager),
        config_manager=config_manager,
    )
    now = datetime.now(timezone.utc)
    session = _session_with_user_messages(
        [now - timedelta(minutes=30), now]  # 30-min gap -> note fires
    )

    messages = await builder.build_messages(
        session=session, include_files_manifest=False, include_system_prompt=True
    )
    content = messages[0]["content"]
    assert "Current Date & Time" in content
    assert "Time Since Previous Message" in content
    assert "30 minutes" in content


@pytest.mark.asyncio
async def test_message_builder_injects_time_into_custom_prompt(tmp_path):
    """A custom system prompt still gets the time appended (runtime enrichment)."""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system_prompt.md").write_text("Default for {user_email}.")
    config_manager = ConfigManager()
    config_manager.app_settings.prompt_base_path = str(prompts_dir)

    builder = MessageBuilder(
        prompt_provider=PromptProvider(config_manager),
        config_manager=config_manager,
    )
    session = Session(user_email="test@example.com")
    session.history.add_message(Message(role=MessageRole.USER, content="Hello"))

    messages = await builder.build_messages(
        session=session,
        include_files_manifest=False,
        include_system_prompt=True,
        custom_system_prompt="You only speak in haiku.",
    )
    content = messages[0]["content"]
    # Custom text is preserved verbatim and leads the message.
    assert content.startswith("You only speak in haiku.")
    # Default template text must not leak in (issue #153 contract preserved).
    assert "Default for" not in content
    # Time enrichment is appended.
    assert "Current Date & Time" in content


@pytest.mark.asyncio
async def test_message_builder_without_config_skips_time_injection(tmp_path):
    """No config_manager and no prompt_provider -> no system prompt, no time."""
    builder = MessageBuilder()
    session = Session(user_email="test@example.com")
    session.history.add_message(Message(role=MessageRole.USER, content="Hello"))
    messages = await builder.build_messages(
        session=session, include_files_manifest=False, include_system_prompt=True
    )
    # No provider -> no system message at all.
    assert all(m["role"] != "system" for m in messages)
