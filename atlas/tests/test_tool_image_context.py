"""Regression tests for MCP tool-returned images reaching the LLM (#909).

Issue #909: an MCP tool's ImageContent blocks were routed to the frontend
canvas but never entered the LLM transcript -- a screenshot-only tool
normalized to ``{"results": {}}`` for the model. These tests cover:

- extraction/validation of image artifacts for LLM vision input
- the synthetic user message appended after a step's tool results
- vision gating (note appended to the tool message when unsupported)
- the rolling most-recent-N and size-budget caps
- end-to-end wiring through the agentic loop and the tools-mode workflow
"""

import base64
import os
import sys
from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from atlas.application.chat.agent.agentic_loop import AgenticLoop
from atlas.application.chat.agent.protocols import AgentContext
from atlas.application.chat.utilities.tool_executor import execute_tools_workflow
from atlas.application.chat.utilities.tool_image_context import (
    MAX_TOOL_IMAGES_PER_TURN,
    ToolImageInjector,
    build_tool_image_message,
    extract_llm_ready_images,
    model_supports_vision,
)
from atlas.domain.messages.models import ConversationHistory, ToolResult
from atlas.interfaces.llm import LLMResponse

_PNG_B64 = base64.b64encode(
    b"\x89PNG\r\n\x1a\nfake-png-payload"
).decode()
_JPEG_B64 = base64.b64encode(b"\xff\xd8\xff\xe0fake-jpeg-payload").decode()


def _png_artifact(name="mcp_image_0.png"):
    return {
        "name": name,
        "b64": _PNG_B64,
        "mime": "image/png",
        "viewer": "image",
        "description": "Image returned by screenshot_tool",
    }


def _image_result(tool_call_id="call_1", artifacts=None):
    return ToolResult(
        tool_call_id=tool_call_id,
        content='{"results": {}}',
        success=True,
        artifacts=artifacts if artifacts is not None else [_png_artifact()],
    )


# ---------------------------------------------------------------------------
# extract_llm_ready_images
# ---------------------------------------------------------------------------

class TestExtractLlmReadyImages:
    def test_extracts_valid_png_artifact(self):
        images = extract_llm_ready_images([_png_artifact()], "shot")
        assert len(images) == 1
        assert images[0]["mime"] == "image/png"
        assert images[0]["b64"] == _PNG_B64

    def test_skips_svg_even_though_extraction_allowlists_it(self):
        # mcp_result_processor accepts image/svg+xml for the canvas; the LLM
        # path must not (providers reject it as image input).
        svg = {
            "name": "mcp_image_0.svg",
            "b64": base64.b64encode(b"<svg/>").decode(),
            "mime": "image/svg+xml",
            "viewer": "image",
        }
        assert extract_llm_ready_images([svg], "shot") == []

    def test_infers_mime_from_extension_when_missing(self):
        artifact = {
            "name": "render.jpg",
            "b64": _JPEG_B64,
            "viewer": "image",
        }
        images = extract_llm_ready_images([artifact], "shot")
        assert len(images) == 1
        assert images[0]["mime"] == "image/jpeg"

    def test_explicit_mime_is_decisive_even_when_extension_looks_like_an_image(self):
        # {"name": "shot.png", "mime": "image/svg+xml"} must be skipped, not
        # relabelled PNG by the filename extension: the provider request
        # would fail on the magic-byte mismatch behind the data URI.
        artifact = {
            "name": "shot.png",
            "b64": base64.b64encode(b"<svg/>").decode(),
            "mime": "image/svg+xml",
            "viewer": "image",
        }
        assert extract_llm_ready_images([artifact], "shot") == []

    def test_explicit_allowlisted_mime_wins_over_extension(self):
        artifact = {"name": "weird.svg", "b64": _PNG_B64, "mime": "image/png"}
        images = extract_llm_ready_images([artifact], "shot")
        assert images and images[0]["mime"] == "image/png"

    def test_skips_invalid_base64(self):
        artifact = {
            "name": "mcp_image_0.png",
            "b64": "not-valid-base64!!!",
            "mime": "image/png",
            "viewer": "image",
        }
        assert extract_llm_ready_images([artifact], "shot") == []

    def test_skips_oversized_image(self):
        from atlas.application.chat.utilities.tool_image_context import (
            MAX_TOOL_IMAGE_B64_BYTES,
        )
        artifact = {
            "name": "big.png",
            "b64": base64.b64encode(b"x" * (MAX_TOOL_IMAGE_B64_BYTES)).decode(),
            "mime": "image/png",
        }
        assert extract_llm_ready_images([artifact], "shot") == []

    def test_tolerates_newline_chunked_base64(self):
        chunked = "\n".join(_PNG_B64[i:i + 8] for i in range(0, len(_PNG_B64), 8))
        artifact = {"name": "a.png", "b64": chunked, "mime": "image/png"}
        images = extract_llm_ready_images([artifact], "shot")
        assert images and images[0]["b64"] == _PNG_B64

    def test_handles_garbage_input(self):
        assert extract_llm_ready_images(None) == []
        assert extract_llm_ready_images([]) == []
        assert extract_llm_ready_images(["nope", 42, {"name": "x"}]) == []

    def test_multiple_images_preserved_in_order(self):
        artifacts = [
            _png_artifact("a.png"),
            {"name": "b.jpg", "b64": _JPEG_B64, "mime": "image/jpeg"},
        ]
        images = extract_llm_ready_images(artifacts, "shot")
        assert [i["name"] for i in images] == ["a.png", "b.jpg"]


