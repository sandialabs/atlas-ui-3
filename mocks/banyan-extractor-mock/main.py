#!/usr/bin/env python3
"""
Banyan Extractor Mock Service

Provides HTTP endpoints for PDF and PPTX content extraction using
banyan-ingest (NemoparseProcessor for PDFs, PptxProcessor for PowerPoint).
Falls back to pypdf for PDF extraction if banyan-ingest is not installed.

Endpoints:
  - POST /extract       - PDF text extraction via banyan-ingest or pypdf
  - POST /extract-pptx  - PPTX text extraction via banyan-ingest
  - GET  /health        - Health check
"""

import base64
import io
import json
import logging
import os
import tempfile
from datetime import datetime
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn

logger = logging.getLogger(__name__)


def sanitize_filename_for_log(filename: str) -> str:
    """Sanitize filename to prevent log injection attacks."""
    if not filename:
        return "<empty>"
    sanitized = "".join(
        c if c.isprintable() and c not in "\n\r\t" else "_" for c in filename
    )
    if len(sanitized) > 255:
        sanitized = sanitized[:252] + "..."
    return sanitized


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ENDPOINT_CONFIG = {
    "url": "https://NEMOTRON_ENDPOINT_URL/v1",
    "model_name": "nvidia/nemotron-parse",
}


def load_endpoint_config() -> dict:
    """Load Nemotron endpoint config from endpoint_config.json if present."""
    config_path = os.path.join(os.path.dirname(__file__), "endpoint_config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            return json.load(f)
    return ENDPOINT_CONFIG


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Banyan Extractor Mock Service",
    description=(
        "File extraction service using banyan-ingest for PDF and PPTX "
        "content extraction via Nemotron Parse."
    ),
)


# ---------------------------------------------------------------------------
# Request / Response Models
# ---------------------------------------------------------------------------

class ExtractionOptions(BaseModel):
    """Options for extraction requests."""
    preview_chars: Optional[int] = 2000
    extract_images: Optional[bool] = False


class ExtractionRequest(BaseModel):
    """Standard extraction request format."""
    content: str  # base64-encoded file content
    filename: str
    options: Optional[ExtractionOptions] = None


class ExtractionMetadata(BaseModel):
    """Metadata about the extraction result."""
    pages: Optional[int] = None
    char_count: Optional[int] = None
    truncated: Optional[bool] = None


class ExtractionResponse(BaseModel):
    """Standard extraction response format."""
    success: bool
    text: Optional[str] = None
    error: Optional[str] = None
    metadata: Optional[ExtractionMetadata] = None


# ---------------------------------------------------------------------------
# PDF Extraction
# ---------------------------------------------------------------------------

def extract_pdf_with_banyan(pdf_bytes: bytes, config: dict) -> tuple[str, dict]:
    """
    Extract text from PDF bytes using banyan-ingest NemoparseProcessor.

    Writes bytes to a temp file because process_document() expects a filepath.

    Returns:
        Tuple of (extracted_text, metadata_dict)
    """
    from banyan_ingest import NemoparseProcessor

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        processor = NemoparseProcessor(
            endpoint_url=config["url"],
            model_name=config["model_name"],
        )
        result = processor.process_document(tmp_path)
        full_text = result.get_output_as_markdown()
    finally:
        os.unlink(tmp_path)

    metadata = {
        "char_count": len(full_text),
    }
    return full_text, metadata


