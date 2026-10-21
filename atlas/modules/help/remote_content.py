"""Fetch remotely hosted Help Markdown and the images it references (issue #1107).

Everything here is deliberately narrow. The deployment configures exactly one
documentation URL, and this module only ever talks to that URL's origin (plus
any host the operator lists in ``HELP_ALLOWED_HOSTS``). There is no
unrestricted URL fetch: a request for anything else is refused before a
socket is opened.

The document is fetched server-side so the browser never makes a cross-origin
request (no CORS trouble, and the client IP is not handed to the documentation
host), and so the response can be cached and reused. A conditional request
(``If-None-Match`` / ``If-Modified-Since``) keeps the cache cheap.

Failure handling: when the host is unreachable the last known good document
is served, marked stale. The caller decides what to do when there is no cached
copy to fall back on.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from urllib.parse import quote, urljoin, urlparse

import httpx

from atlas.modules.help.help_config import HelpConfig

logger = logging.getLogger(__name__)

# Where the rewritten image references point. The browser asks Atlas for the
# image, and Atlas asks the documentation host -- so the browser never talks
# to the external site directly and the default CSP (img-src 'self') keeps
# working.
IMAGE_PROXY_PATH = "/api/help/image"

# Redirects are followed by hand so every hop can be checked against the
# allowed hosts before it is requested.
MAX_REDIRECTS = 3

# How many documents to keep. A deployment configures one documentation URL;
# this is only a safety net for a process that has seen several.
MAX_CACHED_DOCUMENTS = 4

REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class HelpFetchError(Exception):
    """A hosted-documentation request could not be completed."""


class HelpPolicyError(HelpFetchError):
    """A request was refused because it fell outside the allowed hosts."""


@dataclass
class RemoteHelpResult:
    """The outcome of a remote Help fetch."""

    content: str = ""
    source_url: str = ""
    cached: bool = False
    stale: bool = False
    fetched_at: Optional[float] = None
    error: Optional[str] = None


@dataclass
class _CachedDocument:
    content: str
    etag: Optional[str]
    last_modified: Optional[str]
    expires_at: float
    fetched_at: float


# The caches live in the process: a Help document changes on a human timescale,
# and a shared cache would be a lot of machinery for one document.
_document_cache: Dict[str, _CachedDocument] = {}
_image_cache: Dict[str, Tuple[float, bytes, str]] = {}
_cache_lock = threading.Lock()


def clear_caches() -> None:
    """Drop every cached document and image (used by tests and on config change)."""
    with _cache_lock:
        _document_cache.clear()
        _image_cache.clear()


def _prune_expired_images(now: float) -> None:
    """Drop the cached images that are past their TTL.

    A document is kept past its TTL on purpose: it is the last known good
    copy, and it is what an outage falls back to. An image has no such
    role, and a documentation site whose image URLs change on every edit
    would otherwise leave an entry behind each time.
    """
    with _cache_lock:
        for url, (expires_at, _body, _content_type) in list(_image_cache.items()):
            if expires_at <= now:
                del _image_cache[url]


def _trim_document_cache() -> None:
    """Keep the document cache small.

    A deployment configures one documentation URL, so this is a safety net
    for a long-running process that has seen several URLs: the oldest copies
    go, the most recent ones stay.
    """
    with _cache_lock:
        if len(_document_cache) <= MAX_CACHED_DOCUMENTS:
            return
        ordered = sorted(
            _document_cache.items(), key=lambda item: item[1].fetched_at, reverse=True
        )
        for url, _entry in ordered[MAX_CACHED_DOCUMENTS:]:
            del _document_cache[url]


def _origin(url: str) -> Tuple[str, str, int]:
    """``(scheme, host, port)`` of a URL, normalized for comparison."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return scheme, host, port


def _host_key(url: str) -> Tuple[str, int]:
    """The comparison key for an allowed host: ``host`` plus optional port."""
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port or (443 if scheme == "https" else 80)
    return host, port


def _is_allowed_url(url: str, config: HelpConfig) -> bool:
    """True when ``url`` may be fetched for this configuration.

    The configured documentation host is always allowed. Extra hosts must be
    listed by the operator, and every one of them must be reached over https
    (except loopback, for local development).
    """
    if not config.content_url:
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if parsed.username or parsed.password:
        return False
    if not parsed.hostname:
        return False

    key = _host_key(url)
    if key == _host_key(config.content_url):
        return True
    for host, port in config.allowed_hosts:
        if key[0] != host:
            continue
        if port is not None and key[1] != port:
            continue
        if parsed.scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
            return False
        return True
    return False