# ---------------------------------------------------------------------------
# build_tool_image_message
# ---------------------------------------------------------------------------

class TestBuildToolImageMessage:
    def test_message_shape_is_openai_image_url_blocks(self):
        message = build_tool_image_message(
            [{"name": "a.png", "b64": _PNG_B64, "mime": "image/png"}],
            ["screenshot_tool"],
        )
        assert message["role"] == "user"
        assert isinstance(message["content"], list)
        assert message["content"][0]["type"] == "text"
        assert "screenshot_tool" in message["content"][0]["text"]
        image_block = message["content"][1]
        assert image_block["type"] == "image_url"
        assert image_block["image_url"]["url"].startswith("data:image/png;base64,")
        assert _PNG_B64 in image_block["image_url"]["url"]

    def test_singular_wording_for_one_image(self):
        message = build_tool_image_message(
            [{"name": "a.png", "b64": _PNG_B64, "mime": "image/png"}],
            ["t"],
        )
        assert "1 image," in message["content"][0]["text"]

    def test_deduplicates_tool_names(self):
        message = build_tool_image_message(
            [
                {"name": "a.png", "b64": _PNG_B64, "mime": "image/png"},
                {"name": "b.png", "b64": _PNG_B64, "mime": "image/png"},
            ],
            ["t", "t", "u"],
        )
        assert "t, u" in message["content"][0]["text"]


# ---------------------------------------------------------------------------
# model_supports_vision
# ---------------------------------------------------------------------------

class TestModelSupportsVision:
    def _config(self, supports_vision):
        cfg = MagicMock()
        cfg.llm_config.models = {
            "vision-model": SimpleNamespace(supports_vision=True),
            "text-model": SimpleNamespace(supports_vision=False),
        }
        return cfg

    def test_true_only_when_configured(self):
        cfg = self._config(True)
        assert model_supports_vision(cfg, "vision-model") is True
        assert model_supports_vision(cfg, "text-model") is False

    def test_unknown_model_is_false(self):
        cfg = self._config(True)
        assert model_supports_vision(cfg, "missing") is False

    def test_no_config_is_false(self):
        assert model_supports_vision(None, "vision-model") is False


# ---------------------------------------------------------------------------
# ToolImageInjector
# ---------------------------------------------------------------------------

