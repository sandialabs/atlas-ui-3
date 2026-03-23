"""Tests for vision image support in message building and file processing.

Verifies that:
- ModelConfig correctly recognizes supports_vision
- handle_session_files stores image_b64 for vision models
- build_files_manifest excludes vision images when exclude_vision_images=True
- MessageBuilder embeds image content blocks in the last user message
"""

import base64
import uuid

import pytest

from atlas.application.chat.preprocessors.message_builder import (
    MessageBuilder,
    _build_vision_user_message,
)
from atlas.application.chat.utilities.file_processor import (
    build_files_manifest,
    handle_session_files,
)
from atlas.domain.messages.models import Message, MessageRole
from atlas.domain.sessions.models import Session
from atlas.modules.config.config_manager import LLMConfig, ModelConfig
from atlas.modules.file_storage.manager import FileManager
from atlas.modules.file_storage.mock_s3_client import MockS3StorageClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(user_email="test@example.com") -> Session:
    sid = uuid.uuid4()
    return Session(id=sid, user_email=user_email)


def _make_file_manager() -> FileManager:
    return FileManager(s3_client=MockS3StorageClient())


def _png_b64() -> str:
    """Tiny 1x1 white PNG as base64."""
    # Minimal valid PNG bytes
    raw = (
        b"\x89PNG\r\n\x1a\n"  # signature
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
        b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00"
        b"\x00\x00\x00IEND\xaeB`\x82"
    )
    return base64.b64encode(raw).decode()


# ---------------------------------------------------------------------------
# ModelConfig tests
# ---------------------------------------------------------------------------

class TestModelConfigSupportsVision:
    def test_default_is_false(self):
        cfg = ModelConfig(model_name="gpt-4", model_url="http://x")
        assert cfg.supports_vision is False

    def test_can_be_set_true(self):
        cfg = ModelConfig(model_name="gpt-4o", model_url="http://x", supports_vision=True)
        assert cfg.supports_vision is True

    def test_llm_config_roundtrip(self):
        llm_cfg = LLMConfig(models={
            "vision-model": ModelConfig(
                model_name="gpt-4o", model_url="http://x", supports_vision=True
            ),
            "text-model": ModelConfig(
                model_name="gpt-3.5", model_url="http://x"
            ),
        })
        assert llm_cfg.models["vision-model"].supports_vision is True
        assert llm_cfg.models["text-model"].supports_vision is False


# ---------------------------------------------------------------------------
# handle_session_files tests
# ---------------------------------------------------------------------------

class TestHandleSessionFilesVision:
    @pytest.mark.asyncio
    async def test_stores_image_b64_for_vision_model(self):
        fm = _make_file_manager()
        b64 = _png_b64()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"photo.png": {"content": b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
        )
        file_ref = context["files"]["photo.png"]
        assert file_ref.get("image_b64") == b64
        assert file_ref.get("image_mime_type") == "image/png"

    @pytest.mark.asyncio
    async def test_no_image_b64_without_vision(self):
        fm = _make_file_manager()
        b64 = _png_b64()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"photo.png": {"content": b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=False,
        )
        file_ref = context["files"]["photo.png"]
        assert "image_b64" not in file_ref

    @pytest.mark.asyncio
    async def test_non_image_file_never_gets_image_b64(self):
        fm = _make_file_manager()
        txt_b64 = base64.b64encode(b"hello world").decode()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"readme.txt": {"content": txt_b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
        )
        file_ref = context["files"]["readme.txt"]
        assert "image_b64" not in file_ref


# ---------------------------------------------------------------------------
# build_files_manifest tests
# ---------------------------------------------------------------------------

class TestBuildFilesManifestExcludeVisionImages:
    def _ctx_with_image_and_text(self):
        return {
            "files": {
                "photo.png": {
                    "content_type": "image/png",
                    "extract_mode": "none",
                    "image_b64": "abc123",
                    "image_mime_type": "image/png",
                },
                "report.txt": {
                    "content_type": "text/plain",
                    "extract_mode": "none",
                },
            }
        }

    def test_includes_both_when_not_excluding(self):
        manifest = build_files_manifest(self._ctx_with_image_and_text())
        assert manifest is not None
        assert "photo.png" in manifest["content"]
        assert "report.txt" in manifest["content"]

    def test_excludes_vision_images_when_flag_set(self):
        manifest = build_files_manifest(
            self._ctx_with_image_and_text(), exclude_vision_images=True
        )
        assert manifest is not None
        assert "photo.png" not in manifest["content"]
        assert "report.txt" in manifest["content"]

    def test_returns_none_when_all_files_are_vision_images(self):
        ctx = {
            "files": {
                "a.png": {
                    "content_type": "image/png",
                    "extract_mode": "none",
                    "image_b64": "data",
                    "image_mime_type": "image/png",
                }
            }
        }
        manifest = build_files_manifest(ctx, exclude_vision_images=True)
        assert manifest is None


