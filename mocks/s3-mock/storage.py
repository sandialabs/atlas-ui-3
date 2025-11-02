import os
import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Tuple, Optional, List

HTTP_DATE_FMT = "%a, %d %b %Y %H:%M:%S GMT"


def ensure_bucket(root: Path, bucket: str) -> Path:
    bucket_path = root / bucket
    bucket_path.mkdir(parents=True, exist_ok=True)
    return bucket_path


def object_paths(bucket_root: Path, key: str) -> Tuple[Path, Path]:
    # Sanitize key to prevent traversal
    key = key.lstrip("/")
    safe_parts = []
    for part in key.split("/"):
        if part in ("..", ""):
            continue
        safe_parts.append(part)
    safe_key = "/".join(safe_parts)
    obj_path = bucket_root / safe_key
    meta_path = bucket_root / f"{safe_key}.meta.json"
    obj_path.parent.mkdir(parents=True, exist_ok=True)
    return obj_path, meta_path


def calc_etag(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def httpdate(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime(HTTP_DATE_FMT)


def iso8601(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def save_object(bucket_root: Path, key: str, data: bytes, content_type: str, metadata: Dict[str, str], tags: Dict[str, str]) -> Dict[str, str]:
    obj_path, meta_path = object_paths(bucket_root, key)
    tmp_path = obj_path.with_suffix(obj_path.suffix + ".tmp")
    # Atomic write
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, obj_path)
    etag = calc_etag(data)
    meta = {
        "content_type": content_type or "application/octet-stream",
        "metadata": metadata or {},
        "tags": tags or {},
        "etag": etag,
        "last_modified": httpdate(os.path.getmtime(obj_path)),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)
    return meta


def load_meta(bucket_root: Path, key: str) -> Optional[Dict[str, str]]:
    _, meta_path = object_paths(bucket_root, key)
    if not meta_path.exists():
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def load_object(bucket_root: Path, key: str) -> Optional[Tuple[bytes, Dict[str, str]]]:
    obj_path, _ = object_paths(bucket_root, key)
    if not obj_path.exists():
        return None
    with open(obj_path, "rb") as f:
        data = f.read()
    meta = load_meta(bucket_root, key) or {}
    # Refresh last_modified if needed
    meta["last_modified"] = httpdate(os.path.getmtime(obj_path))
    return data, meta


def delete_object(bucket_root: Path, key: str) -> bool:
    obj_path, meta_path = object_paths(bucket_root, key)
    deleted = False
    if obj_path.exists():
        obj_path.unlink()
        deleted = True
    if meta_path.exists():
        meta_path.unlink()
    return deleted


def list_objects(bucket_root: Path, prefix: str = "", max_keys: int = 100) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    base = bucket_root
    for root, _, files in os.walk(base):
        for name in files:
            if name.endswith(".meta.json"):
                continue
            rel_path = Path(root).joinpath(name).relative_to(base)
            key = str(rel_path).replace(os.sep, "/")
            if not key.startswith(prefix):
                continue
            obj_path = base / rel_path
            size = obj_path.stat().st_size
            meta = load_meta(bucket_root, key) or {}
            items.append({
                "Key": key,
                "Size": size,
                "ETag": meta.get("etag") or calc_etag(open(obj_path, "rb").read()),
                "LastModified": iso8601(obj_path.stat().st_mtime),
            })
            if len(items) >= max_keys:
                return items
    # Sort by last modified desc to mimic S3 behavior often seen by clients
    items.sort(key=lambda x: x["LastModified"], reverse=True)
    return items


def set_tags(bucket_root: Path, key: str, tags: Dict[str, str]) -> None:
    meta = load_meta(bucket_root, key) or {"metadata": {}, "content_type": "application/octet-stream", "etag": ""}
    meta["tags"] = tags
    _, meta_path = object_paths(bucket_root, key)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)


def get_tags(bucket_root: Path, key: str) -> Dict[str, str]:
    meta = load_meta(bucket_root, key) or {}
    return meta.get("tags", {})
