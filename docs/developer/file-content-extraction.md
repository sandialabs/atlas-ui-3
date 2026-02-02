# File Content Extraction

**Last updated:** 2026-01-30

This document describes how to configure automatic file content extraction for uploaded files (PDFs, images, etc.) in Atlas UI.

## Overview

When users upload files, Atlas UI can automatically extract text content and send it to the LLM as context. This is controlled by:

1. **Feature flag**: `FEATURE_FILE_CONTENT_EXTRACTION_ENABLED`
2. **Config file**: `config/defaults/file-extractors.json`
3. **Per-file mode toggle**: Users can cycle extraction mode per file in the UI

## Extraction Modes

Atlas UI supports three extraction modes, selectable globally and per-file:

| Mode | Description | LLM Prompt Behavior |
|------|-------------|---------------------|
| `full` | Default. Entire extracted text injected into context | Wrapped in `<< content of file X >>` / `<< end content of file X >>` markers, no truncation |
| `preview` | Truncated preview only | First 10 lines / 2000 chars with "Content preview:" label |
| `none` | Filename listed, no content | File listed by name only; content available on request |

The UI displays a clickable badge that cycles through modes: green (full) -> blue (preview) -> gray (none).

Legacy config values are automatically normalized: `"extract"` -> `"full"`, `"attach_only"` -> `"none"`.

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
  "default_behavior": "full",

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
| `default_behavior` | string | `"full"`, `"preview"`, or `"none"` - system default (legacy `"extract"` and `"attach_only"` auto-normalized) |
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
| `request_format` | string | `"base64"` | `"base64"`, `"multipart"`, or `"url"` |
| `form_field_name` | string | `"file"` | Form field name for multipart uploads |
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

### Base64 JSON Format (`request_format: "base64"`)

The default format sends the file as a base64-encoded string in a JSON payload:

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

### Multipart Form-Data Format (`request_format: "multipart"`)

Sends the file as a multipart form-data upload, equivalent to `curl -F 'file=@document.pdf'`. This is useful for extraction services that accept standard file uploads.

**Request:** `POST` with `Content-Type: multipart/form-data` containing the file in the field specified by `form_field_name` (default: `"file"`). An `Accept: application/json` header is included automatically.

**Response:** Same JSON format as base64 -- the extractor must return JSON with the field specified by `response_field`.

**Example config:**
```json
{
  "extractors": {
    "pdf-text": {
      "url": "https://example.com/nlp/extract",
      "method": "POST",
      "request_format": "multipart",
      "form_field_name": "file",
      "response_field": "text",
      "api_key": "${NLP_EXTRACT_API_KEY}",
      "enabled": true
    }
  }
}
```

## Example Implementations

| Extractor | Backend |
|-----------|---------|
| `pdf-text` | Custom PDF extraction service |
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
