# Adding Custom Canvas Renderers

The canvas panel displays tool-generated files (PDFs, images, HTML, iframes). To add support for new file types (e.g., `.stl`, `.obj`, `.ipynb`):

## Canvas Architecture Flow

1. Backend tool returns artifacts and optional `display` hints → stored in S3 → sends `canvas_files` WebSocket message
2. Frontend receives file metadata (filename, s3_key, type, viewer_hint/display type)
3. Frontend fetches file content from `/api/files/download/{s3_key}` when needed
4. `CanvasPanel` renders based on file type and viewer configuration

## Built-in Viewers and Iframe Support

The canvas supports several built-in viewer types, selected via the artifact `viewer` field or display configuration:

- `html`: Render HTML content in an isolated, sanitized frame
- `image`: Display images such as PNG/JPEG
- `pdf`: Render PDF documents
- `iframe`: Embed external content from a URL

For iframe-based content, there are two primary patterns:

1. **Direct iframe via `display`** – the tool sets `display.type = "iframe"` and provides a `url`, `title`, and optional `sandbox` attributes.
2. **HTML artifact with embedded `<iframe>`** – the tool returns a standard HTML artifact (with `viewer: "html"`) that includes one or more `<iframe>` elements in its content.

In both cases, the frontend enforces a strict allowlist of iframe attributes (`src`, `sandbox`, `allow`, `allowfullscreen`, `frameborder`, `scrolling`) and sanitizes all HTML using DOMPurify before rendering.

## Steps to Add a New Type

**1. Extend type detection** in `frontend/src/hooks/chat/useFiles.js`:
```javascript
function getFileType(filename) {
  const extension = filename.toLowerCase().split('.').pop()
  if (['stl', 'obj', 'gltf'].includes(extension)) return '3d-model'
  // ... existing types
}
```

**2. Install any required viewer libraries:**
```bash
cd frontend
npm install three @react-three/fiber @react-three/drei
```

**3. Add rendering case** in `frontend/src/components/CanvasPanel.jsx` (around line 211):
```javascript
case '3d-model':
  return (
    <div className="p-4 h-full">
      <STLViewer url={currentFileContent.url} filename={currentFileContent.file.filename} />
    </div>
  );
```

**4. Create the viewer component** (e.g., `frontend/src/components/STLViewer.jsx`).

## Backend Considerations

No backend changes needed. Tools just return artifacts with proper filenames:

```python
return {
    "results": {"summary": "Generated 3D model"},
    "artifacts": [{
        "name": "model.stl",
        "b64": base64.b64encode(stl_bytes).decode(),
        "mime": "model/stl"
    }]
}
```

The `ChatService` automatically processes artifacts, uploads to S3, and sends canvas notifications.
