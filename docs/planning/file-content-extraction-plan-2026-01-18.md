# File Content Extraction Feature Plan

**Created:** 2026-01-18
**Status:** Proposed
**Author:** Claude Code

## Overview

When users upload PDFs or images, they often expect ATLAS to automatically extract and analyze the content. Currently, uploaded files are attached to the session context and listed in a manifest sent to the LLM, but the actual file contents are not extracted. The LLM only knows files exist - not what's inside them.

This plan introduces a configurable, feature-flagged system for automatic file content extraction with:
- HTTP-based extraction services (swappable backends)
- Config-driven extension-to-extractor mapping
- Per-file user toggle to disable extraction when desired

## Current Behavior

1. Frontend converts uploaded file to base64 and sends via WebSocket
2. Backend uploads to S3 and stores metadata in session context
3. LLM receives a files manifest: "Available session files: report.pdf, chart.png"
4. LLM does **not** receive actual content unless a tool explicitly fetches and processes it

## Proposed Architecture

### 1. Feature Flag

Add to `AppSettings` in `backend/modules/config/config_manager.py`:

```python
feature_file_content_extraction_enabled: bool = Field(
    default=False,
    validation_alias=AliasChoices(
        "FEATURE_FILE_CONTENT_EXTRACTION_ENABLED",
        "FILE_CONTENT_EXTRACTION_ENABLED"
    )
)
```

**Environment variable:** `FEATURE_FILE_CONTENT_EXTRACTION_ENABLED=true`

When `false`, files are attached as references only (current behavior).

### 2. New Config File: `file-extractors.json`

**Location:** `config/defaults/file-extractors.json` (with override support in `config/overrides/`)

**Structure:**

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
      "response_field": "text"
    },
    "image-vision": {
      "url": "http://localhost:8011/analyze",
      "method": "POST",
      "timeout_seconds": 60,
      "max_file_size_mb": 20,
      "request_format": "base64",
      "response_field": "description",
      "enabled": false
    },
    "ocr": {
      "url": "http://localhost:8012/ocr",
      "method": "POST",
      "timeout_seconds": 45,
      "request_format": "base64",
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
  },

  "mime_mapping": {
    "application/pdf": "pdf-text",
    "image/png": "image-vision",
    "image/jpeg": "image-vision",
    "image/gif": "image-vision",
    "image/webp": "image-vision",
    "image/tiff": "ocr",
    "image/bmp": "ocr"
  }
}
```

**Configuration Fields:**

| Field | Purpose |
|-------|---------|
| `enabled` | Global kill switch (independent of feature flag) |
| `default_behavior` | `"extract"` or `"attach_only"` - system default |
| `extractors` | Named extractor services with HTTP config |
| `extension_mapping` | File extension to extractor name |
| `mime_mapping` | MIME type to extractor name (fallback) |

### 3. Extractor Service Contract

Each extractor URL expects a simple HTTP POST with a standard contract:

**Request:**
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

**Response:**
```json
{
  "success": true,
  "text": "Extracted content here...",
  "metadata": {
    "pages": 5,
    "char_count": 12500,
    "truncated": true
  }
}
```

This allows extractors to be:
- Internal MCP servers (wrap existing `pdfbasic` as HTTP)
- External APIs (OpenAI vision, Google Document AI, etc.)
- Self-hosted services (Tesseract OCR container, etc.)

### 4. User Toggle: Per-Upload Extraction Control

**Frontend UI Addition:**

Add a toggle or dropdown in the file upload area:
- **"Extract content"** (default if `default_behavior: "extract"`)
- **"Attach only"** (skip extraction, just reference)

**WebSocket Message Change:**

```json
{
  "type": "chat",
  "content": "Analyze this document",
  "files": {
    "report.pdf": {
      "content": "<base64>",
      "extract": true
    },
    "raw-data.pdf": {
      "content": "<base64>",
      "extract": false
    }
  }
}
```

The `extract` field (boolean) overrides the system default per-file.

### 5. Pydantic Models

New models in `backend/modules/config/config_manager.py`:

```python
class FileExtractorConfig(BaseModel):
    url: str
    method: str = "POST"
    timeout_seconds: int = 30
    max_file_size_mb: int = 50
    preview_chars: Optional[int] = 2000
    request_format: str = "base64"  # or "url"
    response_field: str = "text"
    enabled: bool = True


