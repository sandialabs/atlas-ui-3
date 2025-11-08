# Endpoint Summary

Generated: 2025-08-09
Branch: feature/s3-file-storage

## 1. Frontend-Used Endpoints
(HTTP method inferred from usage / backend definition.)

- GET /api/config
- GET /api/banners
- WebSocket /ws
- GET /api/files
- GET /api/files/{file_key}
- DELETE /api/files/{file_key}
- GET /api/users/{user_email}/files/stats
- GET /admin/   (access check; overlaps with @app.get("/admin") and admin_router.get("/"))
- GET /admin/system-status
- GET /admin/banners
- POST /admin/banners
- GET /admin/mcp-config
- POST /admin/mcp-config
- GET /admin/llm-config
- POST /admin/llm-config
- GET /admin/help-config
- POST /admin/help-config
- GET /admin/logs (with query param lines=500)
- GET /admin/logs/viewer (with query param lines=500)
- GET /admin/logs/download
- GET /admin/mcp-health
- POST /admin/trigger-health-check
- POST /admin/reload-config
- GET /api/feedback (admin view, query param limit=100)
- POST /api/feedback
- (Dynamic) POST /admin/{currentEndpoint} (AdminDashboard saveConfig consolidates to one of the explicit POST admin endpoints above)

## 2. Frontend Call With No Matching Backend Route

- GET /api/files/download/{s3_key}
  - Used in `CanvasPanel.jsx` to fetch file contents.
  - Backend currently exposes only GET /api/files/{file_key} returning JSON (base64 content) or could add a dedicated binary download route.

## 3. Backend Routes Not (Yet) Referenced in Frontend (Scanned Components)

- POST /api/files  (file upload; no fetch found in inspected components—upload UI may be elsewhere or pending)
- GET /api/sessions
- GET /api/llm-health
- GET /api/debug/servers
- GET /healthz
- GET /api/files/health
- DELETE /api/feedback/{feedback_id}
- GET /api/feedback/stats

## 4. Notes & Recommendations

- Canvas download mismatch: Either (a) change CanvasPanel to call GET /api/files/{file_key} and adapt to base64/text vs blob, or (b) implement a new raw download endpoint `/api/files/download/{file_key}` returning appropriate binary/media type.
- Consider consolidating duplicate admin root handlers (@app.get("/admin") and admin_router.get("/")) if not intentional.
- If uploads are intended in current UI, add frontend fetch POST /api/files to support file selection & base64 encoding; otherwise remove dead code/comments referencing uploads.
- Security: Ensure admin endpoints are consistently protected (they use `require_admin` dependency—confirmed) and that WebSocket auth header is enforced in non-debug environments.

## 5. Method Source Verification

Backend files reviewed:
- backend/main.py
- backend/admin_routes.py
- backend/feedback_routes.py
- backend/files_routes.py

Frontend files scanned for usage (fetch/WebSocket):
- frontend/src/contexts/ChatContext.jsx
- frontend/src/contexts/WSContext.jsx
- frontend/src/components/AdminDashboard.jsx
- frontend/src/components/BannerPanel.jsx
- frontend/src/components/ChatArea.jsx
- frontend/src/components/FilesPage.jsx
- frontend/src/components/CanvasPanel.jsx
- frontend/src/components/FeedbackButton.jsx
- frontend/src/components/HelpPage.jsx

Search patterns used: fetch(, '/api/', '/admin/', /ws

## 6. Next Possible Improvements

- Add automated script to diff declared backend routes vs. frontend usages to flag drift.
- Generate OpenAPI spec section in docs and link each frontend call to spec.
- Add tests to ensure /api/files/download mismatch gets resolved (either removing the call or adding route).
