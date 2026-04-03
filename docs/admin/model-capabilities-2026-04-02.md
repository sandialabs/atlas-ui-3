# Model Capabilities Enforcement

**PR**: #491
**Date**: 2026-04-02

## Overview

Models can declare their capabilities in `llmconfig.yml`. The backend enforces these capabilities at runtime, stripping unsupported features and warning the user. The frontend displays capability indicators in the model dropdown and shows warning banners when incompatible features are selected.

## Configuration

### `supports_tools`

Add `supports_tools: false` to any model entry in `llmconfig.yml` to indicate it does not support tool/function calling:

```yaml
models:
  groq-gpt-oss-120b:
    model_url: "https://api.groq.com/openai/v1/chat/completions"
    model_name: "gpt-oss-120b"
    api_key: "${GROQ_API_KEY}"
    compliance_level: "External"
    supports_tools: false
```

The field defaults to `true` when omitted, for backward compatibility with existing configs.

### `model_card`

Optional rich markdown text displayed in the model info panel:

```yaml
models:
  gpt-4o:
    model_name: "gpt-4o"
    model_url: "https://api.openai.com/v1/chat/completions"
    model_card: |
      Latest multimodal model with vision and tool support.
      Context window: 128K tokens.
```

The field defaults to `null` when omitted.

## Backend Enforcement

### Tool Stripping

When a user sends a chat request with tools selected but the model has `supports_tools: false`:

1. `ChatOrchestrator._model_supports_tools()` checks the model config
2. All selected tools are removed from the request
3. A `warning` WebSocket message is sent to the client
4. The request falls through to plain or RAG mode instead of tools mode

### Agent Mode Blocking

Agent mode requires tool calling. When a user enables agent mode with a non-tool-capable model:

1. Agent mode is disabled for the request
2. A `warning` WebSocket message is sent
3. The request falls through to plain mode

### Warning Delivery

Capability warnings are sent via `EventPublisher.publish_warning()`, which sends a WebSocket message with `type: "warning"`. This is distinct from `publish_chat_response()` (which sends `type: "chat_response"`), ensuring the frontend can render warnings with appropriate styling.

The same `warning` message type is used by `file_processor.py` for vision capability warnings.

## WebSocket Message Types

### `warning` (new in PR #491)

Sent when the backend detects a capability mismatch.

```json
{
  "type": "warning",
  "message": "**Note:** The model `groq-gpt-oss-120b` does not support tool/function calling. Your selected tools have been disabled for this request."
}
```

The frontend renders warning messages with `role: "system"` and `type: "warning"` styling.

## Frontend Behavior

### Model Dropdown

- Vision-capable models show an eye icon
- Tool-capable models show a wrench icon (dimmed when `supports_tools: false`)
- Models with a `model_card` show an info button that opens a detail panel

### Warning Banners

Yellow warning banners appear above the chat input when:
- Tools are selected but the current model has `supports_tools: false`
- Images are uploaded but the current model has `supports_vision: false`

## How It Works End-to-End

1. **Config loading** (`config_manager.py`): `ModelConfig.supports_tools` and `ModelConfig.model_card` are parsed from YAML.
2. **API exposure** (`config_routes.py`): `/api/config` and `/api/config/shell` include `supports_tools` and `model_card` per model so the frontend can adapt.
3. **Orchestrator** (`orchestrator.py`): `_model_supports_tools()` checks the config and strips tools or agent mode with warnings when needed.
4. **Event publisher** (`interfaces/events.py`, `websocket_publisher.py`): `publish_warning()` sends `type: "warning"` messages over WebSocket.
5. **Frontend handler** (`websocketHandlers.js`): The `warning` case adds a system message with warning styling.
6. **Frontend UI** (`ChatArea.jsx`, `Header.jsx`): Capability icons and warning banners driven by model config from `/api/config`.
