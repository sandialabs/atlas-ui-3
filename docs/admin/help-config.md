# Customizing the Help Page

Last updated: 2026-10-21

The Help page shows Markdown. By default that Markdown is a local file
(`help.md`). An operator can instead point Atlas at a documentation site they
host themselves, so the documentation can be updated without rebuilding or
redeploying Atlas.

Three display modes, selected with `HELP_DISPLAY_MODE`:

| Mode              | What the Help button does                                    | What Atlas fetches             |
|-------------------|--------------------------------------------------------------|---------------------------------|
| `local` (default) | Opens the in-app Help page with the local `help.md`          | Nothing (the local file)        |
| `remote_markdown` | Opens the in-app Help page with Markdown from the hosted site | The document, server-side       |
| `external_link`   | Opens the documentation site in a new browser tab             | Nothing                         |

`local` is the default, so an existing installation is unchanged until an
operator asks for something else.

## Configuration

```env
# Render Markdown that is hosted somewhere else (issue #1107).
HELP_DISPLAY_MODE=remote_markdown
HELP_CONTENT_URL=https://docs.example.org/atlas/help.md
```

Or, to send users to the documentation site itself:

```env
HELP_DISPLAY_MODE=external_link
HELP_CONTENT_URL=https://docs.example.org/atlas/
```

| Variable                         | Default | Meaning                                                                                |
|----------------------------------|---------|----------------------------------------------------------------------------------------|
| `HELP_DISPLAY_MODE`              | `local` | `local`, `remote_markdown`, or `external_link`                                          |
| `HELP_CONTENT_URL`               | *(empty)* | The documentation URL. Required by the two external modes                               |
| `HELP_ALLOWED_HOSTS`             | *(empty)* | Extra `host` or `host:port` entries that may serve Help images and same-site redirects |
| `HELP_REMOTE_CACHE_TTL_SECONDS`  | `300`   | How long a fetched document (and its images) is reused before it is fetched again        |
| `HELP_REMOTE_TIMEOUT_SECONDS`    | `10.0`  | Timeout for each request to the documentation host                                       |
| `HELP_REMOTE_MAX_BYTES`          | `1048576` | Maximum size of a fetched document                                                     |
| `HELP_REMOTE_MAX_IMAGE_BYTES`    | `5242880` | Maximum size of a fetched image                                                        |

The URL must be an absolute `https` URL (plain `http` is accepted only for
loopback hosts such as `localhost`, which is what a local test setup uses).
Credentials embedded in the URL are refused.

### When the configuration is wrong

A mode that names external documentation but has no usable URL -- an empty
URL, a URL without a scheme, a plain-`http` URL for a remote host, an
unknown mode name -- is a configuration error. Atlas does not start with a
broken Help button:

* the problem is logged once at startup as an error,
* the effective mode falls back to `local`, so the Help page keeps working
  with the local document,
* the reason is shown on the Help page and in the Admin Dashboard, and it is
  returned by `GET /api/help` and `GET /admin/help-config`.

## Remote Markdown

The document is fetched by the Atlas backend, not by the browser. That avoids
cross-origin trouble, keeps the reader's IP address away from the
documentation host, and lets the response be cached.

* **Caching.** A fetched document is reused for `HELP_REMOTE_CACHE_TTL_SECONDS`
  (5 minutes by default). After that the server revalidates it with a
  conditional request (`ETag` / `Last-Modified`) and only transfers the body
  when it actually changed. A changed document is therefore visible without a
  redeploy.
* **Failure.** If the documentation host is unreachable, the last known good
  copy is served (the Help page says it is serving a stale copy). If there is
  no cached copy, the local `help.md` is shown instead, with the reason.
* **Images.** Image references in the hosted document are resolved against the
  document URL, so a relative reference (`images/diagram.png`) and a
  root-relative one (`/images/diagram.png`) both point at the documentation
  host and are *not* treated as ATLAS-local `/help-images/` paths. ATLAS
  serves them through `/api/help/image`, so the browser never contacts the
  documentation host directly and the default content security policy
  (`img-src 'self'`) keeps working. Only the configured documentation host
  (and any host listed in `HELP_ALLOWED_HOSTS`) may be fetched that way; an
  absolute image URL pointing somewhere else is left alone and is blocked by
  the default policy unless the operator widens `SECURITY_CSP_VALUE`.
* **Sanitization.** The document is rendered with `marked` + `DOMPurify`, the
  same pipeline used for chat messages, so a hosted document cannot inject
  scripts into the page.

## External link

In `external_link` mode the Help button is a link to the configured URL: it
opens in a new tab (`target="_blank"`, `rel="noopener noreferrer"`), leaves
the current conversation open, and is keyboard accessible. Atlas fetches
nothing and renders nothing -- there is no request to the documentation host
at all.

## Local mode

Local mode is the pre-existing behavior and is unchanged.

The Help page content is authored in Markdown. You can customize it by creating a `help.md` file.

*   **Location**: Place your custom file at `config/help.md` (user override). The shipped default lives at `atlas/config/help.md`.
*   **Override env var**: `HELP_CONFIG_FILE=help.md` (set in `.env` to point at a different filename).
*   **Legacy**: If `help.md` is not found, the backend falls back to the legacy `help-config.json` (structured JSON) for backward compatibility. New deployments should use Markdown.

The file is rendered with `marked` + `DOMPurify` on the frontend, the same pipeline used for chat messages, so standard GitHub-flavored Markdown (headings, lists, tables, code blocks, blockquotes) is supported.

### Embedding images

Drop image files into `config/help-images/` (user override) or `atlas/config/help-images/` (shipped default) and reference them in `help.md` as:

```markdown
![alt text](/help-images/filename.png)
```

The backend mounts `/help-images/*` and searches the user-override directory first, then falls back to the shipped one. Path traversal is blocked.

### Example `help.md`

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

- `GET /admin/help-config` — returns the current Markdown content, plus the
  display mode and the configured URL
- `PUT /admin/help-config` — writes new Markdown content (size-limited)

When the deployment uses an external mode, the Admin Dashboard says so and
warns that the local editor no longer controls what users see. The editor
still works (the file is still written) -- it simply is not the document the
Help page shows.
