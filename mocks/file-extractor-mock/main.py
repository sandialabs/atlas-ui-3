#!/usr/bin/env python3
"""
Mock File Extractor Service - Testing Support

Provides mock HTTP endpoints for file content extraction services.
All endpoints are on the same host/port but at different paths:
  - /extract  - PDF text extraction (actually extracts text using pypdf)
  - /analyze  - Image vision analysis (returns generic description)
  - /ocr      - OCR text extraction (returns generic OCR result)

Follows the contract defined in the file-content-extraction-plan.
"""

import base64
import io
import logging
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
    # Remove newlines, carriage returns, and other control characters
    sanitized = "".join(c if c.isprintable() and c not in "\n\r\t" else "_" for c in filename)
    # Limit length to prevent log flooding
    if len(sanitized) > 255:
        sanitized = sanitized[:252] + "..."
    return sanitized


app = FastAPI(
    title="Mock File Extractor Service",
    description="Mock file extraction service for testing ATLAS file content extraction",
)


# ---------------------------------------------------------------------------
# Request/Response Models
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
    width: Optional[int] = None
    height: Optional[int] = None
    format: Optional[str] = None


class ExtractionResponse(BaseModel):
    """Standard extraction response format."""
    success: bool
    text: Optional[str] = None
    description: Optional[str] = None  # For image-vision responses
    error: Optional[str] = None
    metadata: Optional[ExtractionMetadata] = None


# ---------------------------------------------------------------------------
# PDF Extraction (Real extraction using pypdf)
# ---------------------------------------------------------------------------