def _require_allowed_url(url: str, config: HelpConfig, what: str) -> None:
    if not _is_allowed_url(url, config):
        raise HelpPolicyError(
            f"refusing to fetch {what} from '{url}': only the configured documentation "
            "host is allowed"
        )


async def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    """Read a response body, refusing to buffer more than ``max_bytes``."""
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise HelpFetchError(f"response exceeded the {max_bytes} byte limit")
    return bytes(buffer)


async def _get(
    url: str,
    config: HelpConfig,
    headers: Optional[Dict[str, str]] = None,
    max_bytes: Optional[int] = None,
) -> Tuple[int, str, bytes, httpx.Headers]:
    """GET a URL, following only same-policy redirects.

    Returns ``(status, final_url, body, response_headers)``.
    """
    limit = config.max_bytes if max_bytes is None else max_bytes
    timeout = httpx.Timeout(config.timeout_seconds)
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            async with client.stream("GET", current, headers=headers or {}) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise HelpFetchError(
                            f"redirect from '{current}' had no Location header"
                        )
                    target = urljoin(current, location)
                    _require_allowed_url(target, config, "a redirect target")
                    current = target
                    continue
                body = b""
                if response.status_code == 200:
                    body = await _read_capped(response, limit)
                return response.status_code, current, body, response.headers
    raise HelpFetchError(f"too many redirects while fetching '{url}'")


