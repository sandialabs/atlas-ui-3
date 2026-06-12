"""Tests for native PDF document support in message building and file processing.

Verifies that:
- ModelConfig recognizes supports_pdf
- handle_session_files stores pdf_b64 for PDF-capable models only
- Native PDFs are excluded from the text manifest and skip text extraction
- MessageBuilder embeds PDF document content blocks on the last user message
- Size, page-count, and document-count limits are enforced
- Stale PDF data is cleared between turns
"""

import base64
import uuid
from io import BytesIO

import pytest

from atlas.application.chat.preprocessors.message_builder import (
    MessageBuilder,
    _build_multimodal_user_message,
)
from atlas.application.chat.utilities.file_processor import (
    _MAX_PDF_B64_BYTES,
    _MAX_PDF_DOCUMENTS_PER_REQUEST,
    _MAX_PDF_PAGES,
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
    return Session(id=uuid.uuid4(), user_email=user_email)


def _make_file_manager() -> FileManager:
    return FileManager(s3_client=MockS3StorageClient())


def _pdf_b64(pages: int = 1) -> str:
    """Build a minimal valid multi-page PDF as base64 via pypdf."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# ModelConfig
# ---------------------------------------------------------------------------

class TestModelConfigSupportsPdf:
    def test_default_is_false(self):
        cfg = ModelConfig(model_name="m", model_url="http://x")
        assert cfg.supports_pdf is False

    def test_can_be_set_true(self):
        cfg = ModelConfig(model_name="m", model_url="http://x", supports_pdf=True)
        assert cfg.supports_pdf is True

    def test_llm_config_roundtrip(self):
        llm_cfg = LLMConfig(models={
            "pdf-model": ModelConfig(model_name="m", model_url="http://x", supports_pdf=True),
            "text-model": ModelConfig(model_name="m", model_url="http://x"),
        })
        assert llm_cfg.models["pdf-model"].supports_pdf is True
        assert llm_cfg.models["text-model"].supports_pdf is False


# ---------------------------------------------------------------------------
# handle_session_files
# ---------------------------------------------------------------------------

class TestHandleSessionFilesPdf:
    @pytest.mark.asyncio
    async def test_stores_pdf_b64_for_pdf_model(self):
        fm = _make_file_manager()
        b64 = _pdf_b64()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"doc.pdf": {"content": b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_pdf=True,
        )
        ref = context["files"]["doc.pdf"]
        assert ref.get("pdf_b64") == b64
        assert ref.get("pdf_mime_type") == "application/pdf"

    @pytest.mark.asyncio
    async def test_no_pdf_b64_without_pdf_support(self):
        fm = _make_file_manager()
        b64 = _pdf_b64()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"doc.pdf": {"content": b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_pdf=False,
        )
        assert "pdf_b64" not in context["files"]["doc.pdf"]

    @pytest.mark.asyncio
    async def test_non_pdf_file_never_gets_pdf_b64(self):
        fm = _make_file_manager()
        txt_b64 = base64.b64encode(b"hello").decode()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"readme.txt": {"content": txt_b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_pdf=True,
        )
        assert "pdf_b64" not in context["files"]["readme.txt"]

    @pytest.mark.asyncio
    async def test_native_pdf_skips_text_extraction(self):
        fm = _make_file_manager()
        b64 = _pdf_b64()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"doc.pdf": {"content": b64, "extractMode": "full"}},
            file_manager=fm,
            model_supports_pdf=True,
        )
        ref = context["files"]["doc.pdf"]
        assert ref.get("pdf_b64") == b64
        # No extracted content because the model reads the PDF directly
        assert "extracted_content" not in ref


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

class TestPdfLimits:
    @pytest.mark.asyncio
    async def test_oversized_pdf_not_stored(self):
        fm = _make_file_manager()
        oversized = base64.b64encode(b"%PDF-" + b"x" * _MAX_PDF_B64_BYTES).decode()
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"huge.pdf": {"content": oversized, "extractMode": "none"}},
            file_manager=fm,
            model_supports_pdf=True,
        )
        assert "pdf_b64" not in context["files"]["huge.pdf"]

    @pytest.mark.asyncio
    async def test_over_page_limit_pdf_not_stored(self):
        fm = _make_file_manager()
        b64 = _pdf_b64(pages=_MAX_PDF_PAGES + 1)
        warnings: list = []

        async def update_callback(event):
            if event.get("type") == "warning":
                warnings.append(event["message"])

        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map={"long.pdf": {"content": b64, "extractMode": "none"}},
            file_manager=fm,
            model_supports_pdf=True,
            update_callback=update_callback,
        )
        assert "pdf_b64" not in context["files"]["long.pdf"]
        assert any("long.pdf" in w for w in warnings)

    @pytest.mark.asyncio
    async def test_excess_pdf_documents_demoted(self):
        fm = _make_file_manager()
        b64 = _pdf_b64()
        files_map = {
            f"doc_{i:02d}.pdf": {"content": b64, "extractMode": "none"}
            for i in range(_MAX_PDF_DOCUMENTS_PER_REQUEST + 2)
        }
        context = await handle_session_files(
            session_context={},
            user_email="u@example.com",
            files_map=files_map,
            file_manager=fm,
            model_supports_pdf=True,
        )
        pdf_count = sum(1 for r in context["files"].values() if r.get("pdf_b64"))
        assert pdf_count == _MAX_PDF_DOCUMENTS_PER_REQUEST


# ---------------------------------------------------------------------------
# build_files_manifest exclusion
# ---------------------------------------------------------------------------

class TestManifestExcludePdf:
    def _ctx(self):
        return {
            "files": {
                "doc.pdf": {
                    "content_type": "application/pdf",
                    "extract_mode": "none",
                    "pdf_b64": "abc",
                    "pdf_mime_type": "application/pdf",
                },
                "notes.txt": {"content_type": "text/plain", "extract_mode": "none"},
            }
        }

    def test_includes_pdf_when_not_excluding(self):
        manifest = build_files_manifest(self._ctx())
        assert "doc.pdf" in manifest["content"]

    def test_excludes_pdf_when_flag_set(self):
        manifest = build_files_manifest(self._ctx(), exclude_pdf_documents=True)
        assert "doc.pdf" not in manifest["content"]
        assert "notes.txt" in manifest["content"]


# ---------------------------------------------------------------------------
# _build_multimodal_user_message
# ---------------------------------------------------------------------------

class TestBuildMultimodalUserMessage:
    def test_pdf_block_before_text(self):
        msg = _build_multimodal_user_message(
            "Summarize this.",
            [],
            [{"pdf_b64": "abc", "pdf_mime_type": "application/pdf"}],
        )
        content = msg["content"]
        assert content[0]["type"] == "file"
        assert content[0]["file"]["file_data"] == "data:application/pdf;base64,abc"
        assert content[0]["file"]["format"] == "application/pdf"
        assert content[1] == {"type": "text", "text": "Summarize this."}

    def test_combined_pdf_image_and_text_order(self):
        msg = _build_multimodal_user_message(
            "Analyze both.",
            [{"image_b64": "img", "image_mime_type": "image/png"}],
            [{"pdf_b64": "pdf", "pdf_mime_type": "application/pdf"}],
        )
        types = [b["type"] for b in msg["content"]]
        # documents, then text, then images
        assert types == ["file", "text", "image_url"]


# ---------------------------------------------------------------------------
# MessageBuilder integration
# ---------------------------------------------------------------------------

class TestMessageBuilderPdf:
    @pytest.mark.asyncio
    async def test_attaches_pdf_to_last_user_message(self):
        session = _make_session()
        session.history.add_message(Message(role=MessageRole.USER, content="Summarize the PDF"))
        session.context["files"] = {
            "doc.pdf": {
                "content_type": "application/pdf",
                "extract_mode": "none",
                "pdf_b64": "FAKEPDF",
                "pdf_mime_type": "application/pdf",
            }
        }
        messages = await MessageBuilder().build_messages(
            session=session,
            include_system_prompt=False,
            model_supports_pdf=True,
        )
        last_user = [m for m in messages if m.get("role") == "user"][-1]
        content = last_user["content"]
        assert isinstance(content, list)
        file_blocks = [b for b in content if b.get("type") == "file"]
        assert len(file_blocks) == 1
        assert file_blocks[0]["file"]["file_data"] == "data:application/pdf;base64,FAKEPDF"

    @pytest.mark.asyncio
    async def test_no_pdf_support_leaves_message_as_string(self):
        session = _make_session()
        session.history.add_message(Message(role=MessageRole.USER, content="Hi"))
        session.context["files"] = {
            "doc.pdf": {
                "content_type": "application/pdf",
                "extract_mode": "none",
                "pdf_b64": "FAKEPDF",
                "pdf_mime_type": "application/pdf",
            }
        }
        messages = await MessageBuilder().build_messages(
            session=session,
            include_system_prompt=False,
            model_supports_pdf=False,
        )
        last_user = [m for m in messages if m.get("role") == "user"][-1]
        assert isinstance(last_user["content"], str)

    @pytest.mark.asyncio
    async def test_pdf_excluded_from_manifest(self):
        session = _make_session()
        session.history.add_message(Message(role=MessageRole.USER, content="Hi"))
        session.context["files"] = {
            "doc.pdf": {
                "content_type": "application/pdf",
                "extract_mode": "none",
                "pdf_b64": "FAKEPDF",
                "pdf_mime_type": "application/pdf",
            }
        }
        messages = await MessageBuilder().build_messages(
            session=session,
            include_system_prompt=False,
            include_files_manifest=True,
            model_supports_pdf=True,
        )
        for sm in [m for m in messages if m.get("role") == "system"]:
            assert "doc.pdf" not in sm.get("content", "")


# ---------------------------------------------------------------------------
# Stale cleanup
# ---------------------------------------------------------------------------

class TestStalePdfCleanup:
    @pytest.mark.asyncio
    async def test_prior_turn_pdf_cleared_even_without_new_files(self):
        prior = {
            "files": {
                "old.pdf": {
                    "key": "k",
                    "content_type": "application/pdf",
                    "source": "user",
                    "extract_mode": "none",
                    "pdf_b64": "OLD",
                    "pdf_mime_type": "application/pdf",
                }
            }
        }
        context = await handle_session_files(
            session_context=prior,
            user_email="u@example.com",
            files_map=None,
            file_manager=_make_file_manager(),
            model_supports_pdf=True,
        )
        old_ref = context["files"]["old.pdf"]
        assert "pdf_b64" not in old_ref
        assert "pdf_mime_type" not in old_ref
