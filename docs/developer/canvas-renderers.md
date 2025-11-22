# Adding Custom Canvas Renderers

The canvas panel displays tool-generated files (PDFs, images, HTML). To add support for new file types (e.g., `.stl`, `.obj`, `.ipynb`):

## Canvas Architecture Flow

1. Backend tool returns artifacts → stored in S3 → sends `canvas_files` WebSocket message
2. Frontend receives file metadata (filename, s3_key, type)
3. Frontend fetches file content from `/api/files/download/{s3_key}`
4. `CanvasPanel` renders based on file type

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
