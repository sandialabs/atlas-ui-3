# File Handling in Chat UI

This document explains how file uploads are handled in the chat interface, particularly how files are exposed to LLM context vs tool processing.

## Overview

When users upload files, the system applies intelligent filtering to determine which files should be:
- **LLM-visible**: Mentioned in the conversation context for the LLM to be aware of
- **Tool-only**: Available for tool processing but hidden from LLM context

## File Classification

### LLM-Visible Files
Small text files that the LLM can meaningfully understand:
- `.txt` - Plain text files (≤1KB)
- `.md` - Markdown files (≤1KB) 
- `.rst` - reStructuredText files (≤1KB)
- `.log` - Log files (≤1KB)

### Tool-Only Files
Files that require specialized processing and should not be exposed to LLM:

**Data Files** (require data processing tools):
- `.csv`, `.xlsx`, `.xls` - Spreadsheets
- `.json`, `.jsonl` - JSON data files

**Documents** (require document processing tools):
- `.pdf`, `.doc`, `.docx` - Documents
- `.ppt`, `.pptx` - Presentations

**Media Files** (require media processing tools):
- `.jpg`, `.jpeg`, `.png`, `.gif`, `.bmp`, `.tiff`, `.webp` - Images
- `.mp4`, `.avi`, `.mov`, `.wmv` - Videos
- `.mp3`, `.wav`, `.flac`, `.aac` - Audio

**Binary/Archive Files**:
- `.exe`, `.dll`, `.so`, `.app` - Executables
- `.zip`, `.tar`, `.gz`, `.rar`, `.7z` - Archives
- `.db`, `.sqlite`, `.sqlite3` - Databases

## Size Limits

- **LLM-visible files**: Maximum 1KB decoded size
- **All uploaded files**: Maximum 50MB per file

## Behavior

### What the LLM Sees
For LLM-visible files, the conversation context includes:
```
Files available for analysis:
- readme.txt (Text file, 0.5KB)
- notes.md (Text file, 0.3KB)

Note: 3 additional file(s) are available for tool processing only.
```

### What Tools See
All uploaded files are available to tools regardless of classification. Tools receive the full base64-encoded file content when they request it.

### Security Benefits

1. **No data leakage**: Large files with sensitive data don't appear in LLM context
2. **No base64 exposure**: File content never appears directly in conversations  
3. **Appropriate processing**: Binary/data files are only processed by specialized tools
4. **Performance**: Large files don't bloat conversation context

## Configuration

File policies are defined in `backend/file_config.py`:

```python
# File types that should only be processed by tools
TOOL_ONLY_FILE_TYPES = {'.csv', '.pdf', '.jpg', ...}

# File types that can be mentioned to LLM  
LLM_VISIBLE_FILE_TYPES = {'.txt', '.md', '.rst', '.log'}

# Size limit for LLM-visible files
MAX_LLM_VISIBLE_FILE_SIZE = 1 * 1024  # 1KB
```

## Example Scenarios

### Scenario 1: Data Analysis
**Files uploaded**: `sales_data.csv` (500KB), `readme.txt` (0.5KB)
- **LLM sees**: Only `readme.txt` mentioned in context
- **Tools access**: Both files available for analysis tools
- **Result**: LLM can understand the context from readme, tools can process the CSV data

### Scenario 2: Document Processing  
**Files uploaded**: `report.pdf` (2MB), `presentation.pptx` (5MB)
- **LLM sees**: Only a note that files are available for tool processing
- **Tools access**: Both files available for document processing tools
- **Result**: LLM doesn't get overwhelmed with binary content, tools handle the documents

### Scenario 3: Text Files Only
**Files uploaded**: `notes.md` (0.3KB), `todo.txt` (0.2KB)  
- **LLM sees**: Both files mentioned with categories and sizes
- **Tools access**: Both files available if needed
- **Result**: LLM has full context about the text files for better assistance