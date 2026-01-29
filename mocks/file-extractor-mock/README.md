# Mock File Extractor Service

A mock HTTP service for testing the ATLAS file content extraction feature.

**Created:** 2026-01-19

## Overview

This service provides mock endpoints for file content extraction, following the contract defined in `docs/planning/file-content-extraction-plan-2026-01-18.md`.

All endpoints are hosted on the same server (default port 8010) at different paths:

| Endpoint | Description | Extraction Type |
|----------|-------------|-----------------|
| `/extract` | PDF text extraction | **Real** - uses `pypdf` |
| `/analyze` | Image vision analysis | Mock - returns generic description |
| `/ocr` | OCR text extraction | Mock - returns generic text |

## Quick Start

```bash
cd mocks/file-extractor-mock

# Install dependencies
pip install -r requirements.txt

cp endpoint_config.json.example endpoint_config.json

# modify endpoint_config.json to point to your deployed nemotron url endpoint

# Run the server
python main.py
```

Server starts at `http://127.0.0.1:8010`

## Configuration

Update your `config/overrides/file-extractors.json` to use this mock service:

```json
{
  "enabled": true,
  "default_behavior": "extract",
  "extractors": {
    "pdf-text": {
      "url": "http://localhost:8010/extract",
      "method": "POST",
      "timeout_seconds": 30,
      "max_file_size_mb": 50,
      "preview_chars": 2000,
      "response_field": "text"
    },
    "image-vision": {
      "url": "http://localhost:8010/analyze",
      "method": "POST",
      "timeout_seconds": 60,
      "max_file_size_mb": 20,
      "response_field": "description"
    },
    "ocr": {
      "url": "http://localhost:8010/ocr",
      "method": "POST",
      "timeout_seconds": 45,
      "response_field": "text"
    }
  },
  "extension_mapping": {
    ".pdf": "pdf-text",
    ".png": "image-vision",
    ".jpg": "image-vision",
    ".jpeg": "image-vision",
    ".gif": "image-vision",
    ".webp": "image-vision",
    ".tiff": "ocr",
    ".bmp": "ocr"
  }
}
```

## API Reference

### Request Format

All endpoints accept the same request format:

```json
{
  "content": "<base64-encoded-file>",
  "filename": "document.pdf",
  "options": {
    "preview_chars": 2000,
    "extract_images": false
  }
}
```

### Response Format

```json
{
  "success": true,
  "text": "Extracted content here...",
  "description": "For image-vision only...",
  "error": "Error message if failed",
  "metadata": {
    "pages": 5,
    "char_count": 12500,
    "truncated": false,
    "width": 1920,
    "height": 1080,
    "format": "PNG"
  }
}
```

## Endpoint Details

### POST /extract - PDF Text Extraction

**Real extraction** using `banyan-ingest`. If `banyan-ingest` is not installed or the endpoint url config is not set, then use the `pypdf` library. Actually extracts text content from PDF files.

Returns:
- `text`: Extracted text with page markers
- `metadata.pages`: Number of pages
- `metadata.char_count`: Total character count
- `metadata.truncated`: Whether content exceeds preview_chars

### POST /analyze - Image Vision Analysis

**Mock endpoint** that returns generic descriptions based on filename hints.

Filename keywords trigger different responses:
- `chart`, `graph`, `plot` - Chart/graph description
- `screenshot`, `screen`, `ui` - UI screenshot description
- `doc`, `scan`, `page` - Document image description
- Other - Generic image description

If `Pillow` is installed, also returns image dimensions.

### POST /ocr - OCR Text Extraction

**Mock endpoint** that returns generic OCR-style text.

Filename keywords trigger different responses:
- `receipt`, `invoice`, `bill` - Receipt-style text
- `form`, `application`, `survey` - Form-style text
- Other - Generic lorem ipsum text

### GET /health

Health check endpoint. Returns:

```json
{
  "status": "healthy",
  "timestamp": "2026-01-19T10:30:00.000Z"
}
```

### GET /

Service info and documentation.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| PORT | 8010 | Server port (if modified in code) |

## Testing

Test with curl:

```bash
# Health check
curl http://localhost:8010/health

# PDF extraction (requires a real PDF)
curl -X POST http://localhost:8010/extract \
  -H "Content-Type: application/json" \
  -d '{"content": "JVBERi0xLjQK...", "filename": "test.pdf"}'

# Image analysis
curl -X POST http://localhost:8010/analyze \
  -H "Content-Type: application/json" \
  -d '{"content": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "filename": "test.png"}'

# OCR
curl -X POST http://localhost:8010/ocr \
  -H "Content-Type: application/json" \
  -d '{"content": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==", "filename": "receipt.tiff"}'
```

## Integration with ATLAS

1. Start the mock service: `python main.py`
2. Enable file content extraction: `FEATURE_FILE_CONTENT_EXTRACTION_ENABLED=true`
3. Create `config/overrides/file-extractors.json` with the configuration above
4. Upload a PDF to ATLAS - text will be extracted automatically
