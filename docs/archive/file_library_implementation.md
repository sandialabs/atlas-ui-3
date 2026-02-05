# File Library Implementation Plan

## Overview

Add a "File Library" feature to show all user files across all sessions (not just current session files), with download, delete, and load-to-session capabilities.

## Current State

### Already Implemented (Backend)

All core backend functionality exists:

- `GET /api/files` - List all user files (files_routes.py:117)
- `GET /api/files/download/{file_key}` - Download file (files_routes.py:197)
- `DELETE /api/files/{file_key}` - Delete file (files_routes.py:139)
- `GET /api/users/{user_email}/files/stats` - User stats (files_routes.py:163)
- S3Client with full CRUD operations (modules/file_storage/s3_client.py)
- Authorization and auth checks already integrated

### Current Frontend

- `FileManager.jsx` - Shows session files only
- `FileManagerPanel.jsx` - Modal wrapper for file manager
- Download/delete actions work for session files

## Implementation Plan

### Phase 1: Frontend Tab UI (1 day)

**Add tab switcher to FileManagerPanel:**

1. Add state for active tab: `useState('session' | 'library')`
2. Add tab buttons in header
3. Conditionally render SessionFilesView or AllFilesView

**Create new components:**

```
frontend/src/components/
├── AllFilesView.jsx        - New component (similar to FileManager)
└── SessionFilesView.jsx    - Rename/refactor existing FileManager
```

**AllFilesView features:**
- Fetch from `GET /api/files?limit=1000`
- Display file list with same UI as FileManager
- Add search/filter (client-side)
- Show file metadata: name, size, type, date, source
- Actions: Download, Delete, "Load to Session"

### Phase 2: Load to Session Feature (0.5 days)

**Backend:**

Add new endpoint or WebSocket message type:

```python
# Option A: REST endpoint
POST /api/sessions/current/files
Body: { "s3_key": "users/..." }

# Option B: WebSocket message
{ "type": "attach_file", "s3_key": "users/..." }
```

Implementation:
- Fetch file metadata from S3
- Add to session context files dictionary
- Emit files_update to frontend
- Return success/error

**Frontend:**
- Add "Load to Session" button in AllFilesView
- Call new endpoint/send WS message
- Show success notification
- Refresh session files view

### Phase 3: Polish (0.5 days)

**UX improvements:**
- Add loading states
- Add confirmation modal for delete
- Show which files are already in current session
- Add sort by (name, date, size, type)
- Add filter by type (code, image, document, data, other)
- Display storage stats

**Error handling:**
- Handle failed downloads
- Handle delete errors
- Handle network errors

## Implementation Details

### Tab UI Structure

```jsx
// FileManagerPanel.jsx
const [activeTab, setActiveTab] = useState('session')

<div className="tabs">
  <button onClick={() => setActiveTab('session')}>
    Session Files ({sessionFiles.total_files})
  </button>
  <button onClick={() => setActiveTab('library')}>
    All Files
  </button>
</div>

{activeTab === 'session' ? (
  <SessionFilesView files={sessionFiles} />
) : (
  <AllFilesView />
)}
```

### AllFilesView API Integration

```javascript
// AllFilesView.jsx
useEffect(() => {
  fetch('/api/files?limit=1000', {
    headers: { 'Authorization': `Bearer ${token}` }
  })
    .then(res => res.json())
    .then(files => {
      // Convert to organized format
      const organized = organizeFiles(files)
      setAllFiles(organized)
    })
}, [])
```

### Load to Session Logic

```javascript
const handleLoadToSession = async (file) => {
  try {
    const response = await fetch('/api/sessions/current/files', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ s3_key: file.key })
    })

    if (response.ok) {
      showNotification('File loaded to session')
      // Session files will update via WebSocket
    }
  } catch (error) {
    showError('Failed to load file')
  }
}
```

## File Organization

```
frontend/src/components/
├── FileManagerPanel.jsx      - Modal container with tabs (MODIFY)
├── SessionFilesView.jsx      - Current session files (RENAME from FileManager.jsx)
└── AllFilesView.jsx          - All user files (NEW)

atlas/routes/
└── files_routes.py           - Add attach endpoint (MODIFY)

atlas/application/chat/
└── service.py                - Add attach_file method (MODIFY)
```

## Testing

**Backend:**
- Test attach file to session
- Test authorization (can't attach other user's files)
- Test session context updates

**Frontend:**
- Test tab switching
- Test file list rendering
- Test download/delete actions
- Test load to session flow
- Test search/filter

**E2E:**
1. Upload file in session A
2. Start new session B
3. Open File Library
4. Find file from session A
5. Load into session B
6. Verify file appears in session B files

## Success Criteria

- Users can view all their files across all sessions
- Users can download any file
- Users can delete any file
- Users can load old files into current session
- UI is responsive and intuitive
- No regressions to existing session file functionality

## Estimated Time

- Phase 1 (Frontend tabs): 1 day
- Phase 2 (Load to session): 0.5 days
- Phase 3 (Polish): 0.5 days
- **Total: 2 days**

## Future Enhancements

- Pagination for large file lists
- Bulk delete
- File preview modal
- User-defined tags/labels
- Storage quota display
- Auto-cleanup of old files
