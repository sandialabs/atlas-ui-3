#!/usr/bin/env python3
"""
Mock Multipart File Extractor Service

A lightweight mock that accepts file uploads via multipart/form-data and
returns extracted text. Mimics an NLP extraction endpoint such as:
    curl -F 'file=@document.pdf' http://localhost:8011/extract

Supports PDF text extraction (via pypdf if installed) and plain text files.
For any other file type, returns a placeholder description of the upload.

Usage:
    python multipart_extractor_mock.py                   # port 8011
    python multipart_extractor_mock.py --port 9000       # custom port
    python multipart_extractor_mock.py --field document   # custom form field name
"""

import argparse
import io
import logging
from datetime import datetime, timezone
from typing import Optional

import uvicorn
from fastapi import FastAPI, File, UploadFile
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Mock Multipart File Extractor",
    description="Accepts file uploads via multipart/form-data and returns extracted text",
)

# ---------------------------------------------------------------------------
# Response model
# ---------------------------------------------------------------------------

class ExtractionResponse(BaseModel):
    """Response matching the NLP extraction service contract."""
    id: Optional[str] = None
    text: str
    filepath: Optional[str] = None
    extract_timestamp: Optional[str] = None
    extract_user: Optional[str] = None
    metadata: dict = {}
    sections: list = []


# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def extract_text_from_pdf(file_bytes: bytes) -> tuple[str, dict]:
    """Extract text from PDF bytes. Returns (text, metadata)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        try:
            from PyPDF2 import PdfReader
        except ImportError:
            return (
                "[PDF text extraction unavailable - install pypdf: "
                "pip install pypdf]",
                {"error": "pypdf not installed"},
            )

    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for i, page in enumerate(reader.pages):
        page_text = page.extract_text()
        if page_text:
            pages.append(f"--- Page {i + 1} ---\n{page_text}")

    text = "\n\n".join(pages) if pages else "[No extractable text found in PDF]"
    return text, {"pages": len(reader.pages), "char_count": len(text)}


def extract_text_from_bytes(filename: str, file_bytes: bytes) -> tuple[str, dict]:
    """
    Route extraction based on file extension.
    Returns (extracted_text, metadata).
    """
    lower = filename.lower()

    if lower.endswith(".pdf"):
        return extract_text_from_pdf(file_bytes)

    if lower.endswith((".txt", ".md", ".csv", ".json", ".xml", ".html", ".log")):
        # Treat as plain text
        try:
            text = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = file_bytes.decode("latin-1")
        return text, {"char_count": len(text)}

    # Fallback: describe what we received
    return (
        f"[Received file '{filename}' ({len(file_bytes)} bytes). "
        f"This mock does not have a real extractor for this file type. "
        f"In production, the extraction service would return the file's text content.]"
    ), {"file_size_bytes": len(file_bytes)}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/extract", response_model=ExtractionResponse)
async def extract_file(file: UploadFile = File(...)):
    """
    Extract text from an uploaded file (multipart/form-data).

    Equivalent to:
        curl -F 'file=@document.pdf' http://localhost:8011/extract
    """
    file_bytes = await file.read()
    filename = file.filename or "unknown"

    logger.info(
        "Received multipart upload: %s (%d bytes, content_type=%s)",
        filename, len(file_bytes), file.content_type,
    )

    text, metadata = extract_text_from_bytes(filename, file_bytes)

    return ExtractionResponse(
        id=None,
        text=text,
        filepath=filename,
        extract_timestamp=datetime.now(timezone.utc).isoformat(),
        extract_user="mock-service",
        metadata=metadata,
        sections=[],
    )


@app.get("/health")
async def health():
    """Health check."""
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/")
async def root():
    """Service info."""
    return {
        "service": "Mock Multipart File Extractor",
        "version": "1.0.0",
        "endpoints": {
            "POST /extract": "Upload a file via multipart/form-data, returns extracted text",
            "GET /health": "Health check",
        },
        "usage": "curl -F 'file=@document.pdf' http://localhost:8011/extract",
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Mock multipart file extraction service"
    )
    parser.add_argument(
        "--port", type=int, default=8011,
        help="Port to listen on (default: 8011)",
    )
    parser.add_argument(
        "--host", type=str, default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--field", type=str, default="file",
        help="Expected form field name (default: file). "
             "Note: FastAPI always accepts 'file' in this mock; "
             "use this flag as a reminder of what your config should set.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    print(f"Mock Multipart File Extractor")
    print(f"=============================")
    print(f"  Host:       {args.host}")
    print(f"  Port:       {args.port}")
    print(f"  Form field: {args.field}")
    print()
    print(f"Endpoints:")
    print(f"  POST /extract  - Upload file via multipart/form-data")
    print(f"  GET  /health   - Health check")
    print()
    print(f"Test with:")
    print(f"  curl -F '{args.field}=@document.pdf' http://{args.host}:{args.port}/extract")
    print()

    uvicorn.run(app, host=args.host, port=args.port)