# ---------------------------------------------------------------------------
# _build_vision_user_message helper
# ---------------------------------------------------------------------------

class TestBuildVisionUserMessage:
    def test_produces_multimodal_content(self):
        msg = _build_vision_user_message(
            "What is in this image?",
            [{"image_b64": "abc", "image_mime_type": "image/jpeg"}],
        )
        assert msg["role"] == "user"
        content = msg["content"]
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "What is in this image?"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,abc"

    def test_multiple_images(self):
        msg = _build_vision_user_message(
            "Compare these.",
            [
                {"image_b64": "aaa", "image_mime_type": "image/png"},
                {"image_b64": "bbb", "image_mime_type": "image/png"},
            ],
        )
        assert len(msg["content"]) == 3  # text + 2 images


# ---------------------------------------------------------------------------
# MessageBuilder integration tests
# ---------------------------------------------------------------------------

class TestMessageBuilderVision:
    @pytest.mark.asyncio
    async def test_attaches_images_to_last_user_message(self):
        session = _make_session()
        session.history.add_message(Message(role=MessageRole.USER, content="Show me the image"))
        # Simulate vision image in context (as if handle_session_files stored it)
        session.context["files"] = {
            "photo.png": {
                "content_type": "image/png",
                "extract_mode": "none",
                "image_b64": "FAKEBASE64",
                "image_mime_type": "image/png",
            }
        }

        builder = MessageBuilder()
        messages = await builder.build_messages(
            session=session,
            include_system_prompt=False,
            model_supports_vision=True,
        )

        # Find the last user message
        user_msgs = [m for m in messages if m.get("role") == "user"]
        assert user_msgs, "No user message found"
        last_user = user_msgs[-1]

        # Content should be a list (multimodal)
        content = last_user["content"]
        assert isinstance(content, list)
        text_blocks = [b for b in content if b.get("type") == "text"]
        image_blocks = [b for b in content if b.get("type") == "image_url"]
        assert len(text_blocks) == 1
        assert text_blocks[0]["text"] == "Show me the image"
        assert len(image_blocks) == 1
        assert "data:image/png;base64,FAKEBASE64" in image_blocks[0]["image_url"]["url"]

    @pytest.mark.asyncio
    async def test_no_vision_leaves_message_as_string(self):
        session = _make_session()
        session.history.add_message(Message(role=MessageRole.USER, content="Hello"))
        session.context["files"] = {
            "photo.png": {
                "content_type": "image/png",
                "extract_mode": "none",
                "image_b64": "FAKEBASE64",
                "image_mime_type": "image/png",
            }
        }

        builder = MessageBuilder()
        messages = await builder.build_messages(
            session=session,
            include_system_prompt=False,
            model_supports_vision=False,
        )

        user_msgs = [m for m in messages if m.get("role") == "user"]
        assert user_msgs
        # Content must remain a string when vision is off
        assert isinstance(user_msgs[-1]["content"], str)

    @pytest.mark.asyncio
    async def test_vision_image_excluded_from_manifest(self):
        session = _make_session()
        session.history.add_message(Message(role=MessageRole.USER, content="Hello"))
        session.context["files"] = {
            "photo.png": {
                "content_type": "image/png",
                "extract_mode": "none",
                "image_b64": "FAKEBASE64",
                "image_mime_type": "image/png",
            }
        }

        builder = MessageBuilder()
        messages = await builder.build_messages(
            session=session,
            include_system_prompt=False,
            include_files_manifest=True,
            model_supports_vision=True,
        )

        # No system manifest message should mention photo.png
        system_msgs = [m for m in messages if m.get("role") == "system"]
        for sm in system_msgs:
            assert "photo.png" not in sm.get("content", ""), \
                "Vision image should not appear in text manifest"
