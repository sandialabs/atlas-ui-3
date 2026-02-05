# Mock Multipart File Extractor

**Created:** 2026-01-30

A standalone mock server that accepts file uploads via **multipart/form-data** for testing the Atlas multipart extraction path. Mimics an NLP extraction endpoint.

## Quick Start

```bash
# Terminal 1: Start the mock extractor
cd mocks/multipart-extractor-mock
python multipart_extractor_mock.py

# Terminal 2: Test with curl
curl -F 'file=@/path/to/document.pdf' http://localhost:8011/extract
```

## Live Testing with Atlas

### 1. Start the mock extractor

```bash
cd mocks/multipart-extractor-mock
python multipart_extractor_mock.py
# Listening on http://127.0.0.1:8011
```

### 2. Copy the sample config into overrides

```bash
cp mocks/multipart-extractor-mock/sample-file-extractors.json \
   config/overrides/file-extractors.json
```

This configures Atlas to send PDF, TXT, MD, and CSV files to the mock via multipart upload.

### 3. Enable file extraction in .env

```bash
FEATURE_FILE_CONTENT_EXTRACTION_ENABLED=true
```

### 4. Start Atlas

```bash
bash agent_start.sh
```

### 5. Upload a file in the browser

Open http://localhost:8000, attach a PDF or text file to a chat message. You should see:
- The mock server logs the upload (filename, size, content type)
- Atlas displays the extracted text in the conversation

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/extract` | Upload a file via multipart form-data, returns extracted text |
| GET | `/health` | Health check |
| GET | `/` | Service info |

## Response Format

```json
{
  "id": null,
  "text": "Extracted text content here...",
  "filepath": "document.pdf",
  "extract_timestamp": "2026-01-30T15:30:00.000000+00:00",
  "extract_user": "mock-service",
  "metadata": { "pages": 3, "char_count": 5200 },
  "sections": []
}
```

Atlas reads the `text` field (configured via `response_field` in extractor config).

## Supported File Types

| Extension | Extraction |
|-----------|-----------|
| `.pdf` | Real text extraction via `pypdf` (install with `pip install pypdf`) |
| `.txt`, `.md`, `.csv`, `.json`, `.xml`, `.html`, `.log` | Read as plain text (UTF-8 with Latin-1 fallback) |
| Other | Returns a placeholder message describing the upload |

## CLI Options

```
python multipart_extractor_mock.py [--port PORT] [--host HOST] [--field FIELD]

  --port   Port to listen on (default: 8011)
  --host   Host to bind (default: 127.0.0.1)
  --field  Reminder of the expected form field name (default: file)
```

## Testing with curl

```bash
# Upload a PDF
curl -F 'file=@report.pdf' http://localhost:8011/extract

# Upload a text file
curl -F 'file=@notes.txt' http://localhost:8011/extract

# Upload with a custom field name (server always accepts 'file')
curl -F 'file=@data.csv' http://localhost:8011/extract

# Health check
curl http://localhost:8011/health
```
