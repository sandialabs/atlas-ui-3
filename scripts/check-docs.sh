#!/bin/bash
# check-docs.sh - Documentation structure linter for docs/
#
# Enforces that the docs tree stays discoverable and internally consistent:
#   1. Every Markdown doc is reachable from an index (a README.md) so nothing
#      rots in an orphaned corner of the tree.
#   2. Every relative link inside an index (and other docs) resolves to a file
#      that actually exists - no dead links.
#   3. The docs/ root holds only README.md; every other doc lives in a category.
#
# Exit code 0 = clean, 1 = problems found. Run from anywhere.
#
# Usage:
#   bash scripts/check-docs.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOCS_DIR="${PROJECT_ROOT}/docs"

if [ ! -d "${DOCS_DIR}" ]; then
    echo "Error: docs directory not found at ${DOCS_DIR}" >&2
    exit 1
fi

python3 - "${DOCS_DIR}" <<'PY'
import os
import re
import sys
from urllib.parse import urldefrag, unquote

docs_dir = os.path.abspath(sys.argv[1])

# Markdown inline links: [text](target) and bare image/link targets.
LINK_RE = re.compile(r'\]\(\s*(<[^>]+>|[^)\s]+)')

errors = []
warnings = []

md_files = []
all_files = set()
for root, dirs, files in os.walk(docs_dir):
    dirs[:] = [d for d in dirs if d not in {'__pycache__', '.git'}]
    for f in files:
        full = os.path.join(root, f)
        all_files.add(os.path.abspath(full))
        if f.lower().endswith('.md'):
            md_files.append(os.path.abspath(full))

readmes = [f for f in md_files if os.path.basename(f).lower() == 'readme.md']
non_readme = [f for f in md_files if os.path.basename(f).lower() != 'readme.md']

def rel(p):
    return os.path.relpath(p, docs_dir)

def extract_targets(md_path):
    """Return resolved absolute paths of local relative links found in a doc."""
    targets = []
    try:
        text = open(md_path, encoding='utf-8').read()
    except (OSError, UnicodeDecodeError) as e:
        warnings.append(f"could not read {rel(md_path)}: {e}")
        return targets
    for m in LINK_RE.finditer(text):
        raw = m.group(1).strip()
        if raw.startswith('<') and raw.endswith('>'):
            raw = raw[1:-1].strip()
        target, _frag = urldefrag(raw)
        if not target:
            continue
        # Skip external / absolute / mailto links.
        if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', target):
            continue
        if target.startswith('//') or target.startswith('#'):
            continue
        target = unquote(target)
        resolved = os.path.normpath(os.path.join(os.path.dirname(md_path), target))
        targets.append((raw, resolved))
    return targets

# --- Check 1 + 2: collect index references, flag broken links everywhere ----
referenced = set()
for md in md_files:
    is_index = os.path.basename(md).lower() == 'readme.md'
    for raw, resolved in extract_targets(md):
        if is_index and resolved.lower().endswith('.md'):
            referenced.add(resolved)
        # Broken-link check for any local link that points inside docs/.
        if resolved.startswith(docs_dir + os.sep) or resolved == docs_dir:
            if not os.path.exists(resolved):
                errors.append(
                    f"broken link in {rel(md)} -> {raw} "
                    f"(resolves to {rel(resolved)}, which does not exist)"
                )

# --- Check 1: orphan docs (not linked from any index) ----------------------
for md in sorted(non_readme):
    if md not in referenced:
        errors.append(
            f"orphan doc {rel(md)} is not linked from any README.md index"
        )

# --- Check 3: docs/ root holds only README.md ------------------------------
for md in non_readme:
    if os.path.dirname(md) == docs_dir:
        errors.append(
            f"{rel(md)} sits at docs/ root; move it into a category subdirectory"
        )

# --- Report ----------------------------------------------------------------
if warnings:
    print("Warnings:")
    for w in warnings:
        print(f"  - {w}")
    print()

if errors:
    print(f"check-docs: FAILED with {len(errors)} problem(s):")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)

print(f"check-docs: OK ({len(md_files)} markdown files, "
      f"{len(readmes)} indexes, no orphans or broken links)")
PY
