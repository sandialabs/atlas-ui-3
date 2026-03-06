# 3-State Chat Save Mode

Last updated: 2026-02-25

## Overview

Chat history supports three save modes that the user cycles through by clicking the save button in the header:

| Mode | Icon | Color | Storage |
|------|------|-------|---------|
| Incognito | Database + strikethrough | Red | Nothing saved |
| Saved Locally | HardDrive | Blue | Browser IndexedDB |
| Saved to Server | Cloud | Green | Backend database |

The selected mode persists across page refreshes via `usePersistentState` (localStorage key: `chatui-save-mode`).

## Architecture

### Frontend

- **`saveModeConfig.js`**: Shared constants (`SAVE_MODES = ['none', 'local', 'server']`) and `nextSaveMode()` cycling function
- **`localConversationDB.js`**: IndexedDB wrapper (`atlas-chat-local` database) that provides the same data shape as the server REST API
- **`useLocalConversationHistory.js`**: Drop-in replacement for `useConversationHistory` backed by IndexedDB instead of server API calls
- **`ChatContext.jsx`**: Manages `saveMode` state, auto-saves to IndexedDB when in `local` mode (debounced 1s), sends `save_mode` field over WebSocket
- **`Header.jsx`**: 3-state cycling button with distinct icons and colors per mode
- **`Sidebar.jsx`**: Calls both `useConversationHistory` and `useLocalConversationHistory` unconditionally (React hooks rules), then selects the active one based on `saveMode`

### Backend

- **`main.py`**: Treats `save_mode !== 'server'` as incognito (skips database persistence)
- **`config_routes.py`**: Exposes `chat_history_save_modes: ['none', 'local', 'server']` in the features config

### Data Flow

```
User clicks save button -> cycles: server -> none -> local -> server
  Mode persisted to localStorage

When saveMode === 'local':
  Messages change -> 1s debounce -> saveConversation() to IndexedDB
  Sidebar shows conversations from useLocalConversationHistory
  Backend receives save_mode='local' -> does NOT save to database

When saveMode === 'server':
  Backend receives save_mode='server' -> saves to database normally
  Sidebar shows conversations from useConversationHistory (server API)

When saveMode === 'none':
  Nothing saved anywhere
  Sidebar shows empty conversation list
```

## IndexedDB Schema

Database: `atlas-chat-local`, version 1, object store: `conversations`

- Key path: `id`
- Indexes: `user_email`, `updated_at`
- Each record stores the full conversation including messages array
- `summaryFromRecord()` converts full records to the list-view summary shape with `_local: true` flag
