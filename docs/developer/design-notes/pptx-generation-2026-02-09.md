# PPTX Generation MCP Server

Last updated: 2026-02-09

The PPTX generator is a FastMCP server (`atlas/mcp/pptx_generator/main.py`) that converts markdown content into professional PowerPoint presentations with 16:9 aspect ratio.

## Tool: `markdown_to_pptx`

Accepts markdown content where `#` or `##` headers become slide titles, and content below each header becomes slide body text. Returns base64-encoded `.pptx` and `.html` artifacts.

**Parameters:**
- `markdown_content` (required) - Markdown with headers as slide breaks
- `file_name` (optional) - Output filename base (sanitized, default: "presentation")
- `image_filename` (optional) - Image file to embed on the first slide
- `image_data_base64` (optional) - Base64-encoded image data as fallback

## Layout Strategy (2026-02-09)

The generator uses a three-tier layout strategy to produce well-formatted slides:

1. **Custom template file** - If a `.pptx` template is found (via `PPTX_TEMPLATE_PATH` or search paths), it is loaded and the "Title and Content" layout is used. Header/footer bars are omitted since the template's slide master should define them.
2. **Built-in Office layouts** - If no template is found, a blank `Presentation()` is created and the built-in "Title and Content" layout is used with Sandia-style header/footer bars.
3. **Blank layout fallback** - If no "Title and Content" layout exists, falls back to layout index 6 (blank) with manually positioned title and content textboxes plus header/footer bars.

## Template Discovery

Templates are discovered in this order (first match wins):

1. `PPTX_TEMPLATE_PATH` environment variable (explicit path to a `.pptx` file)
2. `atlas/mcp/pptx_generator/template.pptx` (next to the script)
3. `atlas/config/pptx_template.pptx` (package config directory)
4. `config/pptx_template.pptx` (user config override directory)

## Template Requirements

A compatible template file must:
- Be a valid `.pptx` file
- Contain a slide layout named "Title and Content" (the standard Office layout name)
- Have placeholder index 0 for the title and placeholder index 1 for the body content

If the template lacks a "Title and Content" layout, the generator logs a warning with the available layout names and falls back to tier 2 (built-in layouts).

## Configuration

Add to `.env` (optional):
```bash
# Explicit path to a custom PowerPoint template
PPTX_TEMPLATE_PATH=/path/to/custom/template.pptx
```

## MCP Server Registration

Register in `mcp.json`:
```json
{
  "pptx_generator": {
    "command": "python",
    "args": ["atlas/mcp/pptx_generator/main.py"],
    "groups": ["default"]
  }
}
```
