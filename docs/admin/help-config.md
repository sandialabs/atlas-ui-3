# Customizing the Help Modal

Last updated: 2026-01-19

You can customize the content that appears in the "Help" or "About" modal in the UI by creating a `help-config.json` file.

*   **Location**: Place your custom file at `config/overrides/help-config.json`.

The file consists of a title and a list of sections, each with a title and content that can include markdown for formatting.

## Example `help-config.json`

```json
{
  "title": "About Our Chat Application",
  "sections": [
    {
      "title": "Welcome",
      "content": "This is a custom chat application for our organization. It provides access to internal tools and data sources."
    },
    {
      "title": "Available Tools",
      "content": "You can use tools for:\n\n*   Querying databases\n*   Analyzing documents\n*   Searching our internal knowledge base"
    },
    {
      "title": "Support",
      "content": "For questions or issues, please contact the support team at [support@example.com](mailto:support@example.com)."
    }
  ]
}
```
