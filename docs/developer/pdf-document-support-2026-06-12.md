# Native PDF Document Support

**PR**: #648
**Date**: 2026-06-12

## Overview

Models configured with `supports_pdf: true` receive uploaded PDF files as inline
base64 **document content blocks** in the user message, rather than having the
PDF text-extracted into the plain-text files manifest. This lets the LLM read the
PDF directly (including layout and embedded visuals, subject to the provider
notes below).

This mirrors the vision image support flow (`supports_vision`). See
[vision-image-support-2026-03-23.md](./vision-image-support-2026-03-23.md).

## Configuration

```yaml
models:
  claude-sonnet-bedrock:
    model_url: "bedrock/anthropic.claude-sonnet-4-..."
    model_name: "claude-sonnet"
    compliance_level: "Internal"
    supports_pdf: true
```

The field defaults to `false` when omitted. `/api/config` and
`/api/config/shell` expose `supports_pdf` per model so the frontend can adapt.

## How It Works

1. **Config** (`modules/config/models.py`): `ModelConfig.supports_pdf` parsed from YAML.
2. **API** (`routes/config_routes.py`): `supports_pdf` added to both config endpoints.
3. **File processing** (`application/chat/utilities/file_processor.py`): when
   `model_supports_pdf=True`, `handle_session_files` stores the raw `pdf_b64` and
   `pdf_mime_type` on eligible (`application/pdf`) file refs. It **also extracts
   the PDF text as a durable fallback** (forcing a full extraction even when the
   upload's `extractMode` is `none`). That extracted text is excluded from the
   manifest on the turn the PDF is sent natively — so it does not duplicate
   tokens — but it keeps the content reachable when the native document block is
   later dropped (see *Text Fallback* below). Stale PDF data from prior turns is
   cleared at the start of each turn.
4. **Message building** (`preprocessors/message_builder.py`):
   `build_messages(model_supports_pdf=True)` attaches the PDFs as `file` content
   blocks on the last user message and excludes them from the files manifest.
5. **Orchestrator** (`orchestrator.py`): `_model_supports_pdf()` reads the config
   flag and threads it into the file processor and message builder.

## Message Format

PDFs use the LiteLLM OpenAI-spec `file` content block, which LiteLLM translates
to each provider's native document format (a Bedrock Converse `document` block
for Claude). Documents are placed before the text per Anthropic's guidance:

```json
{
  "role": "user",
  "content": [
    {
      "type": "file",
      "file": {
        "file_data": "data:application/pdf;base64,JVBERi0...",
        "format": "application/pdf"
      }
    },
    {"type": "text", "text": "Summarize this document."}
  ]
}
```

When a turn includes both images and PDFs, the content array is ordered
documents, then text, then images.

## Size, Page, Count, and Payload Limits

| Limit | Value | Constant |
|-------|-------|----------|
| Max PDF size (per document) | 20 MB base64 (≈15 MB raw) | `_MAX_PDF_B64_BYTES` |
| Max pages per PDF | 100 | `_MAX_PDF_PAGES` |
| Max PDFs per request | 5 | `_MAX_PDF_DOCUMENTS_PER_REQUEST` |
| Max aggregate inline payload | 18 MB base64 | `_MAX_TOTAL_INLINE_B64_BYTES` |

Guards are evaluated cheap-first: the per-document size check runs before the
`pypdf` page count (which decodes and parses the file, so it runs off the asyncio
event loop via `asyncio.to_thread`). Page count is best-effort; if it can't be
read, the page guard is skipped.

The **aggregate** guard exists because several mid-sized PDFs can each pass the
per-document cap yet still sum past Bedrock's hard ~20 MB *total request* limit.
After all uploads are processed, PDFs are kept oldest-first until either the
5-document count or the 18 MB aggregate budget (inline PDFs **and** vision images)
is reached; the rest are demoted to their text fallback with a user warning. The
budget is set below 20 MB to leave headroom for the system prompt and history.

PDFs over the per-document size or page limit are not sent natively either. What
"fall back" means depends on configuration — see *Text Fallback* below; it is not
unconditionally a full text extraction.

## Text Fallback

Because the native document block is dropped in three situations —

1. a follow-up turn (stale `pdf_b64`/`pdf_mime_type` are cleared each turn),
2. count demotion (PDF #6+), and
3. aggregate-payload demotion —

natively-eligible PDFs always have their text extracted up front so the content
survives in the files manifest. The fallback's quality depends on configuration:

- **Extraction enabled** (`FEATURE_FILE_CONTENT_EXTRACTION_ENABLED=true` and a
  PDF extractor configured): the manifest carries the extracted text.
- **Extraction disabled or unavailable**: there is no text to fall back to, so a
  dropped PDF becomes a **name-only** manifest entry. The user-facing warning
  reflects this ("a file reference (no extractable text)").

PDFs over the per-document size/page limit follow the same path: extracted text
when extraction is available, name-only otherwise.

### Why 20 MB and not larger

AWS Bedrock enforces a **hard 20 MB limit on the entire request payload** (the
base64 PDF plus system prompt, history, and everything else). Base64 inflates raw
bytes by ~33%, so the largest raw PDF that fits inline is roughly 14–15 MB, and
that leaves little headroom for conversation history. The legacy 4.5 MB
per-document Bedrock cap does **not** apply to PDFs on Claude 4+, so the request
payload limit — not a per-document cap — is the binding constraint. Going beyond
this would require the Anthropic Files API (`file_id` references), which is not
available on Bedrock (Bedrock only accepts base64 document sources).

## Provider Note: Bedrock Converse and Citations

On Bedrock's Converse API (which LiteLLM uses), Claude only performs **full
visual PDF understanding** (charts, scanned pages, layout) when **citations are
enabled** on the document block. Without citations, Converse falls back to basic
text extraction only. The portable LiteLLM `file` block used here does not
currently expose a citations toggle, so on Bedrock this feature delivers
text-extraction-mode document reading. If guaranteed visual analysis is required,
the follow-up is to emit a provider-native document block with
`citations: {enabled: true}` (or use the InvokeModel path).

## References

- [Anthropic — PDF support](https://platform.claude.com/docs/en/build-with-claude/pdf-support)
- [Amazon Bedrock — API restrictions](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-api-restrictions.html)
- [LiteLLM — Using PDF Input](https://docs.litellm.ai/docs/completion/document_understanding)
