"""Per-corpus data classifications for HTTP RAG sources (issues #1032, #1035).

One calculation decides which classifications a discovered corpus may receive,
and it is shared by discovery (which corpora the picker offers) and query-time
authorization (which corpora a request may reach), so the two cannot drift.

A corpus can only narrow its server's ``allowed_data_classifications``, never
widen them. Its own declaration is, in order:

1. ``allowed_data_classifications`` from discovery, when the backend sends it
   (an empty list is a declaration: approved for nothing).
2. With the server's ``legacy_corpus_classifications`` opt-in, a
   ``compliance_level`` the backend actually sent, read as a one-element list.
   A malformed value approves the corpus for nothing.
3. Otherwise nothing: the corpus inherits the server's list.

Query-time checks need the corpora's discovery metadata, which only the
backend knows. ``CorpusMetadataCache`` keeps the last discovery answer per
(server, user) for a short time so a turn's pre-flight check, the picker and
the query itself share one backend round trip.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from atlas.core.compliance import declared_classifications, narrow_classifications

# How long a discovery answer may stand in for a fresh one at query time. Short,
# so a corpus reclassified by its backend is re-checked within about a minute.
CORPUS_METADATA_TTL_SECONDS = 60.0
# A requested corpus missing from an answer younger than this is refused
# without asking the backend again, so repeated requests for an id the backend
# does not know (a model's invented corpus, say) cannot force a discovery
# round trip per query.
CORPUS_METADATA_MIN_REFRESH_SECONDS = 5.0
# How long a failed discovery stops query-time checks from asking again. Longer
# than the query-time discovery timeout, so a hung backend costs one timeout
# per window rather than one per query.
CORPUS_DISCOVERY_FAILURE_SECONDS = 30.0
# Upper bound on a query-time discovery call; the configured client timeout
# applies when it is shorter.
CORPUS_DISCOVERY_TIMEOUT_SECONDS = 10.0
# Upper bound on cached (server, user) entries; the oldest are dropped first.
CORPUS_METADATA_MAX_ENTRIES = 2048


def legacy_corpus_classifications(ds: Any) -> Optional[List[str]]:
    """A corpus's legacy ``compliance_level`` as a declaration, if it sent one.

    ``None`` when the backend did not send the field (or sent ``null``) --
    never the model's display default, so a missing field cannot classify a
    corpus. A value that is not a non-empty string is unreadable and becomes
    ``[]``, which approves the corpus for nothing (fail closed).
    """
    fields_set = getattr(ds, "model_fields_set", None)
    if fields_set is not None and "compliance_level" not in fields_set:
        return None
    raw = getattr(ds, "compliance_level", None)
    if raw is None:
        return None
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def corpus_classifications(ds: Any, server_config: Any) -> Optional[List[str]]:
    """The classifications a discovered HTTP corpus is approved for.

    The server's list from ``rag-sources.json``, narrowed by the corpus's own
    declaration (see the module docstring for which one counts). ``None`` when
    the server declares nothing, which denies the corpus in any classified
    session.
    """
    own = getattr(ds, "allowed_data_classifications", None)
    if not isinstance(own, list):
        own = None
        if getattr(server_config, "legacy_corpus_classifications", False):
            own = legacy_corpus_classifications(ds)
    return narrow_classifications(own, declared_classifications(server_config))


def corpus_declares_own(ds: Any, server_config: Any) -> bool:
    """Whether the corpus narrows its server's list rather than inheriting it."""
    if isinstance(getattr(ds, "allowed_data_classifications", None), list):
        return True
    return bool(getattr(server_config, "legacy_corpus_classifications", False)) and (
        legacy_corpus_classifications(ds) is not None
    )


class CorpusMetadataCache:
    """Recent discovery answers, keyed by ``(server, user)``.

    Discovery is per user (the backend filters by ``as_user``), so entries are
    never shared between users. Only a non-empty answer is stored: the HTTP
    client reports a failed discovery as an empty list, and "the backend did
    not answer" must not read as "the backend offers nothing". An empty answer
    leaves an existing entry alone, so a refresh that fails does not discard
    one that is still within its TTL.

    A failed refresh is remembered for ``failure_seconds`` so that during a
    backend outage queries are refused (or, under the model floor alone, let
    through) at once instead of each waiting out the discovery timeout.
    """

    def __init__(
        self,
        ttl_seconds: float = CORPUS_METADATA_TTL_SECONDS,
        max_entries: int = CORPUS_METADATA_MAX_ENTRIES,
        failure_seconds: float = CORPUS_DISCOVERY_FAILURE_SECONDS,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.failure_seconds = failure_seconds
        self._entries: OrderedDict[Tuple[str, str], Tuple[float, Dict[str, Any]]] = OrderedDict()
        self._failures: OrderedDict[Tuple[str, str], float] = OrderedDict()

    @staticmethod
    def _now() -> float:
        return time.monotonic()

    def store(self, server: str, user: str, data_sources: Iterable[Any]) -> None:
        corpora = {ds.id: ds for ds in data_sources if getattr(ds, "id", None)}
        key = (server, user or "")
        if not corpora:
            return
        self._failures.pop(key, None)
        self._entries[key] = (self._now(), corpora)
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def mark_failed(self, server: str, user: str) -> None:
        """Record that discovery for ``(server, user)`` just failed."""
        key = (server, user or "")
        self._failures[key] = self._now()
        self._failures.move_to_end(key)
        while len(self._failures) > self.max_entries:
            self._failures.popitem(last=False)

    def recently_failed(self, server: str, user: str) -> bool:
        """Whether discovery for ``(server, user)`` failed too recently to retry."""
        failed_at = self._failures.get((server, user or ""))
        return failed_at is not None and self._now() - failed_at < self.failure_seconds

    def lookup(self, server: str, user: str) -> Optional[Dict[str, Any]]:
        """The fresh discovery answer for ``(server, user)``, or ``None``."""
        entry = self._fresh(server, user)
        return entry[1] if entry else None

    def age(self, server: str, user: str) -> Optional[float]:
        """Seconds since the fresh answer for ``(server, user)`` was stored."""
        entry = self._fresh(server, user)
        return self._now() - entry[0] if entry else None

    def _fresh(self, server: str, user: str) -> Optional[Tuple[float, Dict[str, Any]]]:
        key = (server, user or "")
        entry = self._entries.get(key)
        if entry is None:
            return None
        if self._now() - entry[0] > self.ttl_seconds:
            del self._entries[key]
            return None
        return entry

    def invalidate(self, server: Optional[str] = None) -> None:
        if server is None:
            self._entries.clear()
            self._failures.clear()
            return
        for store in (self._entries, self._failures):
            for key in [k for k in store if k[0] == server]:
                del store[key]


__all__ = [
    "CORPUS_DISCOVERY_FAILURE_SECONDS",
    "CORPUS_DISCOVERY_TIMEOUT_SECONDS",
    "CORPUS_METADATA_MIN_REFRESH_SECONDS",
    "CORPUS_METADATA_TTL_SECONDS",
    "CorpusMetadataCache",
    "corpus_classifications",
    "corpus_declares_own",
    "legacy_corpus_classifications",
]