class TestToolImageInjector:
    def test_injects_synthetic_user_message_after_tool_results(self):
        messages = [
            {"role": "user", "content": "screenshot the model"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1"}]},
            {"role": "tool", "content": '{"results": {}}', "tool_call_id": "call_1"},
        ]
        injector = ToolImageInjector(enabled=True)
        injector.after_tool_results(
            messages, [_image_result()],
            tool_names={"call_1": "fusion360_fusion_mcp_read"},
        )
        assert len(messages) == 4
        injected = messages[-1]
        assert injected["role"] == "user"
        blocks = injected["content"]
        assert blocks[0]["type"] == "text"
        assert "fusion360_fusion_mcp_read" in blocks[0]["text"]
        assert blocks[1]["type"] == "image_url"
        assert _PNG_B64 in blocks[1]["image_url"]["url"]
        # The tool message itself stays text-only (providers reject images there).
        assert messages[2]["content"] == '{"results": {}}'

    def test_no_injection_without_image_artifacts(self):
        messages = [{"role": "user", "content": "hi"}]
        result = ToolResult(tool_call_id="c1", content="text only", artifacts=[])
        ToolImageInjector(enabled=True).after_tool_results(messages, [result])
        assert messages == [{"role": "user", "content": "hi"}]

    def test_vision_disabled_appends_separate_note_message(self):
        # The tool result JSON must stay untouched (it is often parsed);
        # the note rides as its own system message after the tool results.
        tool_content = '{"results": {}}'
        messages = [
            {"role": "user", "content": "screenshot"},
            {"role": "tool", "content": tool_content, "tool_call_id": "call_1"},
        ]
        injector = ToolImageInjector(enabled=False)
        injector.after_tool_results(
            messages, [_image_result()],
            tool_names={"call_1": "fusion360_fusion_mcp_read"},
        )
        assert len(messages) == 3
        assert messages[1]["content"] == tool_content  # JSON preserved
        note = messages[2]
        assert note["role"] == "system"
        assert "does not support vision" in note["content"]
        assert "fusion360_fusion_mcp_read" in note["content"]

    def test_vision_disabled_note_skipped_for_non_image_artifacts(self):
        messages = [{"role": "tool", "content": "plain", "tool_call_id": "c1"}]
        result = ToolResult(
            tool_call_id="c1", content="ok",
            artifacts=[{"name": "out.pptx", "b64": _PNG_B64, "mime": "application/vnd.x"}],
        )
        ToolImageInjector(enabled=False).after_tool_results(messages, [result])
        assert len(messages) == 1

    def test_rolling_cap_demotes_oldest_images(self):
        messages = []
        injector = ToolImageInjector(enabled=True)
        for step in range(MAX_TOOL_IMAGES_PER_TURN + 2):
            result = _image_result(tool_call_id=f"call_{step}")
            messages.append({"role": "tool", "content": "{}", "tool_call_id": f"call_{step}"})
            injector.after_tool_results(messages, [result])
        live_image_messages = [
            m for m in messages
            if isinstance(m.get("content"), list)
            and any(b.get("type") == "image_url" for b in m["content"] if isinstance(b, dict))
        ]
        demoted = [
            m for m in messages
            if isinstance(m.get("content"), str) and "removed from context" in m["content"]
        ]
        total_live = sum(
            1 for m in live_image_messages
            for b in m["content"] if isinstance(b, dict) and b.get("type") == "image_url"
        )
        assert total_live == MAX_TOOL_IMAGES_PER_TURN
        assert len(demoted) == 2

    def test_batch_over_cap_keeps_newest_images(self):
        # One tool result carrying more images than the rolling cap must not
        # create an oversized message that later demotes to zero images: the
        # newest cap-sized slice is kept up front.
        artifacts = [_png_artifact(f"mcp_image_{i}.png") for i in range(MAX_TOOL_IMAGES_PER_TURN + 3)]
        messages = []
        ToolImageInjector(enabled=True).after_tool_results(
            messages, [_image_result(artifacts=artifacts)],
        )
        image_blocks = [
            b for b in messages[-1]["content"]
            if isinstance(b, dict) and b.get("type") == "image_url"
        ]
        assert len(image_blocks) == MAX_TOOL_IMAGES_PER_TURN
        # The intro must say 6 (the retained count), not 9.
        assert f"returned {MAX_TOOL_IMAGES_PER_TURN} images" in messages[-1]["content"][0]["text"]

    def test_batch_boundary_demotes_individually_not_wholesale(self):
        # 4 images, then 4 more: the rolling cap of 6 must trim 2 images off
        # the oldest message (keeping its remaining images + a note-free
        # text) rather than dropping the entire first message.
        messages = []
        injector = ToolImageInjector(enabled=True)
        for step, batch in enumerate((4, 4)):
            artifacts = [_png_artifact(f"s{step}_img{i}.png") for i in range(batch)]
            messages.append({"role": "tool", "content": "{}", "tool_call_id": f"call_{step}"})
            injector.after_tool_results(
                messages, [_image_result(tool_call_id=f"call_{step}", artifacts=artifacts)],
            )
        live = [
            len([b for b in m["content"] if isinstance(b, dict) and b.get("type") == "image_url"])
            for m in messages
            if isinstance(m.get("content"), list)
        ]
        assert sum(live) == MAX_TOOL_IMAGES_PER_TURN
        assert live == [2, 4]  # oldest message trimmed by 2, not destroyed
        # No message was fully demoted to a note (the first still holds 2).
        assert not any(
            isinstance(m.get("content"), str) and "removed from context" in m.get("content", "")
            for m in messages
        )

    def test_budget_evicts_old_images_instead_of_rejecting_new(self):
        # With the aggregate budget exhausted by old images, a new image
        # must evict the oldest live images rather than being dropped --
        # most-recent-wins. Three images of ~4.2 MB base64 each overfill
        # the 12 MB budget, so each new injection demotes the oldest live
        # image until it fits.
        from atlas.application.chat.utilities.tool_image_context import (
            MAX_TOOL_IMAGE_TOTAL_B64_BYTES,
        )

        # A long run of "A" is valid base64 (decodes to zero bytes); the
        # string length is the payload size the caps see.
        big_b64 = "A" * (MAX_TOOL_IMAGE_TOTAL_B64_BYTES // 3 + 10000)
        messages = []
        injector = ToolImageInjector(enabled=True)
        for step in range(3):
            artifacts = [{"name": f"big{step}.png", "b64": big_b64, "mime": "image/png"}]
            messages.append({"role": "tool", "content": "{}", "tool_call_id": f"call_{step}"})
            injector.after_tool_results(
                messages, [_image_result(tool_call_id=f"call_{step}", artifacts=artifacts)],
            )
        demoted_notes = [
            m for m in messages
            if isinstance(m.get("content"), str) and "removed from context" in m.get("content", "")
        ]
        live_image_messages = [
            m for m in messages
            if isinstance(m.get("content"), list)
            and any(b.get("type") == "image_url" for b in m["content"] if isinstance(b, dict))
        ]
        # The oldest message was demoted to a note; the two newest images
        # fit side by side and both stay live (each is just over a third of
        # the 12 MB budget -- under the 5 MB per-image cap -- so only one
        # eviction was needed to admit the third).
        assert len(demoted_notes) == 1
        assert len(live_image_messages) == 2
        live_urls = [
            b["image_url"]["url"]
            for m in live_image_messages
            for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "image_url"
        ]
        assert len(live_urls) == 2
        assert all(big_b64 in url for url in live_urls)

    def test_injection_never_raises_on_bad_results(self):
        messages = [{"role": "user", "content": "hi"}]
        injector = ToolImageInjector(enabled=True)
        weird = SimpleNamespace(tool_call_id="c1", artifacts="not-a-list")
        injector.after_tool_results(messages, [weird])
        assert messages == [{"role": "user", "content": "hi"}]

    def test_noop_results_list(self):
        messages = [{"role": "user", "content": "hi"}]
        ToolImageInjector(enabled=True).after_tool_results(messages, [])
        assert messages == [{"role": "user", "content": "hi"}]

    def test_stripped_image_blocks_terminate_instead_of_hanging(self):
        # A live entry whose image blocks were stripped externally (while its
        # bookkeeping still claims images) must be retired on the next
        # over-cap injection -- a zero-progress return would leave both
        # caller loops spinning the request handler at 100% CPU.
        messages = []
        injector = ToolImageInjector(enabled=True)
        injector.after_tool_results(messages, [_image_result()])
        injected = messages[-1]
        injected["content"] = [
            block for block in injected["content"]
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        batch = [_png_artifact(f"mcp_image_{i}.png") for i in range(MAX_TOOL_IMAGES_PER_TURN)]
        messages.append({"role": "tool", "content": "{}", "tool_call_id": "call_2"})
        injector.after_tool_results(
            messages, [_image_result(tool_call_id="call_2", artifacts=batch)],
        )
        # The stale entry was replaced in place with the demotion note.
        assert isinstance(injected["content"], str)
        assert "removed from context" in injected["content"]
        # The newest batch is fully live (cap respected, loop terminated).
        live_blocks = [
            block for block in messages[-1]["content"]
            if isinstance(block, dict) and block.get("type") == "image_url"
        ]
        assert len(live_blocks) == MAX_TOOL_IMAGES_PER_TURN

    def test_budget_rejection_appends_explanation_note(self, monkeypatch):
        # When every image of a step is rejected by the aggregate budget the
        # model must still learn images existed -- silence would leave it
        # with {"results": {}} and no explanation (the original symptom).
        import atlas.application.chat.utilities.tool_image_context as tic

        monkeypatch.setattr(tic, "MAX_TOOL_IMAGE_TOTAL_B64_BYTES", 10)
        messages = []
        injector = tic.ToolImageInjector(enabled=True)
        messages.append({"role": "tool", "content": "{}", "tool_call_id": "c1"})
        injector.after_tool_results(
            messages, [_image_result()], tool_names={"c1": "screenshot_tool"},
        )
        note = messages[-1]
        assert note["role"] == "system"
        assert "exceeded the" in note["content"]
        assert "screenshot_tool" in note["content"]
        assert not any(isinstance(m.get("content"), list) for m in messages)

    def test_tool_label_sanitized_in_notes_and_intro(self):
        # Tool names land inside user/system transcript messages; MCP-server
        # prose must not. Unmapped ids fall back to the artifact description
        # and get bounded to a filename-like subset.
        prose = "Image returned by ssh -i key user@host; rm -rf / && pwned"
        artifacts = [dict(_png_artifact(), description=prose)]
        messages = [{"role": "tool", "content": "{}", "tool_call_id": "cx"}]
        ToolImageInjector(enabled=True).after_tool_results(
            messages, [_image_result(artifacts=artifacts)],
        )
        intro = messages[-1]["content"][0]["text"]
        assert "rm -rf" not in intro
        assert "@" not in intro
        assert ";" not in intro
        assert "ssh" in intro  # the sanitized prefix survives

        note_messages = [{"role": "tool", "content": "{}", "tool_call_id": "cx"}]
        ToolImageInjector(enabled=False).after_tool_results(
            note_messages, [_image_result(artifacts=artifacts)],
        )
        note = note_messages[-1]["content"]
        assert "rm -rf" not in note
        assert "@" not in note


# ---------------------------------------------------------------------------
# Agentic loop integration
# ---------------------------------------------------------------------------

class _FakeLLM:
    """Queued responses; records every message list it is called with."""

    def __init__(self, responses: List[LLMResponse]):
        self._responses = list(responses)
        self.call_count = 0
        self.message_history: List[List[dict]] = []

    async def call_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                              temperature=0.7, user_email=None):
        self.call_count += 1
        self.message_history.append([dict(m) for m in messages])
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="done")

    async def call_plain(self, model, messages, temperature=0.7, user_email=None):
        self.call_count += 1
        self.message_history.append([dict(m) for m in messages])
        return "final"


def _make_context():
    return AgentContext(
        session_id=uuid4(),
        user_email="test@example.com",
        files={},
        history=ConversationHistory(),
    )


def _vision_config():
    """Config manager stub marking 'vision-model' as vision-capable."""
    cfg = MagicMock()
    cfg.llm_config.models = {
        "vision-model": SimpleNamespace(supports_vision=True),
        "text-model": SimpleNamespace(supports_vision=False),
    }
    return cfg


def _collect_events():
    async def handler(event):
        pass
    return handler


class TestAgenticLoopInjectsToolImages:
    @pytest.mark.asyncio
    async def test_image_reaches_next_llm_call(self):
        png_call = SimpleNamespace(
            id="call_1", type="function",
            function=SimpleNamespace(name="screenshot", arguments="{}"),
        )
        llm = _FakeLLM([
            LLMResponse(content="", tool_calls=[png_call]),
            LLMResponse(content="I can see the rocket now"),
        ])
        tool_mgr = MagicMock()

        async def fake_execute(tool_call_obj, context=None):
            return _image_result(tool_call_id=tool_call_obj.id)

        tool_mgr.execute_tool = AsyncMock(side_effect=fake_execute)
        tool_mgr.get_tools_schema = MagicMock(return_value=[
            {"type": "function", "function": {"name": "screenshot", "parameters": {}}}
        ])

        loop = AgenticLoop(llm=llm, tool_manager=tool_mgr, prompt_provider=None,
                           config_manager=_vision_config())
        loop.skip_approval = True
        messages = [{"role": "user", "content": "screenshot the model"}]

        await loop.run(
            model="vision-model", messages=messages, context=_make_context(),
            selected_tools=["screenshot"], data_sources=None, max_steps=5,
            temperature=0.7, event_handler=_collect_events(),
        )

        second_call_messages = llm.message_history[1]
        injected = [m for m in second_call_messages if m.get("role") == "user" and isinstance(m.get("content"), list)]
        assert len(injected) == 1
        assert any(
            b.get("type") == "image_url" and _PNG_B64 in b["image_url"]["url"]
            for b in injected[0]["content"] if isinstance(b, dict)
        )

    @pytest.mark.asyncio
    async def test_vision_gated_off_leaves_note_not_image(self):
        png_call = SimpleNamespace(
            id="call_1", type="function",
            function=SimpleNamespace(name="screenshot", arguments="{}"),
        )
        llm = _FakeLLM([
            LLMResponse(content="", tool_calls=[png_call]),
            LLMResponse(content="done"),
        ])
        tool_mgr = MagicMock()

        async def fake_execute(tool_call_obj, context=None):
            return _image_result(tool_call_id=tool_call_obj.id)

        tool_mgr.execute_tool = AsyncMock(side_effect=fake_execute)
        tool_mgr.get_tools_schema = MagicMock(return_value=[
            {"type": "function", "function": {"name": "screenshot", "parameters": {}}}
        ])

        # No config manager -> model_supports_vision is False -> gating.
        loop = AgenticLoop(llm=llm, tool_manager=tool_mgr, prompt_provider=None)
        loop.skip_approval = True
        messages = [{"role": "user", "content": "screenshot"}]

        await loop.run(
            model="text-model", messages=messages, context=_make_context(),
            selected_tools=["screenshot"], data_sources=None, max_steps=5,
            temperature=0.7, event_handler=_collect_events(),
        )

        second_call_messages = llm.message_history[1]
        assert not any(isinstance(m.get("content"), list) for m in second_call_messages)
        tool_messages = [m for m in second_call_messages if m.get("role") == "tool"]
        # The note rides as its own system message; the tool JSON is untouched.
        system_notes = [m for m in second_call_messages if m.get("role") == "system"]
        assert any(
            "does not support vision" in (m.get("content") or "") for m in system_notes
        )
        assert tool_messages[0]["content"] == '{"results": {}}'


# ---------------------------------------------------------------------------
# Tools-mode workflow integration
# ---------------------------------------------------------------------------

class TestSynthesisUserQuestionLookup:
    @pytest.mark.asyncio
    async def test_list_content_user_messages_skipped_for_question(self):
        # The synthetic tool-image message (and any multimodal user turn)
        # carries a list of content blocks; the synthesis prompt lookup must
        # skip it and use the real textual question instead. Feeding the
        # list to the prompt provider's ``.strip()`` used to raise and be
        # swallowed, silently dropping the configured synthesis prompt.
        from atlas.application.chat.utilities.tool_executor import (
            synthesize_tool_results,
        )

        class _PromptProvider:
            def get_tool_synthesis_prompt(self, user_question):
                return f"PROMPT[{user_question}]"

        class _LlmCaller:
            def __init__(self):
                self.prompts_seen = []

            async def call_plain(self, model, messages, user_email=None):
                self.prompts_seen.extend(
                    m["content"] for m in messages
                    if m.get("role") == "system" and str(m.get("content", "")).startswith("PROMPT")
                )
                return "answer"

        caller = _LlmCaller()
        messages = [
            {"role": "user", "content": "check the rocket"},
            {"role": "tool", "content": "{}", "tool_call_id": "c1"},
            build_tool_image_message(
                [{"name": "a.png", "b64": _PNG_B64, "mime": "image/png"}],
                ["screenshot_tool"],
            ),
        ]
        await synthesize_tool_results(
            model="m", messages=messages, llm_caller=caller,
            prompt_provider=_PromptProvider(),
        )
        assert caller.prompts_seen == ["PROMPT[check the rocket]"]

    @pytest.mark.asyncio
    async def test_text_blocks_of_genuine_multimodal_user_turn_are_used(self):
        # A real user turn that carries attachments (text + image/PDF blocks)
        # is still the question: its text blocks feed the synthesis prompt.
        from atlas.application.chat.utilities.tool_executor import (
            synthesize_tool_results,
        )

        class _PromptProvider:
            def get_tool_synthesis_prompt(self, user_question):
                return f"PROMPT[{user_question}]"

        class _LlmCaller:
            def __init__(self):
                self.prompts_seen = []

            async def call_plain(self, model, messages, user_email=None):
                self.prompts_seen.extend(
                    m["content"] for m in messages
                    if m.get("role") == "system" and str(m.get("content", "")).startswith("PROMPT")
                )
                return "answer"

        caller = _LlmCaller()
        messages = [
            {"role": "user", "content": [
                {"type": "text", "text": "compare with this chart"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,x"}},
            ]},
            {"role": "tool", "content": "{}", "tool_call_id": "c1"},
        ]
        await synthesize_tool_results(
            model="m", messages=messages, llm_caller=caller,
            prompt_provider=_PromptProvider(),
        )
        assert caller.prompts_seen == ["PROMPT[compare with this chart]"]

    @pytest.mark.asyncio
    async def test_demoted_tool_image_note_is_not_the_question(self):
        # After the rolling cap empties an injected message, the message is
        # left as a plain-string note -- which the string-only lookup would
        # mistake for the user's question. The structural check must skip it.
        from atlas.application.chat.utilities.tool_executor import (
            synthesize_tool_results,
        )
        from atlas.application.chat.utilities.tool_image_context import (
            _INTRO_PREFIX,
        )

        class _PromptProvider:
            def get_tool_synthesis_prompt(self, user_question):
                return f"PROMPT[{user_question}]"

        class _LlmCaller:
            def __init__(self):
                self.prompts_seen = []

            async def call_plain(self, model, messages, user_email=None):
                self.prompts_seen.extend(
                    m["content"] for m in messages
                    if m.get("role") == "system" and str(m.get("content", "")).startswith("PROMPT")
                )
                return "answer"

        caller = _LlmCaller()
        messages = [
            {"role": "user", "content": "check the rocket"},
            {"role": "tool", "content": "{}", "tool_call_id": "c1"},
            {"role": "user", "content": _INTRO_PREFIX + "3 image(s) returned "
             "by a tool were removed from context to stay within the "
             "tool-image budget. The file(s) remain in the session files.]"},
        ]
        await synthesize_tool_results(
            model="m", messages=messages, llm_caller=caller,
            prompt_provider=_PromptProvider(),
        )
        assert caller.prompts_seen == ["PROMPT[check the rocket]"]


class TestExecuteToolsWorkflowInjectsToolImages:
    @pytest.mark.asyncio
    async def test_images_reach_synthesis_call(self):
        llm_response = LLMResponse(
            content="",
            tool_calls=[SimpleNamespace(
                id="call_1", type="function",
                function=SimpleNamespace(name="screenshot", arguments="{}"),
            )],
        )
        tool_mgr = MagicMock()

        async def fake_execute(tool_call_obj, context=None):
            return _image_result(tool_call_id=tool_call_obj.id)

        tool_mgr.execute_tool = AsyncMock(side_effect=fake_execute)

        class _LlmCaller:
            def __init__(self):
                self.calls = []

            async def call_plain(self, model, messages, user_email=None):
                self.calls.append(messages)
                return "I can see it"

        llm_caller = _LlmCaller()

        messages = [{"role": "user", "content": "screenshot"}]
        final, tool_results = await execute_tools_workflow(
            llm_response=llm_response,
            messages=messages,
            model="vision-model",
            session_context={},
            tool_manager=tool_mgr,
            llm_caller=llm_caller,
            prompt_provider=None,
            skip_approval=True,
            image_injector=ToolImageInjector(enabled=True),
        )

        assert final == "I can see it"
        synthesis_messages = llm_caller.calls[-1]
        image_messages = [
            m for m in synthesis_messages
            if m.get("role") == "user" and isinstance(m.get("content"), list)
            and any(
                isinstance(b, dict) and b.get("type") == "image_url"
                for b in m["content"]
            )
        ]
        assert len(image_messages) == 1
        assert tool_results[0].success is True

    @pytest.mark.asyncio
    async def test_without_injector_behavior_unchanged(self):
        llm_response = LLMResponse(
            content="",
            tool_calls=[SimpleNamespace(
                id="call_1", type="function",
                function=SimpleNamespace(name="screenshot", arguments="{}"),
            )],
        )
        tool_mgr = MagicMock()

        async def fake_execute(tool_call_obj, context=None):
            return _image_result(tool_call_id=tool_call_obj.id)

        tool_mgr.execute_tool = AsyncMock(side_effect=fake_execute)

        class _LlmCaller:
            async def call_plain(self, model, messages, user_email=None):
                return "answer"

        messages = [{"role": "user", "content": "screenshot"}]
        await execute_tools_workflow(
            llm_response=llm_response,
            messages=messages,
            model="vision-model",
            session_context={},
            tool_manager=tool_mgr,
            llm_caller=_LlmCaller(),
            prompt_provider=None,
            skip_approval=True,
            # No image_injector: legacy behavior (no image message).
        )
        assert not any(
            m.get("role") == "user" and isinstance(m.get("content"), list)
            for m in messages
        )


# ---------------------------------------------------------------------------
# Tools-mode continuation loop integration
# ---------------------------------------------------------------------------

class _ScriptedStreamingLLM:
    """stream_with_tools pops one scripted turn per call (text, tool_calls)."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.seen_messages: List[List[dict]] = []

    async def stream_with_tools(self, model, messages, tools_schema, tool_choice="auto",
                                temperature=0.7, user_email=None):
        self.seen_messages.append([dict(m) for m in messages])
        text, tool_calls = self._turns.pop(0) if self._turns else (None, None)
        if text:
            yield text
        yield LLMResponse(content=text or "", tool_calls=tool_calls)

    async def stream_plain(self, model, messages, temperature=0.7, user_email=None):
        yield "synthesized"


class TestToolsModeContinuationInjectsToolImages:
    @pytest.mark.asyncio
    async def test_continuation_round_sees_image_with_real_tool_name(self):
        # The tools-mode streaming continuation loop must inject the step's
        # image so the next round (and synthesis) can see it, labeled with
        # the real tool name from the call map.
        from atlas.application.chat.modes.tools import ToolsModeRunner

        def _tc(call_id, name, arguments="{}"):
            return SimpleNamespace(
                id=call_id, type="function",
                function=SimpleNamespace(name=name, arguments=arguments),
            )

        llm = _ScriptedStreamingLLM(turns=[
            ("taking the shot", [_tc("c1", "fusion_shot")]),
            ("I can see the rocket now.", None),
        ])

        async def _execute_multiple(tool_calls, session_context, tool_manager,
                                    update_callback=None, config_manager=None,
                                    skip_approval=False):
            return [_image_result(tool_call_id=tc.id) for tc in tool_calls]

        config = SimpleNamespace(
            llm_config=SimpleNamespace(
                models={"test-model": SimpleNamespace(supports_vision=True)},
            ),
            app_settings=SimpleNamespace(
                tools_mode_max_extra_rounds=3,
                feature_agent_mode_available=False,
            ),
        )
        tool_manager = MagicMock()
        tool_manager.get_tools_schema = MagicMock(return_value=[{"type": "function"}])
        runner = ToolsModeRunner(
            llm=llm,
            tool_manager=tool_manager,
            event_publisher=AsyncMock(),
            config_manager=config,
        )
        session = MagicMock()
        session.history = MagicMock()
        session.history.add_message = MagicMock()
        session.session_id = "s1"
        session.files = {}

        with patch("atlas.application.chat.modes.tools.tool_executor") as mock_te:
            mock_te.execute_multiple_tools = _execute_multiple
            mock_te.build_files_manifest = MagicMock(return_value=None)
            await runner.run_streaming(
                session=session,
                model="test-model",
                messages=[{"role": "user", "content": "screenshot the model"}],
                selected_tools=["fusion_shot"],
            )

        continuation_messages = llm.seen_messages[1]
        injected = [
            m for m in continuation_messages
            if m.get("role") == "user" and isinstance(m.get("content"), list)
        ]
        assert len(injected) == 1
        blocks = injected[0]["content"]
        assert "fusion_shot" in blocks[0]["text"]
        assert any(
            isinstance(block, dict) and block.get("type") == "image_url"
            for block in blocks
        )