def extract_pdf_text(pdf_bytes: bytes) -> tuple[str, dict]:
    """
    Extract text from PDF bytes using pypdf.

    Returns:
        Tuple of (extracted_text, metadata_dict)
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        # Fall back to PyPDF2 if pypdf not available
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            raise ImportError(
                "PDF extraction requires 'pypdf' or 'PyPDF2'. "
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


@app.post("/extract", response_model=ExtractionResponse)
async def extract_pdf(request: ExtractionRequest):
    """
    Extract text content from a PDF file.

    This endpoint actually extracts text from the provided PDF using pypdf.
    """
    try:
        # Decode base64 content
        try:
            pdf_bytes = base64.b64decode(request.content)
        except Exception as e:
            return ExtractionResponse(
                success=False,
                error=f"Invalid base64 content: {str(e)}"
            )

        # Extract text
        try:
            text, metadata_dict = extract_pdf_text(pdf_bytes)
        except ImportError as e:
            return ExtractionResponse(
                success=False,
                error=str(e)
            )
        except Exception as e:
            logger.exception(f"PDF extraction failed for {sanitize_filename_for_log(request.filename)}")
            return ExtractionResponse(
                success=False,
                error=f"PDF extraction failed: {str(e)}"
            )

        # Apply preview truncation if requested
        preview_chars = 2000
        if request.options and request.options.preview_chars:
            preview_chars = request.options.preview_chars

        truncated = len(text) > preview_chars
        if truncated:
            # We return full text, but note truncation in metadata
            pass

        return ExtractionResponse(
            success=True,
            text=text,
            metadata=ExtractionMetadata(
                pages=metadata_dict.get("pages"),
                char_count=metadata_dict.get("char_count"),
                truncated=truncated,
            )
        )

    except Exception as e:
        logger.exception(f"Unexpected error processing {sanitize_filename_for_log(request.filename)}")
        return ExtractionResponse(
            success=False,
            error=f"Unexpected error: {str(e)}"
        )


# ---------------------------------------------------------------------------
# Image Vision Analysis (Generic mock response)
# ---------------------------------------------------------------------------

IMAGE_ANALYSIS_RESPONSES = {
    "default": (
        "This image appears to contain visual content. The image has been "
        "analyzed by the mock vision service. In a production environment, "
        "this would be processed by a vision-capable AI model (like GPT-4V "
        "or Claude Vision) to provide detailed descriptions of the image "
        "contents including objects, text, people, scenes, and other visual elements."
    ),
    "chart": (
        "This appears to be a chart or graph visualization. It contains data "
        "presented in a visual format, likely showing trends, comparisons, or "
        "distributions. The specific data values and labels would be analyzed "
        "by a vision model in production."
    ),
    "screenshot": (
        "This appears to be a screenshot of a user interface. It shows various "
        "UI elements including buttons, text fields, and navigation components. "
        "The specific application and content would be identified by a vision "
        "model in production."
    ),
    "document": (
        "This image appears to contain a scanned document or photograph of text. "
        "The text content would typically be extracted using OCR capabilities. "
        "For accurate text extraction, consider using the /ocr endpoint instead."
    ),
}


def get_image_description(filename: str, image_bytes: bytes) -> tuple[str, dict]:
    """
    Generate a mock image description based on filename hints.

    In production, this would call a vision API.
    """
    filename_lower = filename.lower()

    # Try to infer image type from filename
    if any(word in filename_lower for word in ["chart", "graph", "plot"]):
        description = IMAGE_ANALYSIS_RESPONSES["chart"]
    elif any(word in filename_lower for word in ["screenshot", "screen", "ui"]):
        description = IMAGE_ANALYSIS_RESPONSES["screenshot"]
    elif any(word in filename_lower for word in ["doc", "scan", "page"]):
        description = IMAGE_ANALYSIS_RESPONSES["document"]
    else:
        description = IMAGE_ANALYSIS_RESPONSES["default"]

    # Try to get image dimensions
    metadata = {}
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(image_bytes))
        metadata["width"] = img.width
        metadata["height"] = img.height
        metadata["format"] = img.format
    except ImportError:
        # PIL not available, skip dimension extraction
        pass
    except Exception:
        # Image parsing failed, skip
        pass

    return description, metadata


@app.post("/analyze", response_model=ExtractionResponse)
async def analyze_image(request: ExtractionRequest):
    """
    Analyze an image and return a description.

    This is a mock endpoint that returns generic descriptions.
    In production, this would call a vision API (GPT-4V, Claude Vision, etc.).
    """
    try:
        # Decode base64 content
        try:
            image_bytes = base64.b64decode(request.content)
        except Exception as e:
            return ExtractionResponse(
                success=False,
                error=f"Invalid base64 content: {str(e)}"
            )

        # Generate mock description
        description, metadata_dict = get_image_description(request.filename, image_bytes)

        return ExtractionResponse(
            success=True,
            text=description,  # Also provide as text for compatibility
            description=description,
            metadata=ExtractionMetadata(
                width=metadata_dict.get("width"),
                height=metadata_dict.get("height"),
                format=metadata_dict.get("format"),
                char_count=len(description),
            )
        )

    except Exception as e:
        logger.exception(f"Unexpected error analyzing {sanitize_filename_for_log(request.filename)}")
        return ExtractionResponse(
            success=False,
            error=f"Unexpected error: {str(e)}"
        )


# ---------------------------------------------------------------------------
# OCR Endpoint (Generic mock response)
# ---------------------------------------------------------------------------

OCR_MOCK_RESPONSES = {
    "default": (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do "
        "eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim "
        "ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut "
        "aliquip ex ea commodo consequat.\n\n"
        "This is mock OCR output. In production, this would contain the actual "
        "text extracted from the image using Tesseract, Google Cloud Vision, "
        "or another OCR service."
    ),
    "receipt": (
        "RECEIPT\n"
        "================\n"
        "Store Name: Mock Store\n"
        "Date: 2026-01-18\n"
        "----------------\n"
        "Item 1          $10.00\n"
        "Item 2          $25.00\n"
        "Item 3           $5.00\n"
        "----------------\n"
        "Subtotal        $40.00\n"
        "Tax              $3.20\n"
        "TOTAL           $43.20\n"
        "================\n"
        "Thank you for shopping!"
    ),
    "form": (
        "FORM TITLE\n"
        "==========\n\n"
        "Name: _______________\n"
        "Date: _______________\n"
        "Address: _______________\n\n"
        "Section 1:\n"
        "[ ] Option A\n"
        "[X] Option B\n"
        "[ ] Option C\n\n"
        "Comments: This is mock OCR text representing a scanned form."
    ),
}


def perform_mock_ocr(filename: str, image_bytes: bytes) -> tuple[str, dict]:
    """
    Generate mock OCR output based on filename hints.

    In production, this would call an OCR service.
    """
    filename_lower = filename.lower()

    # Try to infer document type from filename
    if any(word in filename_lower for word in ["receipt", "invoice", "bill"]):
        text = OCR_MOCK_RESPONSES["receipt"]
    elif any(word in filename_lower for word in ["form", "application", "survey"]):
        text = OCR_MOCK_RESPONSES["form"]
    else:
        text = OCR_MOCK_RESPONSES["default"]

    metadata = {
        "char_count": len(text),
    }

    return text, metadata


@app.post("/ocr", response_model=ExtractionResponse)
async def ocr_extract(request: ExtractionRequest):
    """
    Extract text from an image using OCR.

    This is a mock endpoint that returns generic OCR results.
    In production, this would use Tesseract, Google Cloud Vision, etc.
    """
    try:
        # Decode base64 content
        try:
            image_bytes = base64.b64decode(request.content)
        except Exception as e:
            return ExtractionResponse(
                success=False,
                error=f"Invalid base64 content: {str(e)}"
            )

        # Generate mock OCR text
        text, metadata_dict = perform_mock_ocr(request.filename, image_bytes)

        return ExtractionResponse(
            success=True,
            text=text,
            metadata=ExtractionMetadata(
                char_count=metadata_dict.get("char_count"),
            )
        )

    except Exception as e:
        logger.exception(f"Unexpected error OCR processing {sanitize_filename_for_log(request.filename)}")
        return ExtractionResponse(
            success=False,
            error=f"Unexpected error: {str(e)}"
        )


# ---------------------------------------------------------------------------
# Utility Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get("/")
async def root():
    """Root endpoint with service info."""
    return {
        "service": "Mock File Extractor Service",
        "version": "1.0.0",
        "description": "Mock file extraction service for testing ATLAS",
        "endpoints": {
            "/extract": "POST - Extract text from PDF files (real extraction)",
            "/analyze": "POST - Analyze images (mock vision response)",
            "/ocr": "POST - OCR text extraction (mock response)",
            "/health": "GET - Health check",
        },
        "request_format": {
            "content": "base64-encoded file content",
            "filename": "original filename",
            "options": {
                "preview_chars": "optional, default 2000",
                "extract_images": "optional, default false",
            }
        },
        "response_format": {
            "success": "boolean",
            "text": "extracted text content",
            "description": "for image analysis only",
            "error": "error message if failed",
            "metadata": "extraction metadata",
        }
    }


if __name__ == "__main__":
    print("Starting Mock File Extractor Service...")
    print("Available endpoints:")
    print("  - POST /extract  - PDF text extraction (real extraction with pypdf)")
    print("  - POST /analyze  - Image vision analysis (mock response)")
    print("  - POST /ocr      - OCR text extraction (mock response)")
    print("  - GET  /health   - Health check")
    print()
    print("Default port: 8010")
    print()

    uvicorn.run(app, host="127.0.0.1", port=8010)
