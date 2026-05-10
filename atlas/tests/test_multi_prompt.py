"""Tests for multi-prompt support and meta passing."""
from unittest.mock import AsyncMock

import pytest

from atlas.application.chat.preprocessors.prompt_override_service import (
    PromptOverrideService,
)


@pytest.fixture
def mock_tool_manager():
    tm = AsyncMock()
    return tm


@pytest.fixture
def service(mock_tool_manager):
    return PromptOverrideService(tool_manager=mock_tool_manager)


class TestMultiPromptSupport:
    @pytest.mark.asyncio
    async def test_single_prompt_still_works(self, service, mock_tool_manager):
        """Backward compat: single prompt applied as system message."""
        mock_tool_manager.get_prompt.return_value = "You are a wizard."
        messages = [{"role": "user", "content": "Hello"}]

        result = await service.apply_prompt_override(messages, ["server_wizard"])
        assert result[0] == {"role": "system", "content": "You are a wizard."}
        assert result[1] == {"role": "user", "content": "Hello"}

    @pytest.mark.asyncio
    async def test_multiple_prompts_all_applied(self, service, mock_tool_manager):
        """All selected prompts are applied, not just the first."""
        async def mock_get_prompt(server, name, **kwargs):
            return {"wizard": "You are a wizard.", "analyst": "You are an analyst."}[name]

        mock_tool_manager.get_prompt = mock_get_prompt
        messages = [{"role": "user", "content": "Hello"}]

        result = await service.apply_prompt_override(
            messages, ["server_wizard", "server_analyst"]
        )
        # Both prompts prepended
        assert len(result) == 3
        assert result[0]["content"] == "You are a wizard."
        assert result[1]["content"] == "You are an analyst."
        assert result[2]["content"] == "Hello"

    @pytest.mark.asyncio
    async def test_failed_prompt_skipped_others_applied(self, service, mock_tool_manager):
        """If one prompt fails, others still get applied."""
        call_count = 0

        async def mock_get_prompt(server, name, **kwargs):
            nonlocal call_count
            call_count += 1
            if name == "bad":
                raise Exception("Server down")
            return "Good prompt."

        mock_tool_manager.get_prompt = mock_get_prompt
        messages = [{"role": "user", "content": "Hello"}]

        result = await service.apply_prompt_override(
            messages, ["server_bad", "server_good"]
        )
        assert len(result) == 2  # one prompt + original message
        assert result[0]["content"] == "Good prompt."

    @pytest.mark.asyncio
    async def test_meta_forwarded_to_get_prompt(self, service, mock_tool_manager):
        """user_email and conversation_id are passed as meta dict."""
        mock_tool_manager.get_prompt.return_value = "Personalized prompt."
        messages = [{"role": "user", "content": "Hello"}]

        await service.apply_prompt_override(
            messages, ["server_wizard"],
            user_email="alice@example.com",
            conversation_id="conv-123",
        )
        mock_tool_manager.get_prompt.assert_called_once_with(
            "server", "wizard",
            meta={"user_email": "alice@example.com", "conversation_id": "conv-123"},
            user_email="alice@example.com",
            conversation_id="conv-123",
        )

    @pytest.mark.asyncio
    async def test_reports_applied_prompt_content(self, service, mock_tool_manager):
        """Resolved prompt content is reported for chat export bookkeeping."""
        mock_tool_manager.get_prompt.return_value = "You are an analyst."
        callback = AsyncMock()
        messages = [{"role": "user", "content": "Hello"}]

        await service.apply_prompt_override(
            messages,
            ["server_analyst"],
            applied_prompt_callback=callback,
        )

        callback.assert_called_once_with({
            "type": "prompt_applied",
            "prompt_key": "server_analyst",
            "server": "server",
            "name": "analyst",
            "content": "You are an analyst.",
        })

    @pytest.mark.asyncio
    async def test_no_prompts_returns_unchanged(self, service):
        messages = [{"role": "user", "content": "Hello"}]
        result = await service.apply_prompt_override(messages, None)
        assert result == messages

        result = await service.apply_prompt_override(messages, [])
        assert result == messages


class TestPromptTextExtraction:
    def test_extract_string(self, service):
        assert service._extract_prompt_text("hello") == "hello"

    def test_extract_multi_content(self, service):
        """Concatenates all TextContent items, not just first."""
        from types import SimpleNamespace

        item1 = SimpleNamespace(text="Part 1.")
        item2 = SimpleNamespace(text=" Part 2.")

        prompt_obj = SimpleNamespace(content=[item1, item2])

        result = service._extract_prompt_text(prompt_obj)
        assert "Part 1." in result
        assert "Part 2." in result

    def test_extract_single_content(self, service):
        """Single content item still works."""
        from types import SimpleNamespace

        item = SimpleNamespace(text="Only part.")
        prompt_obj = SimpleNamespace(content=[item])

        result = service._extract_prompt_text(prompt_obj)
        assert result == "Only part."