class FileExtractorsConfig(BaseModel):
    enabled: bool = True
    default_behavior: str = "extract"  # or "attach_only"
    extractors: Dict[str, FileExtractorConfig] = {}
    extension_mapping: Dict[str, str] = {}
    mime_mapping: Dict[str, str] = {}
```

### 6. Backend Processing Flow

```
User uploads file
       |
       v
Check FEATURE_FILE_CONTENT_EXTRACTION_ENABLED
       |
       v (if false, skip to attach)
Check per-file "extract" flag from frontend
       |
       v (if false, skip to attach)
Lookup extractor by extension -> mime fallback
       |
       v (if no match, skip to attach)
Check extractor.enabled and file size limits
       |
       v
HTTP POST to extractor URL
       |
       v
Store extracted text in file metadata:
  session.context["files"][filename]["extracted_content"] = "..."
       |
       v
Include in files manifest to LLM:
  "report.pdf (5 pages, preview: First 2000 chars of extracted text...)"
```

### 7. Config Layering

Following existing patterns:

1. **Code defaults** - Pydantic model defaults (extraction disabled)
2. **`config/defaults/file-extractors.json`** - Shipped defaults
3. **`config/overrides/file-extractors.json`** - Deployment customization
4. **Environment variables** - `FEATURE_FILE_CONTENT_EXTRACTION_ENABLED`

### 8. Frontend Config Exposure

Add to `/api/config` response in `backend/routes/config_routes.py`:

```json
{
  "features": {
    "file_content_extraction": true
  },
  "file_extraction": {
    "default_behavior": "extract",
    "supported_extensions": [".pdf", ".png", ".jpg", ".jpeg"]
  }
}
```

Frontend uses this to:
- Show/hide extraction toggle in upload UI
- Set default toggle state
- Show supported file type indicators

### 9. Example Extractor Implementations

Lightweight HTTP wrappers can be created for various backends:

| Extractor | Backend |
|-----------|---------|
| `pdf-text` | Wrap existing `pdfbasic` MCP server as HTTP endpoint |
| `image-vision` | Proxy to Claude/GPT-4V vision API |
| `ocr` | Tesseract container or Google Cloud Vision |
| `docx-text` | python-docx extraction service |
| `xlsx-text` | openpyxl/pandas extraction service |

Each is independently deployable and swappable via config.

## Implementation Phases

### Phase 1: Core Infrastructure
- Add feature flag to AppSettings
- Create Pydantic models for FileExtractorsConfig
- Add config file loading to ConfigManager
- Create generic HTTP extractor client

### Phase 2: PDF Extraction
- Create HTTP wrapper for existing pdfbasic MCP server
- Integrate extraction into file upload flow
- Update files manifest to include extracted content preview

### Phase 3: Frontend Toggle
- Add extraction toggle to upload UI
- Update WebSocket message format to include `extract` flag
- Expose config to frontend via /api/config

### Phase 4: Image/Vision Support
- Add image-vision extractor (optional, disabled by default)
- Support for multimodal inline content for vision-capable models

## Summary

| Concern | Solution |
|---------|----------|
| Feature flag | `FEATURE_FILE_CONTENT_EXTRACTION_ENABLED` |
| Global toggle | `file-extractors.json` -> `enabled` |
| Per-file toggle | Frontend sends `extract: true/false` per file |
| Extension mapping | Config-driven `extension_mapping` dict |
| HTTP interface | Generic contract, any service can implement |
| Swappable backends | Change URL in config, no code changes |
| Preview vs full | `preview_chars` limits context size |

This design keeps extraction logic decoupled from the core upload flow, makes it easy to add new file types, and gives users control over when extraction happens.
