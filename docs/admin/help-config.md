# Customizing the Help Page

Last updated: 2026-04-04

The Help page content is authored in Markdown. You can customize it by creating a `help.md` file.

*   **Location**: Place your custom file at `config/help.md` (user override). The shipped default lives at `atlas/config/help.md`.
*   **Override env var**: `HELP_CONFIG_FILE=help.md` (set in `.env` to point at a different filename).
*   **Legacy**: If `help.md` is not found, the backend falls back to the legacy `help-config.json` (structured JSON) for backward compatibility. New deployments should use Markdown.

The file is rendered with `marked` + `DOMPurify` on the frontend, the same pipeline used for chat messages, so standard GitHub-flavored Markdown (headings, lists, tables, code blocks, blockquotes) is supported.

## Embedding images

Drop image files into `config/help-images/` (user override) or `atlas/config/help-images/` (shipped default) and reference them in `help.md` as:

```markdown
![alt text](/help-images/filename.png)
```

The backend mounts `/help-images/*` and searches the user-override directory first, then falls back to the shipped one. Path traversal is blocked.

## Example `help.md`

```markdown
# About Our Chat Application

## Welcome

This is a custom chat application for our organization. It provides access to
internal tools and data sources.

## Available Tools

- Querying databases
- Analyzing documents
- Searching our internal knowledge base

## Support

For questions or issues, contact [support@example.com](mailto:support@example.com).
```

## Admin editing

Admins can edit help content in-app via the Admin Dashboard:

- `GET /admin/help-config` — returns the current Markdown content
- `PUT /admin/help-config` — writes new Markdown content (size-limited)
