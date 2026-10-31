"""Select a page of stored files before the expensive per-object reads.

Listing keys under a user's prefix is cheap. Reading each object's tags and
metadata is not, and that cost is what made ``GET /api/files?limit=1000``
slow. Callers hand every listed object here, and only the returned page is
hydrated.

The extension sets match ``frontend/src/utils/fileLibraryModel.js``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

FILE_CATEGORIES = ("image", "code", "data", "document", "other")

_MAX_PAGE_SIZE = 1000

_CATEGORY_EXTENSIONS = {
    "image": frozenset({"jpg", "jpeg", "png", "gif", "svg", "webp"}),
    "code": frozenset({"js", "jsx", "ts", "tsx", "py", "java", "cpp", "c", "go", "rs"}),
    "data": frozenset({"json", "csv", "xlsx", "xls"}),
    "document": frozenset({"pdf", "doc", "docx", "txt", "md"}),
}

# Upload keys look like ``{unix_seconds}_{8 hex}_{safe_filename}``.
_KEY_FILENAME = re.compile(r"^\d{9,}_[0-9a-fA-F]{8}_(.+)$")


def user_prefix(user_email: str, file_type: Optional[str] = None) -> str:
    """Return the object-key prefix for one user's library, or one source folder."""
    if file_type == "tool":
        return f"users/{user_email}/generated/"
    if file_type == "user":
        return f"users/{user_email}/uploads/"
    return f"users/{user_email}/"


def source_for_key(key: str) -> str:
    """Return ``tool`` for generated objects and ``user`` for everything else."""
    if "/generated/" in (key or ""):
        return "tool"
    return "user"


def filename_from_key(key: str) -> str:
    """Return the stored file name encoded in an object key."""
    leaf = (key or "").rsplit("/", 1)[-1]
    match = _KEY_FILENAME.match(leaf)
    if match:
        return match.group(1)
    return leaf


def category_for_filename(filename: str) -> str:
    """Return the explorer folder for a file name (``image``, ``code``, ...)."""
    leaf = filename or ""
    extension = leaf.rsplit(".", 1)[-1].lower() if "." in leaf else ""
    for category, extensions in _CATEGORY_EXTENSIONS.items():
        if extension in extensions:
            return category
    return "other"


def tags_with_source(tags: Optional[Dict[str, str]], key: str) -> Dict[str, str]:
    """Ensure ``source`` is set, deriving it from the key when tagging is empty.

    ``s3:GetObjectTagging`` is optional. Without it the tag set comes back
    empty and the library cannot tell uploads from generated files, even
    though the key prefix already records that.
    """
    merged = dict(tags or {})
    if not merged.get("source"):
        merged["source"] = source_for_key(key)
    return merged


def _epoch(value: Any) -> Optional[float]:
    """Return a UTC epoch for a listing timestamp, or None when it is unusable."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.timestamp()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _matches(entry: Dict[str, Any], needle: str, category: Optional[str]) -> bool:
    key = entry.get("key") or ""
    leaf = key.rsplit("/", 1)[-1]
    stored_name = filename_from_key(key)
    if needle and needle not in leaf.lower() and needle not in stored_name.lower():
        return False
    if category and category != "all":
        if category_for_filename(stored_name) != category and category_for_filename(leaf) != category:
            return False
    return True


def select_file_page(
    entries: Sequence[Dict[str, Any]],
    *,
    limit: int = 100,
    offset: int = 0,
    query: Optional[str] = None,
    category: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], int]:
    """Return one newest-first page and the number of matches before paging.

    An entry with no usable timestamp sorts after every dated entry. ``limit``
    is capped so a caller cannot hydrate an unbounded page.
    """
    needle = (query or "").strip().lower()
    matched = [entry for entry in entries if _matches(entry, needle, category)]

    def sort_key(entry: Dict[str, Any]):
        stamp = _epoch(entry.get("last_modified"))
        unknown = stamp is None
        return (1 if unknown else 0, -(stamp or 0.0), entry.get("key") or "")

    matched.sort(key=sort_key)
    total = len(matched)
    try:
        page_size = int(limit)
    except (TypeError, ValueError):
        page_size = 100
    page_size = min(max(page_size, 0), _MAX_PAGE_SIZE)
    try:
        start = int(offset)
    except (TypeError, ValueError):
        start = 0
    if start < 0:
        start = 0
    return matched[start:start + page_size], total
