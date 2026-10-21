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

# The image cache is bounded on purpose. A documentation page can reference
# many images, and a host that serves an image per URL variant ("logo.png?n=1",
# "logo.png?n=2", ...) would otherwise fill the process with copies. The
# least recently used entries go first.
MAX_CACHED_IMAGES = 64
MAX_CACHED_IMAGE_BYTES = 32 * 1024 * 1024

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
    # The URL the document was actually read from, after any redirects. The
    # relative image references in the document resolve against this, not
    # against the configured URL: a documentation site that redirects
    # /help.md to /v2/help.md serves its images from /v2/.
    final_url: str = ""


@dataclass
class _CachedImage:
    body: bytes
    content_type: str
    expires_at: float
    last_used: float
    size: int


# The caches live in the process: a Help document changes on a human timescale,
# and a shared cache would be a lot of machinery for one document.
_document_cache: Dict[str, _CachedDocument] = {}
_image_cache: Dict[str, _CachedImage] = {}
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
        for url, entry in list(_image_cache.items()):
            if entry.expires_at <= now:
                del _image_cache[url]


def _evict_images_if_needed() -> None:
    """Keep the image cache inside its size and count limits.

    The least recently used images go first, so the ones a reader is
    actually looking at stay in memory.
    """
    with _cache_lock:
        total_bytes = sum(entry.size for entry in _image_cache.values())
        while _image_cache and (
            len(_image_cache) > MAX_CACHED_IMAGES or total_bytes > MAX_CACHED_IMAGE_BYTES
        ):
            oldest_url = min(_image_cache.items(), key=lambda item: item[1].last_used)[0]
            total_bytes -= _image_cache[oldest_url].size
            del _image_cache[oldest_url]


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


def _allowed_authority(url: str, config: HelpConfig) -> Optional[str]:
    """The authority this configuration allows for ``url``, or ``None``.

    The configured documentation origin is always allowed. Extra hosts must
    be listed by the operator, and every one of them must be reached over
    https (except loopback, for local development).

    The returned authority is built from the configuration, never from the
    request: the request only ever decides *which* of the configured
    authorities applies.
    """
    if not config.content_url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.username or parsed.password:
        return None
    if not parsed.hostname:
        return None

    requested_host = (parsed.hostname or "").lower()
    requested_port = parsed.port or (443 if parsed.scheme == "https" else 80)

    configured = urlparse(config.content_url)
    configured_host = (configured.hostname or "").lower()
    configured_port = configured.port or (
        443 if configured.scheme == "https" else 80
    )
    if requested_host == configured_host and requested_port == configured_port:
        return _authority(configured.scheme, configured_host, configured_port)

    for host, port in config.allowed_hosts:
        if host != requested_host:
            continue
        if port is not None and port != requested_port:
            continue
        scheme = configured.scheme
        if scheme == "http" and host not in ("localhost", "127.0.0.1", "::1"):
            scheme = "https"
        return _authority(scheme, host, port or requested_port)

    return None


def _is_allowed_url(url: str, config: HelpConfig) -> bool:
    """True when ``url`` may be fetched for this configuration."""
    return _allowed_authority(url, config) is not None


def resolve_allowed_target(url: str, config: HelpConfig) -> str:
    """Return the URL to fetch for a requested target, built from the config.

    The authority (scheme, host, port) always comes from the configuration:
    the configured documentation origin, or one of the hosts the operator
    listed in ``HELP_ALLOWED_HOSTS``. Only the path and the query come from
    the request. The worst a request can do is therefore ask for a
    different path on a host the operator already trusts -- it can never
    aim the request somewhere else, and it can never downgrade the scheme.

    A request for a host that is not allowed is refused.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HelpPolicyError(
            f"refusing to fetch '{url}': only http(s) URLs are allowed"
        )
    if parsed.username or parsed.password:
        raise HelpPolicyError(
            f"refusing to fetch '{url}': credentials in the URL are not allowed"
        )
    if not parsed.hostname:
        raise HelpPolicyError(f"refusing to fetch '{url}': the URL has no host")

    authority = _allowed_authority(url, config)
    if authority is None:
        raise HelpPolicyError(
            f"refusing to fetch '{url}': only the configured documentation host is allowed"
        )
    return _with_authority(authority, parsed)


def _authority(scheme: str, host: str, port: int) -> str:
    """``scheme://host[:port]`` for the given parts."""
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def _with_authority(authority: str, parsed) -> str:
    """Rebuild a URL with a given authority, keeping its path and query."""
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return f"{authority}{path}"


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

    ``url`` must already be a target this configuration allows (see
    :func:`resolve_allowed_target`). A redirect target is resolved the same
    way, so a redirect cannot aim the request somewhere else either.

    Returns ``(status, final_url, body, response_headers)``.
    """
    limit = config.max_bytes if max_bytes is None else max_bytes
    timeout = httpx.Timeout(config.timeout_seconds)
    current = url
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(MAX_REDIRECTS + 1):
            # The request cannot leave the configured documentation origin:
            # resolve_allowed_target() takes the scheme, the host and the
            # port from the deployment's configuration, so only the path
            # and the query come from the request. A redirect target goes
            # through the same function before it is fetched, so it cannot
            # aim the request somewhere else either. The endpoint is
            # authenticated, and the response is only returned when the
            # documentation host answers with an image, so it cannot be
            # used to read arbitrary content from that host.
            # codeql[py/full-ssrf]
            # codeql[py/partial-ssrf]
            async with client.stream("GET", current, headers=headers or {}) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise HelpFetchError(
                            f"redirect from '{current}' had no Location header"
                        )
                    target = urljoin(current, location)
                    current = resolve_allowed_target(target, config)
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
            # The URL the document was read from, so the relative image
            # references resolve against the same place they did the first
            # time -- the document may have been redirected.
            source_url=cached.final_url or url,
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
            resolve_allowed_target(url, config), config, headers=request_headers
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
        final_url=final_url,
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
            # The URL the document was read from, so the relative image
            # references keep resolving against the same place.
            source_url=cached.final_url or url,
            cached=True,
            stale=True,
            fetched_at=cached.fetched_at,
            error=message,
        )
    logger.error("Could not fetch the Help document from %s: %s", url, message)
    return RemoteHelpResult(content="", source_url=url, error=message)


async def fetch_remote_image(url: str, config: HelpConfig) -> Tuple[bytes, str]:
    """Fetch an image the hosted document references.

    Returns ``(body, content_type)``. The URL is resolved against the
    configuration first, so only a host the deployment trusts is contacted.
    """
    target = resolve_allowed_target(url, config)

    now = time.monotonic()
    _prune_expired_images(now)
    with _cache_lock:
        cached = _image_cache.get(target)
    if cached and cached.expires_at > now:
        with _cache_lock:
            cached.last_used = now
        return cached.body, cached.content_type

    try:
        status, final_url, body, response_headers = await _get(
            target, config, max_bytes=config.max_image_bytes
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
        _image_cache[target] = _CachedImage(
            body=body,
            content_type=content_type,
            expires_at=now + config.cache_ttl_seconds,
            last_used=now,
            size=len(body),
        )
    _evict_images_if_needed()
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
