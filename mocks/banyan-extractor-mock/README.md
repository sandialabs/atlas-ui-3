# Banyan Extractor Mock Service

Created: 2026-01-30

A file extraction service using [banyan-ingest](https://github.com/sandialabs/banyan-ingest) for PDF and PPTX content extraction via Nemotron Parse.

Falls back to `pypdf` for PDF extraction when banyan-ingest is not installed.

## Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/extract` | POST | PDF text extraction (banyan-ingest, falls back to pypdf) |
| `/extract-pptx` | POST | PPTX text extraction (banyan-ingest required) |
| `/health` | GET | Health check (includes banyan-ingest availability) |

## Quick Start

```bash
cd mocks/banyan-extractor-mock

# Install dependencies (requires git access to sandialabs/banyan-ingest)
pip install -r requirements.txt

# Configure Nemotron endpoint
cp endpoint_config.json.example endpoint_config.json
# Edit endpoint_config.json with your Nemotron Parse URL

# Run the server
python main.py
```

Server starts at `http://127.0.0.1:8011`

## Prerequisites

- **banyan-ingest**: Installed from GitHub (`pip install -r requirements.txt`)
- **Nemotron Parse endpoint**: A running Nemotron Parse service (configure in `endpoint_config.json`)
- **poppler**: System dependency for `pdf2image` (`apt install poppler-utils` or `dnf install poppler-utils`)
- **pypdf**: Included as fallback for PDF extraction without banyan-ingest

## Configuration

### endpoint_config.json

Copy `endpoint_config.json.example` and set your Nemotron endpoint URL:

```json
{
  "url": "https://your-nemotron-endpoint/v1",
  "model_name": "nvidia/nemotron-parse"
}
```

### ATLAS Integration

Add entries to `config/overrides/file-extractors.json`:

```json
{
  "extractors": {
    "pdf-text": {
      "url": "http://localhost:8011/extract",
      "method": "POST",
      "timeout_seconds": 120,
      "max_file_size_mb": 50,
      "preview_chars": 2000,
      "request_format": "base64",
      "response_field": "text",
      "enabled": true
    },
    "pptx-text": {
      "url": "http://localhost:8011/extract-pptx",
      "method": "POST",
      "timeout_seconds": 120,
      "max_file_size_mb": 100,
      "preview_chars": 2000,
      "request_format": "base64",
      "response_field": "text",
      "enabled": true
    }
  },
  "extension_mapping": {
    ".pdf": "pdf-text",
    ".pptx": "pptx-text"
  },
  "mime_mapping": {
    "application/pdf": "pdf-text",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx-text"
  }
}
```

## API Reference

### Request Format

All POST endpoints accept:

```json
{
  "content": "<base64-encoded-file>",
  "filename": "document.pdf",
  "options": {
    "preview_chars": 2000
  }
}
```

### Response Format

```json
{
  "success": true,
  "text": "Extracted content...",
  "error": null,
  "metadata": {
    "pages": 5,
    "char_count": 12500,
    "truncated": false
  }
}
```

## How It Works

### PDF Extraction (`/extract`)

1. Decodes base64 content to bytes
2. Writes bytes to a temporary file (banyan-ingest `process_document()` expects a file path)
3. Attempts extraction with `NemoparseProcessor` using the configured endpoint
4. If banyan-ingest is not installed or fails, falls back to `pypdf`
5. Returns extracted text as markdown

### PPTX Extraction (`/extract-pptx`)

1. Decodes base64 content to bytes
2. Writes bytes to a temporary file
3. Extracts content using banyan-ingest `PptxProcessor`
4. Returns extracted text as markdown
5. No fallback - banyan-ingest is required for PPTX

## Testing

```bash
# Health check
curl http://localhost:8011/health

# PDF extraction
curl -X POST http://localhost:8011/extract \
  -H "Content-Type: application/json" \
  -d '{"content": "<base64-pdf>", "filename": "test.pdf"}'

# PPTX extraction
curl -X POST http://localhost:8011/extract-pptx \
  -H "Content-Type: application/json" \
  -d '{"content": "<base64-pptx>", "filename": "slides.pptx"}'
```

## Differences from file-extractor-mock

| Feature | file-extractor-mock | banyan-extractor-mock |
|---------|--------------------|-----------------------|
| Port | 8010 | 8011 |
| PDF extraction | pypdf only | banyan-ingest (pypdf fallback) |
| PPTX extraction | Not supported | banyan-ingest PptxProcessor |
| Image analysis | Mock responses | Not supported |
| OCR | Mock responses | Not supported |
| External deps | pypdf only | banyan-ingest, Nemotron endpoint |
