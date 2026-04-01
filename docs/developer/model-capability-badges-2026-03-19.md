# Model Capability Badges and Info Panel

**Last updated: 2026-03-19**

The model selection dropdown shows capability badges, descriptions, and an expandable info panel for each model. These are driven by optional fields in `llmconfig.yml`.

## Configuration Fields

Add the following optional fields to any model entry in `llmconfig.yml`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `supports_vision` | `bool` | `false` | Whether the model accepts image inputs. Also controls multimodal content block routing. |
| `supports_tools` | `bool` or omit | `null` | Whether the model supports tool/function calling. |
| `supports_reasoning` | `bool` or omit | `null` | Whether the model supports chain-of-thought reasoning. |
| `context_window` | `int` or omit | `null` | Maximum context window in tokens. |
| `model_card_url` | `string` or omit | `null` | URL to the model's documentation page. Must be `http://` or `https://`. |

### Example

```yaml
models:
  gpt-4.1:
    model_url: "https://api.openai.com/v1/chat/completions"
    model_name: "gpt-4.1"
    api_key: "${OPENAI_API_KEY}"
    description: "OpenAI GPT-4.1 — strong general-purpose model"
    compliance_level: "External"
    supports_vision: true
    supports_tools: true
    supports_reasoning: false
    context_window: 1048576
    model_card_url: "https://platform.openai.com/docs/models/gpt-4.1"
```

## Behavior

### Backend

The `ModelConfig` Pydantic model in `atlas/modules/config/config_manager.py` defines these fields. The `_add_capability_fields` helper in `atlas/routes/config_routes.py` adds non-null capability fields to the model info dicts returned by both `/api/config/shell` and `/api/config` endpoints.

- `supports_vision` is always included in API responses (defaults to `false`).
- Other capability fields are only included when explicitly set (non-null).
- `model_card_url` is validated to require `http://` or `https://` scheme.

### Frontend

The model dropdown in `Header.jsx` renders:

1. **Inline capability badges** next to each model name:
   - Eye icon (blue) for vision
   - Wrench icon (green) for tools
   - Brain icon (purple) for reasoning

2. **Model description** as a gray subtitle below the model name (from the existing `description` field).

3. **Info button** (circle-i icon) appears on models that have `model_card_url` or `context_window` set. Clicking it toggles an inline detail panel (`ModelInfoPopover` component) that shows:
   - Context window size (formatted as "128K tokens" or "1.0M tokens")
   - Capability badges with labels
   - "View Model Card" external link

## Notes

- `supports_vision` is a `bool` (not Optional) because it is also used by the vision/multimodal content routing logic added in PR #466. The other capability fields are `Optional[bool]` since they are purely informational for the UI.
- Omitting a capability field from config means the badge will not appear — there is no difference between omitting a field and setting it to `null`.
- The info button only appears when there is meaningful content to show (`model_card_url` or `context_window`).
