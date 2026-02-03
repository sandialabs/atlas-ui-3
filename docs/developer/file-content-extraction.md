# File Content Extraction

**Last updated:** 2026-02-02

This document describes how to configure automatic file content extraction for uploaded files (PDFs, images, etc.) in Atlas UI.

## Overview

When users upload files, Atlas UI can automatically extract text content and send it to the LLM as context. This is controlled by:

1. **Feature flag**: `FEATURE_FILE_CONTENT_EXTRACTION_ENABLED`
2. **Config file**: `config/defaults/file-extractors.json`
3. **Per-file toggle**: Users can enable/disable extraction per file in the UI

## Configuration

### Feature Flag

Enable file content extraction in your `.env`:

```bash
FEATURE_FILE_CONTENT_EXTRACTION_ENABLED=true
```

### Extractors Configuration

Create or edit `config/defaults/file-extractors.json` (or `config/overrides/file-extractors.json` for deployment customization):

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
      "request_format": "base64",
      "response_field": "text",
      "enabled": true,
      "api_key": "${PDF_EXTRACTOR_API_KEY}",
      "headers": {
        "X-Client-ID": "${PDF_EXTRACTOR_CLIENT_ID}"
      }
    }
  },

  "extension_mapping": {
    ".pdf": "pdf-text"
  },

  "mime_mapping": {
    "application/pdf": "pdf-text"
  }
}
```

### Configuration Fields

| Field | Type | Description |
|-------|------|-------------|
| `enabled` | bool | Global kill switch (independent of feature flag) |
| `default_behavior` | string | `"extract"` or `"attach_only"` - system default |
| `extractors` | object | Named extractor services with HTTP config |
| `extension_mapping` | object | File extension to extractor name mapping |
| `mime_mapping` | object | MIME type to extractor name (fallback) |

### Extractor Configuration Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `url` | string | required | HTTP endpoint URL |
| `method` | string | `"POST"` | HTTP method |
| `timeout_seconds` | int | `30` | Request timeout |
| `max_file_size_mb` | int | `50` | Maximum file size limit |
| `preview_chars` | int | `2000` | Chars to include in preview |
| `request_format` | string | `"base64"` | `"base64"` or `"url"` |
| `response_field` | string | `"text"` | JSON field containing extracted text |
| `enabled` | bool | `true` | Enable/disable this extractor |
| `api_key` | string | `null` | API key for authentication (see below) |
| `headers` | object | `null` | Custom HTTP headers (see below) |

## Authentication

### API Key

Set the `api_key` field to authenticate with your extraction service. The API key is sent as a Bearer token in the `Authorization` header:

```
Authorization: Bearer <api_key>
```

### Environment Variable Support

Both `api_key` and `headers` support the `${ENV_VAR}` syntax for secure credential management:

```json
{
  "extractors": {
    "pdf-text": {
      "url": "http://localhost:8010/extract",
      "api_key": "${PDF_EXTRACTOR_API_KEY}",
      "headers": {
        "X-Client-ID": "${PDF_EXTRACTOR_CLIENT_ID}",
        "X-API-Version": "2024-01"
      }
    }
  }
}
```

Then set in your `.env`:

```bash
PDF_EXTRACTOR_API_KEY=sk-your-secret-key
PDF_EXTRACTOR_CLIENT_ID=my-client-id
```

**Behavior:**
- **URL**: If the env var is not set, the extractor is **disabled** (URL is required)
- **api_key**: If the env var is not set, requests are made **without authentication**
- **headers**: If a header's env var is not set, that header is **omitted**

### Custom Headers

Use the `headers` field for custom authentication schemes or additional metadata:

```json
{
  "extractors": {
    "my-extractor": {
      "url": "https://api.example.com/extract",
      "api_key": "${MY_API_KEY}",
      "headers": {
        "X-Tenant-ID": "tenant-123",
        "X-Request-Source": "atlas-ui"
      }
    }
  }
}
```

## Extractor Service Contract

Each extractor URL should accept:

**Request:**
```json
{
  "content": "<base64-encoded-file>",
  "filename": "document.pdf",
  "options": {
    "preview_chars": 2000
  }
}
```

**Response:**
```json
{
  "success": true,
  "text": "Extracted content here...",
  "metadata": {
    "pages": 5,
    "char_count": 12500
  }
}
```

## Mock Extractor Services

Atlas UI includes two mock extractor services for development and testing:

### file-extractor-mock (Port 8010)

Basic PDF extraction using pypdf. Lightweight, no external dependencies beyond pypdf.

- **Endpoint**: `POST /extract` - PDF text extraction
- **Start**: `cd mocks/file-extractor-mock && bash run.sh`

### banyan-extractor-mock (Port 8011)

Advanced extraction using banyan-ingest with Nemotron Parse for PDFs and PptxProcessor for PowerPoint files. Falls back to pypdf for PDF extraction if banyan-ingest is not installed.

- **Endpoints**:
  - `POST /extract` - PDF extraction (banyan-ingest with pypdf fallback)
  - `POST /extract-pptx` - PPTX extraction (banyan-ingest required)
  - `GET /health` - Health check (reports banyan-ingest availability)
- **Start**: `cd mocks/banyan-extractor-mock && bash run.sh`
- **Config**: Copy `endpoint_config.json.example` to `endpoint_config.json` and set your Nemotron endpoint URL

To use the banyan extractor for PDFs, update `config/overrides/file-extractors.json` to point the `pdf-text` extractor URL to `http://localhost:8011/extract`. For PPTX support, add a `pptx-text` extractor pointing to `http://localhost:8011/extract-pptx`.

## Example Implementations

| Extractor | Backend |
|-----------|---------|
| `pdf-text` | file-extractor-mock (pypdf) or banyan-extractor-mock (Nemotron Parse) |
| `pptx-text` | banyan-extractor-mock (banyan-ingest PptxProcessor) |
| `image-vision` | OpenAI Vision API, Claude Vision |
| `ocr` | Tesseract, Google Cloud Vision |

## Troubleshooting

### Extractor disabled unexpectedly

Check the backend logs for messages like:
- `"Resolved API key env var for extractor 'pdf-text'"` - env var resolved successfully
- `"API key env var not set for extractor 'pdf-text', will make unauthenticated requests"` - optional env var missing
- `"Failed to resolve URL env var for extractor 'pdf-text'"` - required URL env var missing, extractor disabled

### Connection errors

Verify the extractor service is running and accessible from the backend. Check:
- Firewall rules
- Docker network configuration
- Service health endpoints

### File too large

Increase `max_file_size_mb` in the extractor config or compress files before upload.