def extract_pdf_with_pypdf(pdf_bytes: bytes) -> tuple[str, dict]:
    """
    Fallback PDF extraction using pypdf.

    Returns:
        Tuple of (extracted_text, metadata_dict)
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            raise ImportError(
                "PDF extraction requires 'banyan-ingest' or 'pypdf'. "
                "Install with: pip install pypdf"
            )

    pdf_file = io.BytesIO(pdf_bytes)
    reader = PdfReader(pdf_file)

    text_parts = []
    for page_num, page in enumerate(reader.pages):
        page_text = page.extract_text()
        if page_text:
            text_parts.append(f"--- Page {page_num + 1} ---\n{page_text}")

    full_text = "\n\n".join(text_parts)
    metadata = {
        "pages": len(reader.pages),
        "char_count": len(full_text),
    }
    return full_text, metadata


def extract_pdf_text(pdf_bytes: bytes, config: dict) -> tuple[str, dict]:
    """
    Extract text from PDF, trying banyan-ingest first, falling back to pypdf.
    """
    try:
        logger.info("Attempting PDF extraction with banyan-ingest NemoparseProcessor")
        return extract_pdf_with_banyan(pdf_bytes, config)
    except ImportError:
        logger.info("banyan-ingest not available, falling back to pypdf")
        return extract_pdf_with_pypdf(pdf_bytes)
    except Exception as e:
        logger.warning(
            "banyan-ingest extraction failed (%s), falling back to pypdf", e
        )
        return extract_pdf_with_pypdf(pdf_bytes)


# ---------------------------------------------------------------------------
# PPTX Extraction
# ---------------------------------------------------------------------------

def extract_pptx_text(pptx_bytes: bytes) -> tuple[str, dict]:
    """
    Extract text from PPTX bytes using banyan-ingest PptxProcessor.

    Writes bytes to a temp file because PptxProcessor expects a filepath.

    Returns:
        Tuple of (extracted_text, metadata_dict)
    """
    from banyan_ingest import PptxProcessor

    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as tmp:
        tmp.write(pptx_bytes)
        tmp_path = tmp.name

    try:
        processor = PptxProcessor()
        result = processor.process_document(tmp_path)
        full_text = result.get_output_as_markdown()
    finally:
        os.unlink(tmp_path)

    metadata = {
        "char_count": len(full_text),
    }
    return full_text, metadata


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/extract", response_model=ExtractionResponse)
async def extract_pdf(request: ExtractionRequest):
    """Extract text content from a PDF file."""
    try:
        try:
            pdf_bytes = base64.b64decode(request.content)
        except Exception as e:
            return ExtractionResponse(
                success=False,
                error=f"Invalid base64 content: {str(e)}",
            )

        config = load_endpoint_config()

        try:
            text, metadata_dict = extract_pdf_text(pdf_bytes, config)
        except ImportError as e:
            return ExtractionResponse(success=False, error=str(e))
        except Exception as e:
            safe_name = sanitize_filename_for_log(request.filename)
            logger.exception("PDF extraction failed for %s", safe_name)
            return ExtractionResponse(
                success=False,
                error=f"PDF extraction failed: {str(e)}",
            )

        preview_chars = 2000
        if request.options and request.options.preview_chars:
            preview_chars = request.options.preview_chars
        truncated = len(text) > preview_chars

        return ExtractionResponse(
            success=True,
            text=text,
            metadata=ExtractionMetadata(
                pages=metadata_dict.get("pages"),
                char_count=metadata_dict.get("char_count"),
                truncated=truncated,
            ),
        )

    except Exception as e:
        safe_name = sanitize_filename_for_log(request.filename)
        logger.exception("Unexpected error processing %s", safe_name)
        return ExtractionResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
        )


@app.post("/extract-pptx", response_model=ExtractionResponse)
async def extract_pptx(request: ExtractionRequest):
    """Extract text content from a PPTX (PowerPoint) file."""
    try:
        try:
            pptx_bytes = base64.b64decode(request.content)
        except Exception as e:
            return ExtractionResponse(
                success=False,
                error=f"Invalid base64 content: {str(e)}",
            )

        try:
            text, metadata_dict = extract_pptx_text(pptx_bytes)
        except ImportError as e:
            return ExtractionResponse(
                success=False,
                error=(
                    "PPTX extraction requires banyan-ingest. "
                    f"Install with: pip install banyan-ingest. Detail: {e}"
                ),
            )
        except Exception as e:
            safe_name = sanitize_filename_for_log(request.filename)
            logger.exception("PPTX extraction failed for %s", safe_name)
            return ExtractionResponse(
                success=False,
                error=f"PPTX extraction failed: {str(e)}",
            )

        preview_chars = 2000
        if request.options and request.options.preview_chars:
            preview_chars = request.options.preview_chars
        truncated = len(text) > preview_chars

        return ExtractionResponse(
            success=True,
            text=text,
            metadata=ExtractionMetadata(
                char_count=metadata_dict.get("char_count"),
                truncated=truncated,
            ),
        )

    except Exception as e:
        safe_name = sanitize_filename_for_log(request.filename)
        logger.exception("Unexpected error processing %s", safe_name)
        return ExtractionResponse(
            success=False,
            error=f"Unexpected error: {str(e)}",
        )


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    banyan_available = False
    try:
        import banyan_ingest  # noqa: F401
        banyan_available = True
    except ImportError:
        pass

    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "banyan_ingest_available": banyan_available,
    }


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Banyan Extractor Mock Service",
        "version": "1.0.0",
        "description": (
            "File extraction using banyan-ingest (Nemotron Parse for PDF, "
            "PptxProcessor for PPTX). Falls back to pypdf for PDF."
        ),
        "endpoints": {
            "/extract": "POST - PDF text extraction (banyan-ingest or pypdf fallback)",
            "/extract-pptx": "POST - PPTX text extraction (banyan-ingest required)",
            "/health": "GET - Health check",
        },
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Starting Banyan Extractor Mock Service...")
    print("Available endpoints:")
    print("  - POST /extract       - PDF extraction (banyan-ingest / pypdf fallback)")
    print("  - POST /extract-pptx  - PPTX extraction (banyan-ingest required)")
    print("  - GET  /health        - Health check")
    print()
    print("Default port: 8011")
    print()

    # Load endpoint config at startup
    ENDPOINT_CONFIG.update(load_endpoint_config())
    print(f"Nemotron endpoint: {ENDPOINT_CONFIG.get('url', 'not configured')}")
    print()

    uvicorn.run(app, host="127.0.0.1", port=8011)
