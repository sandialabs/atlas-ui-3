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

# Pages hydrate their rows on a shared worker pool, and the page size is
# what bounds how much work one request queues there. Keep the cap close
# to what the explorer asks for (50) so no single request starves others.
_MAX_PAGE_SIZE = 200


class ListingTooLargeError(Exception):
    """Raised when a prefix listing exceeds the traversal safety cap.

    Failing is safer than answering with a partial listing presented as a
    complete page total or a complete set of statistics: the omitted keys
    can include the newest uploads, and paging cannot reach them.
    """

_CATEGORY_EXTENSIONS = {
    "image": frozenset({"jpg", "jpeg", "png", "gif", "svg", "webp"}),
    "code": frozenset({"js", "jsx", "ts", "tsx", "py", "java", "cpp", "c", "go", "rs"}),
    "data": frozenset({"json", "csv", "xlsx", "xls"}),
    "document": frozenset({"pdf", "doc", "docx", "txt", "md"}),
}

# Upload keys look like ``{unix_seconds}_{8 hex}_{safe_filename}``.
_KEY_FILENAME = re.compile(r"^\d{9,}_[0-9a-fA-F]{8}_(.+)$")
# Stored names replace spaces and other separators with "_". Folding both
# sides lets a search for "my report" match ``my_report.pdf`` without
# reading every object's metadata.
_SEARCH_FOLD = re.compile(r"[^a-z0-9]+")


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
    """Derive ``source`` from the key so the API agrees with folder selection.

    Folder membership is decided by the key prefix (``uploads/`` versus
    ``generated/``), because that is what the paged listing filters on. The
    stored ``source`` tag is informational and can carry any value, so the
    listing always overwrites it with the key-derived value rather than
    letting a stale tag contradict the folder the file was counted in.
    """
    merged = dict(tags or {})
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


def _fold_search(value: str) -> str:
    """Lowercase and drop separators so a spaced query matches a stored name."""
    return _SEARCH_FOLD.sub("", (value or "").lower())


def _matches(entry: Dict[str, Any], needle: str, category: Optional[str]) -> bool:
    key = entry.get("key") or ""
    leaf = key.rsplit("/", 1)[-1]
    stored_name = filename_from_key(key)
    if needle:
        # Search the stored file name only. The raw leaf would also match
        # the timestamp and UUID inside the key, which the browser filter
        # never sees, so those rows would fill a page and inflate the
        # total while the browser hides them.
        folded = _fold_search(needle)
        name = stored_name.lower()
        if needle not in name and not (folded and folded in _fold_search(stored_name)):
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
