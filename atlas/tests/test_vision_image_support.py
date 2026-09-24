"""Tests for vision image support in message building and file processing.

Verifies that:
- ModelConfig correctly recognizes supports_vision
- handle_session_files stores image_b64 for vision models
- build_files_manifest excludes vision images when exclude_vision_images=True
- MessageBuilder embeds image content blocks in the last user message
"""

import asyncio
import base64
import hashlib
import uuid
from unittest.mock import AsyncMock

import pytest

import atlas.application.chat.utilities.file_processor as file_processor
from atlas.application.chat.preprocessors.message_builder import (
    MessageBuilder,
    _build_multimodal_user_message,
)
from atlas.application.chat.utilities.file_processor import (
    _MAX_VISION_IMAGE_B64_BYTES,
    _MAX_VISION_IMAGES_PER_REQUEST,
    _VISION_IMAGE_MIME_TYPES,
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


def _tiff_b64(mode: str = "L") -> str:
    """Tiny TIFF encoded with Pillow for conversion tests."""
    Image = pytest.importorskip("PIL.Image")
    from io import BytesIO

    if mode == "I;16":
        image = Image.new("I;16", (2, 1))
        image.putdata([0, 65535])
    else:
        image = Image.new(mode, (1, 1), 255)
    output = BytesIO()
    image.save(output, format="TIFF")
    return base64.b64encode(output.getvalue()).decode()


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

    @pytest.mark.asyncio
    async def test_tiff_converted_to_png_for_vision_model(self):
        fm = _make_file_manager()
        tiff_b64 = _tiff_b64()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"scan.tiff": {"content": tiff_b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
        )
        file_ref = context["files"]["scan.tiff"]
        assert file_ref.get("content_type") == "image/tiff"
        assert file_ref.get("image_mime_type") == "image/png"
        assert base64.b64decode(file_ref["image_b64"]).startswith(b"\x89PNG\r\n\x1a\n")
        assert file_ref["image_b64"] != tiff_b64

    @pytest.mark.asyncio
    async def test_high_precision_tiff_converted_to_png_for_vision_model(self):
        fm = _make_file_manager()
        tiff_b64 = _tiff_b64("I;16")
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"scan.tif": {"content": tiff_b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
        )
        file_ref = context["files"]["scan.tif"]
        assert file_ref.get("content_type") == "image/tiff"
        assert file_ref.get("image_mime_type") == "image/png"
        assert base64.b64decode(file_ref["image_b64"]).startswith(b"\x89PNG\r\n\x1a\n")

    @pytest.mark.asyncio
    async def test_tiff_conversion_failure_warns_and_skips_vision(self):
        """A TIFF that cannot be decoded should warn the user and be dropped
        from the vision payload rather than failing silently."""
        fm = _make_file_manager()
        # Valid base64, but the bytes are not a decodable TIFF image.
        bad_tiff_b64 = base64.b64encode(b"II*\x00not-a-real-tiff").decode()
        warnings: list = []

        async def update_callback(event):
            if event.get("type") == "warning":
                warnings.append(event["message"])

        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"broken.tiff": {"content": bad_tiff_b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
            update_callback=update_callback,
        )
        file_ref = context["files"]["broken.tiff"]
        assert "image_b64" not in file_ref, "Unconvertible TIFF must not be sent as a vision image"
        assert "image_mime_type" not in file_ref
        assert any("broken.tiff" in w for w in warnings), "User should be warned about the failed conversion"


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
        msg = _build_multimodal_user_message(
            "What is in this image?",
            [{"image_b64": "abc", "image_mime_type": "image/jpeg"}],
            [],
        )
        assert msg["role"] == "user"
        content = msg["content"]
        assert isinstance(content, list)
        # With no PDFs, image-only layout is text first then images (unchanged)
        assert content[0] == {"type": "text", "text": "What is in this image?"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"] == "data:image/jpeg;base64,abc"

    def test_multiple_images(self):
        msg = _build_multimodal_user_message(
            "Compare these.",
            [
                {"image_b64": "aaa", "image_mime_type": "image/png"},
                {"image_b64": "bbb", "image_mime_type": "image/png"},
            ],
            [],
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


# ---------------------------------------------------------------------------
# SVG exclusion tests
# ---------------------------------------------------------------------------

class TestSVGExcludedFromVision:
    def test_svg_not_in_vision_mime_allowlist(self):
        assert "image/svg+xml" not in _VISION_IMAGE_MIME_TYPES

    @pytest.mark.asyncio
    async def test_svg_file_not_stored_as_vision_image(self):
        fm = _make_file_manager()
        svg_b64 = base64.b64encode(b"<svg></svg>").decode()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"icon.svg": {"content": svg_b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
        )
        file_ref = context["files"]["icon.svg"]
        assert "image_b64" not in file_ref
        assert "image_mime_type" not in file_ref


# ---------------------------------------------------------------------------
# Size limit tests
# ---------------------------------------------------------------------------

class TestVisionImageSizeLimit:
    @pytest.mark.asyncio
    async def test_oversized_image_not_stored_as_vision(self):
        fm = _make_file_manager()
        # Create a base64 string just over the limit
        oversized_b64 = base64.b64encode(b"x" * (_MAX_VISION_IMAGE_B64_BYTES + 1)).decode()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"huge.png": {"content": oversized_b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
        )
        file_ref = context["files"]["huge.png"]
        assert "image_b64" not in file_ref, "Oversized image should not be stored for vision"

    @pytest.mark.asyncio
    async def test_image_within_size_limit_stored(self):
        fm = _make_file_manager()
        b64 = _png_b64()
        assert len(b64) < _MAX_VISION_IMAGE_B64_BYTES
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"small.png": {"content": b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
        )
        file_ref = context["files"]["small.png"]
        assert file_ref.get("image_b64") == b64


# ---------------------------------------------------------------------------
# Count limit tests
# ---------------------------------------------------------------------------

class TestVisionImageCountLimit:
    @pytest.mark.asyncio
    async def test_excess_images_demoted(self):
        fm = _make_file_manager()
        b64 = _png_b64()
        files_map = {
            f"img_{i:02d}.png": {"content": b64, "extractMode": "none"}
            for i in range(_MAX_VISION_IMAGES_PER_REQUEST + 3)
        }
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map=files_map,
            file_manager=fm,
            model_supports_vision=True,
        )
        vision_count = sum(
            1 for ref in context["files"].values() if ref.get("image_b64")
        )
        assert vision_count == _MAX_VISION_IMAGES_PER_REQUEST


# ---------------------------------------------------------------------------
# Stale image cleanup tests
# ---------------------------------------------------------------------------

class TestStaleVisionImageCleanup:
    @pytest.mark.asyncio
    async def test_prior_turn_vision_images_cleared(self):
        fm = _make_file_manager()
        b64 = _png_b64()
        # Simulate context from a prior turn with a vision image
        prior_context = {
            "files": {
                "old_photo.png": {
                    "key": "some-key",
                    "content_type": "image/png",
                    "size": 100,
                    "source": "user",
                    "extract_mode": "none",
                    "image_b64": "OLD_DATA",
                    "image_mime_type": "image/png",
                }
            }
        }
        # Process new files — the old vision data should be cleared
        context = await handle_session_files(
            session_context=prior_context,
            user_email="u@example.com",
            files_map={"new.png": {"content": b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
        )
        old_ref = context["files"]["old_photo.png"]
        assert "image_b64" not in old_ref, "Stale vision image data should be cleared"
        assert "image_mime_type" not in old_ref
        # New image should have vision data
        new_ref = context["files"]["new.png"]
        assert new_ref.get("image_b64") == b64

    @pytest.mark.asyncio
    async def test_prior_turn_image_is_rehydrated_without_new_files(self):
        fm = _make_file_manager()
        b64 = _png_b64()
        prior_context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"old_photo.png": {"content": b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
        )
        prior_ref = prior_context["files"]["old_photo.png"]
        prior_ref["image_b64"] = "STALE_DATA"

        context = await handle_session_files(
            session_context=prior_context,
            user_email="u@example.com",
            files_map=None,
            file_manager=fm,
            model_supports_vision=True,
        )

        restored_ref = context["files"]["old_photo.png"]
        assert restored_ref["image_b64"] == b64
        assert restored_ref["image_mime_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_rehydrated_image_is_attached_to_follow_up_message(self):
        fm = _make_file_manager()
        b64 = _png_b64()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"photo.png": {"content": b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
        )
        context = await handle_session_files(
            session_context=context,
            user_email="u@example.com",
            files_map=None,
            file_manager=fm,
            model_supports_vision=True,
        )

        session = _make_session()
        session.context = context
        session.history.add_message(Message(role=MessageRole.USER, content="What is in it?"))
        messages = await MessageBuilder().build_messages(
            session=session,
            include_system_prompt=False,
            model_supports_vision=True,
        )

        user_message = [message for message in messages if message.get("role") == "user"][-1]
        assert any(block.get("type") == "image_url" for block in user_message["content"])

    @pytest.mark.asyncio
    async def test_unreadable_stored_image_does_not_abort_rehydration(self):
        b64 = _png_b64()
        context = {
            "files": {
                "unreadable.png": {
                    "key": "bad",
                    "content_type": "image/png",
                    "source": "user",
                },
                "readable.png": {
                    "key": "good",
                    "content_type": "image/png",
                    "source": "user",
                },
            }
        }
        fm = _make_file_manager()

        async def get_file_content(**kwargs):
            if kwargs["s3_key"] == "bad":
                raise RuntimeError("missing")
            return b64

        fm.get_file_content = AsyncMock(side_effect=get_file_content)

        result = await handle_session_files(
            session_context=context,
            user_email="u@example.com",
            files_map=None,
            file_manager=fm,
            model_supports_vision=True,
        )

        assert "image_b64" not in result["files"]["unreadable.png"]
        assert result["files"]["readable.png"]["image_b64"] == b64

    @pytest.mark.asyncio
    async def test_rehydration_keeps_newest_images_at_limit(self):
        b64 = _png_b64()
        context = {
            "files": {
                f"image_{index}.png": {
                    "key": f"key-{index}",
                    "content_type": "image/png",
                    "source": "user",
                }
                for index in range(_MAX_VISION_IMAGES_PER_REQUEST + 1)
            }
        }
        fm = _make_file_manager()
        fm.get_file_content = AsyncMock(return_value=b64)

        result = await handle_session_files(
            session_context=context,
            user_email="u@example.com",
            files_map=None,
            file_manager=fm,
            model_supports_vision=True,
        )

        assert "image_b64" not in result["files"]["image_0.png"]
        assert all(
            result["files"][f"image_{index}.png"].get("image_b64") == b64
            for index in range(1, _MAX_VISION_IMAGES_PER_REQUEST + 1)
        )


# ---------------------------------------------------------------------------
# Rehydration budget, ordering, and stale-payload tests
# ---------------------------------------------------------------------------

def _valid_image_ref(b64: str, key: str) -> dict:
    """A session ref whose inline payload is consistent with its stored object."""
    return {
        "key": key,
        "content_type": "image/png",
        "source": "user",
        "image_b64": b64,
        "image_mime_type": "image/png",
        "image_source_key": key,
        "image_content_hash": hashlib.sha256(b64.encode()).hexdigest(),
    }


class TestNoUploadBudgetAndOrdering:
    """The no-upload (follow-up) path: caps, ordering, and warnings."""

    @pytest.mark.asyncio
    async def test_byte_budget_demotes_newest_first_and_warns(self, monkeypatch):
        """The aggregate budget keeps the newest images and names the demoted."""
        monkeypatch.setattr(file_processor, "_MAX_TOTAL_INLINE_B64_BYTES", 1000)
        context = {
            "files": {
                "old_big.png": _valid_image_ref("X" * 900, "key-old"),
                "mid.png": _valid_image_ref("Y" * 300, "key-mid"),
                "new_small.png": _valid_image_ref("Z" * 100, "key-new"),
            }
        }
        fm = _make_file_manager()
        warnings_seen: list = []

        async def update_callback(event):
            if event.get("type") == "warning":
                warnings_seen.append(event["message"])

        result = await handle_session_files(
            session_context=context,
            user_email="u@example.com",
            files_map=None,
            file_manager=fm,
            model_supports_vision=True,
            update_callback=update_callback,
        )

        kept = [name for name, ref in result["files"].items() if ref.get("image_b64")]
        # Newest-first priority: the newest two fit the budget, the oldest is
        # demoted -- not the reverse.
        assert kept == ["mid.png", "new_small.png"]
        assert any("old_big.png" in message for message in warnings_seen), (
            "The warning must name the demoted file"
        )

    @pytest.mark.asyncio
    async def test_count_cap_keeps_newest_retained_images(self):
        b64 = _png_b64()
        context = {
            "files": {
                f"image_{index:02d}.png": _valid_image_ref(b64, f"key-{index}")
                for index in range(_MAX_VISION_IMAGES_PER_REQUEST + 2)
            }
        }
        fm = _make_file_manager()
        warnings_seen: list = []

        async def update_callback(event):
            if event.get("type") == "warning":
                warnings_seen.append(event["message"])

        result = await handle_session_files(
            session_context=context,
            user_email="u@example.com",
            files_map=None,
            file_manager=fm,
            model_supports_vision=True,
            update_callback=update_callback,
        )

        kept = [name for name, ref in result["files"].items() if ref.get("image_b64")]
        assert len(kept) == _MAX_VISION_IMAGES_PER_REQUEST
        # The two oldest images are demoted; the newest are kept.
        assert "image_00.png" not in kept
        assert "image_01.png" not in kept
        assert f"image_{_MAX_VISION_IMAGES_PER_REQUEST + 1:02d}.png" in kept
        assert any("image_00.png" in message for message in warnings_seen)

    @pytest.mark.asyncio
    async def test_stale_payload_is_a_rehydration_candidate_not_retained(self):
        """A payload that no longer matches its stored object must not count
        as kept *and* as its replacement -- it is rehydrated (or dropped),
        never double-counted against the slot budget."""
        b64 = _png_b64()
        stale = {
            "key": "key-stale",
            "content_type": "image/png",
            "source": "user",
            "image_b64": "STALEPAYLOAD",
            "image_mime_type": "image/png",
            "image_source_key": "key-stale",
            "image_content_hash": "no-longer-matching",
        }
        context = {
            "files": {
                "good.png": _valid_image_ref("VALIDPAYLOAD", "key-good"),
                "stale.png": stale,
            }
        }
        fm = _make_file_manager()
        fm.get_file_content = AsyncMock(return_value=b64)

        result = await handle_session_files(
            session_context=context,
            user_email="u@example.com",
            files_map=None,
            file_manager=fm,
            model_supports_vision=True,
        )

        # The stale payload was replaced from storage, not preserved.
        assert result["files"]["stale.png"]["image_b64"] == b64
        assert fm.get_file_content.await_count == 1
        assert result["files"]["good.png"]["image_b64"] == "VALIDPAYLOAD"


class TestRehydrationDecodingGuards:
    """Decode bounds for stored-image rehydration."""

    @pytest.mark.asyncio
    async def test_oversized_raster_rejected_not_decoded(self, monkeypatch):
        """A TIFF whose declared size exceeds the pixel cap fails the decode
        and warns instead of being decoded into memory."""
        monkeypatch.setattr(file_processor, "_VISION_IMAGE_PIXEL_LIMIT", 1)
        fm = _make_file_manager()
        # 2x1 pixels exceeds the patched 1-pixel cap.
        oversized_tiff = _tiff_b64("I;16")
        warnings_seen: list = []

        async def update_callback(event):
            if event.get("type") == "warning":
                warnings_seen.append(event["message"])

        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"bomb.tiff": {"content": oversized_tiff, "extractMode": "none"}},
            file_manager=fm,
            model_supports_vision=True,
            update_callback=update_callback,
        )

        file_ref = context["files"]["bomb.tiff"]
        assert "image_b64" not in file_ref
        assert any("bomb.tiff" in message for message in warnings_seen)

    @pytest.mark.asyncio
    async def test_rehydration_fanout_is_bounded(self):
        """The rehydration gather must not decode every stored image at once."""
        b64 = _png_b64()
        context = {
            "files": {
                f"image_{index}.png": {
                    "key": f"key-{index}",
                    "content_type": "image/png",
                    "source": "user",
                }
                for index in range(_MAX_VISION_IMAGES_PER_REQUEST)
            }
        }
        fm = _make_file_manager()
        in_flight = 0
        max_in_flight = 0

        async def get_file_content(**kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return b64

        fm.get_file_content = AsyncMock(side_effect=get_file_content)

        result = await handle_session_files(
            session_context=context,
            user_email="u@example.com",
            files_map=None,
            file_manager=fm,
            model_supports_vision=True,
        )

        assert max_in_flight <= file_processor._REHYDRATE_DECODE_CONCURRENCY
        assert all(
            result["files"][f"image_{index}.png"].get("image_b64") == b64
            for index in range(_MAX_VISION_IMAGES_PER_REQUEST)
        )
