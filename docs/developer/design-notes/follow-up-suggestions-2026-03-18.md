# Follow-up Question Suggestions

**Last updated: 2026-03-18**

Atlas UI 3 supports AI-generated follow-up question suggestions that appear after each assistant response. When enabled, the system generates three relevant questions the user might want to ask next, displayed as clickable pill buttons.

## Feature Flag

The feature is controlled by the `FEATURE_FOLLOWUP_SUGGESTIONS_ENABLED` environment variable. It defaults to `false`.

```env
FEATURE_FOLLOWUP_SUGGESTIONS_ENABLED=true
```

The flag is exposed to the frontend via the `/api/config` endpoint under `features.followup_suggestions`.

## How It Works

### Backend

**Endpoint**: `POST /api/suggest_followups`

**Request body**:
```json
{
  "messages": [
    {"role": "user", "content": "What is machine learning?"},
    {"role": "assistant", "content": "Machine learning is..."}
  ],
  "model": "gpt-4"
}
```

**Response**:
```json
{
  "questions": [
    "What are common ML algorithms?",
    "How does supervised learning differ from unsupervised?",
    "What tools are used for ML development?"
  ]
}
```

The endpoint filters conversation messages to only user/assistant pairs with content, then asks the configured LLM to generate exactly 3 follow-up questions. The response is parsed from JSON and capped at 3 questions. If the feature flag is disabled, the endpoint returns HTTP 404.

**Route file**: `atlas/routes/suggestion_routes.py`

### Frontend

After each assistant response completes (streaming or thinking finishes), `ChatContext.jsx` checks if `config.features.followup_suggestions` is enabled. If so, it calls the `/api/suggest_followups` endpoint with the current conversation and model.

The suggestions are rendered in `ChatArea.jsx` as pill-shaped buttons in a single horizontal row between the messages area and the input footer. The row scrolls horizontally if the buttons overflow, with the scrollbar hidden for a clean appearance.

**Layout details**:
- Buttons are left-aligned with message content (offset by the avatar width)
- Single row with `overflow-x: auto` and hidden scrollbar
- Buttons use `whitespace-nowrap` and `flex-shrink-0` to prevent wrapping

**Auto-clearing**: Suggestions are cleared when:
- The user starts typing in the input field
- A new message is sent
- The chat is reset

## Related Files

- `atlas/routes/suggestion_routes.py` — Backend endpoint
- `atlas/modules/config/config_manager.py` — Feature flag definition
- `atlas/routes/config_routes.py` — Exposes flag to frontend
- `frontend/src/contexts/ChatContext.jsx` — Fetches suggestions after response
- `frontend/src/components/ChatArea.jsx` — Renders suggestion buttons
