# Vision Image Support

**PR**: #466
**Date**: 2026-03-23

## Overview

Models with vision capabilities can receive images as inline content blocks in the user message, rather than having image files described in the plain-text files manifest. This allows LLMs to actually "see" uploaded images.

## Configuration

Add `supports_vision: true` to any model entry in `llmconfig.yml`:

```yaml
models:
  gpt-4o:
    model_url: "https://api.openai.com/v1/chat/completions"
    model_name: "gpt-4o"
    api_key: "${OPENAI_API_KEY}"
    compliance_level: "External"
    supports_vision: true
```

The field defaults to `false` when omitted. The config is read from `config/llmconfig.yml` (project root, takes priority) with fallback to `atlas/config/llmconfig.yml` (package defaults).

## Eligible MIME Types

Only raster image formats are embedded as vision content:

- `image/png`
- `image/jpeg`
- `image/gif`
- `image/webp`

SVG files (`image/svg+xml`) are excluded because they are XML text, not raster data, and most LLM vision APIs do not support them.

## Size and Count Limits

- **Max image size**: 20 MB per image (base64-encoded)
- **Max images per message**: 10

Images exceeding these limits fall back to the standard files manifest (text description only).

## How It Works

1. **Config loading** (`config_manager.py`): `ModelConfig.supports_vision` is parsed from YAML.
2. **API exposure** (`config_routes.py`): `/api/config` and `/api/config/shell` include `supports_vision` per model so the frontend can adapt.
3. **File processing** (`file_processor.py`): When `model_supports_vision=True`, `handle_session_files` stores raw `image_b64` and `image_mime_type` on eligible file refs in the session context.
4. **Message building** (`message_builder.py`): `build_messages(model_supports_vision=True)` finds stored image data and replaces the last user message with a multimodal content array containing `text` and `image_url` blocks (using data URIs). The files manifest excludes these images to avoid duplication.
5. **Orchestrator** (`orchestrator.py`): `_model_supports_vision()` checks the config and threads the flag through to the message builder and file processor.
6. **Frontend** (`ChatArea.jsx`): Checks `supports_vision` on the current model to conditionally show image upload UI elements.

## Stale Image Cleanup

When a new turn begins, `handle_session_files` clears `image_b64` and `image_mime_type` from all existing file refs in the session context. This prevents images from a previous turn being re-sent in subsequent messages.

## Message Format

The multimodal message uses the OpenAI `image_url` format with data URIs, which LiteLLM translates to provider-specific formats:

```json
{
  "role": "user",
  "content": [
    {"type": "text", "text": "What's in this image?"},
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/png;base64,iVBOR..."
      }
    }
  ]
}
```