async def fetch_remote_markdown(config: HelpConfig) -> RemoteHelpResult:
    """Fetch the hosted Help document, reusing a cached copy when allowed.

    The cache is refreshed with a conditional request once it is older than
    the configured TTL, so a changed document is picked up without a redeploy
    and an unchanged one costs a 304.
    """
    url = config.content_url
    if not url:
        return RemoteHelpResult(
            content="",
            error="No documentation URL is configured for the remote Help mode.",
        )

    now = time.monotonic()
    with _cache_lock:
        cached = _document_cache.get(url)
    if cached and cached.expires_at > now:
        return RemoteHelpResult(
            content=cached.content,
            source_url=url,
            cached=True,
            stale=False,
            fetched_at=cached.fetched_at,
        )

    request_headers: Dict[str, str] = {"Accept": "text/markdown, text/plain, */*"}
    if cached:
        if cached.etag:
            request_headers["If-None-Match"] = cached.etag
        if cached.last_modified:
            request_headers["If-Modified-Since"] = cached.last_modified

    try:
        status, final_url, body, response_headers = await _get(
            url, config, headers=request_headers
        )
    except HelpFetchError as exc:
        return _stale_fallback(url, cached, str(exc))
    except httpx.HTTPError as exc:
        return _stale_fallback(url, cached, f"could not reach the documentation host: {exc}")

    if status == 304 and cached:
        with _cache_lock:
            cached.expires_at = now + config.cache_ttl_seconds
            cached.fetched_at = now
        return RemoteHelpResult(
            content=cached.content,
            source_url=final_url,
            cached=True,
            stale=False,
            fetched_at=cached.fetched_at,
        )

    if status != 200:
        return _stale_fallback(
            url, cached, f"the documentation host answered with HTTP {status}"
        )

    content = body.decode("utf-8", errors="replace")
    content_type = (response_headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type and not (
        content_type.startswith("text/")
        or content_type in ("application/markdown", "application/x-markdown")
    ):
        # Not fatal: the document is still rendered as Markdown. But a host
        # serving HTML at the documentation URL is usually a mistake worth
        # naming in the log.
        logger.warning(
            "The Help document at %s has content-type '%s'; Markdown was expected",
            final_url,
            content_type,
        )
    entry = _CachedDocument(
        content=content,
        etag=response_headers.get("etag"),
        last_modified=response_headers.get("last-modified"),
        expires_at=now + config.cache_ttl_seconds,
        fetched_at=now,
    )
    with _cache_lock:
        _document_cache[url] = entry
    _trim_document_cache()
    return RemoteHelpResult(
        content=content,
        source_url=final_url,
        cached=False,
        stale=False,
        fetched_at=now,
    )


def _stale_fallback(
    url: str, cached: Optional[_CachedDocument], message: str
) -> RemoteHelpResult:
    """Serve the last known good copy, or report the failure."""
    if cached is not None:
        logger.warning(
            "Serving the last known good Help document from %s: %s", url, message
        )
        return RemoteHelpResult(
            content=cached.content,
            source_url=url,
            cached=True,
            stale=True,
            fetched_at=cached.fetched_at,
            error=message,
        )
    logger.error("Could not fetch the Help document from %s: %s", url, message)
    return RemoteHelpResult(content="", source_url=url, error=message)


async def fetch_remote_image(url: str, config: HelpConfig) -> Tuple[bytes, str]:
    """Fetch an image the hosted document references.

    Returns ``(body, content_type)``. The URL must be on an allowed host.
    """
    _require_allowed_url(url, config, "an image")

    now = time.monotonic()
    _prune_expired_images(now)
    with _cache_lock:
        cached = _image_cache.get(url)
    if cached and cached[0] > now:
        return cached[1], cached[2]

    try:
        status, final_url, body, response_headers = await _get(
            url, config, max_bytes=config.max_image_bytes
        )
    except HelpFetchError as exc:
        raise HelpFetchError(str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HelpFetchError(f"could not reach the documentation host: {exc}") from exc

    if status != 200:
        raise HelpFetchError(f"the documentation host answered with HTTP {status}")

    content_type = (response_headers.get("content-type") or "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        raise HelpFetchError(
            f"'{final_url}' is not an image (content-type: {content_type or 'unknown'})"
        )

    with _cache_lock:
        _image_cache[url] = (now + config.cache_ttl_seconds, body, content_type)
    return body, content_type


# --- Markdown image rewriting -------------------------------------------

# ![alt](src "title") and <img src="..."> -- the two ways a Markdown
# document can point at an image.
_MARKDOWN_IMAGE_RE = re.compile(
    r"(!\[[^\]]*\]\(\s*)(<?)([^\s>)]+)(>?)([^)]*)\)",
)
_HTML_IMAGE_RE = re.compile(
    r"(<img\b[^>]*?\bsrc\s*=\s*)(\"|')(.*?)\2",
    re.IGNORECASE | re.DOTALL,
)


def _proxy_url(absolute_url: str) -> str:
    return f"{IMAGE_PROXY_PATH}?url={quote(absolute_url, safe='')}"


def _rewrite_src(src: str, document_url: str, config: Optional[HelpConfig] = None) -> str:
    """Resolve one image reference against the document URL.

    A reference that lands on the documentation host (or on another host the
    operator allowed) is rewritten to the Atlas image proxy. One that points
    somewhere else is left alone: the default content security policy
    (``img-src 'self'``) blocks it, and an operator who wants it can widen
    the policy rather than having Atlas quietly fetch from an arbitrary third
    party.
    """
    candidate = (src or "").strip()
    if not candidate:
        return src
    if candidate.startswith("data:"):
        return src

    if candidate.startswith("//"):
        scheme = urlparse(document_url).scheme or "https"
        candidate = f"{scheme}:{candidate}"

    absolute = urljoin(document_url, candidate)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return src

    if config is not None:
        if not _is_allowed_url(absolute, config):
            return src
    else:
        document_origin = _origin(document_url)
        if _origin(absolute) != document_origin:
            return src

    return _proxy_url(absolute)


def rewrite_markdown_images(
    markdown: str, document_url: str, config: Optional[HelpConfig] = None
) -> str:
    """Point a hosted document's image references at the Atlas image proxy.

    Relative references (``images/x.png``) and root-relative ones
    (``/images/x.png``) are resolved against the document URL, so a hosted
    document never has to know that it is being rendered inside Atlas -- and
    a reference like ``/help-images/x.png`` is not mistaken for an
    ATLAS-local path.
    """
    if not markdown or not document_url:
        return markdown

    def _markdown_sub(match: re.Match) -> str:
        prefix, open_angle, src, close_angle, suffix = match.groups()
        rewritten = _rewrite_src(src, document_url, config)
        # The closing parenthesis is part of the match but not of a group, so
        # it has to be put back by hand.
        return f"{prefix}{open_angle}{rewritten}{close_angle}{suffix})"

    def _html_sub(match: re.Match) -> str:
        prefix, quote_char, src = match.groups()
        return f"{prefix}{quote_char}{_rewrite_src(src, document_url, config)}{quote_char}"

    rewritten = _MARKDOWN_IMAGE_RE.sub(_markdown_sub, markdown)
    return _HTML_IMAGE_RE.sub(_html_sub, rewritten)
