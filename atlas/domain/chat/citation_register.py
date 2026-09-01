"""Turn-scoped citation numbering for ``atlas_search`` results (issue #874).

Before search became an explicit tool call (#862), citations were produced by
the RAG pre-injection path in two pieces: an instruction telling the model to
write ``[1]``/``[2]`` markers, and a ``**References**`` markdown block appended
to the assistant's own message. The frontend recovered the sources by scanning
that rendered markdown for the literal word "References".

That worked while exactly one retrieval happened per turn, before the model was
asked anything. It cannot survive a tool: the model may search zero times, once,
or five times, and the answer is written across those calls rather than after a
single injection. Scraping the answer text also gave attacker-controlled passage
text a way to masquerade as a reference entry, which is why
``_sanitize_snippet`` exists.

So citations become data. Every document ``atlas_search`` returns is registered
here, once per turn, and gets a number:

* **Identity-keyed.** A document hit by two searches in the same turn keeps the
  number it got the first time. Without this, three calls returning overlapping
  results would restart at ``[1]`` three times and the markers in the prose
  would point at whichever call the model happened to be looking at.
* **Monotonic, and continued across turns.** ``[3]`` means one document for the
  whole conversation. Restarting per turn makes a scrollback actively
  misleading, since the same marker resolves differently further up.

The register is a plain per-turn object carried on the tool execution context,
exactly like the sleep tool's turn budget (``TURN_BUDGET_KEY``): the tool
executor forwards it to every call, and the turn's owner reads it back when the
turn closes. Nothing here talks to a transport or a session store.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Execution-context key. Mirrors ``TURN_BUDGET_KEY``: mutable, one per turn,
# forwarded by ``tool_executor`` to every tool call.
CITATION_REGISTER_KEY = "citation_register"

# Message-metadata key. Persisted on the turn's closing assistant message so a
# reloaded conversation renders its citations without re-running retrieval, and
# so the next turn can continue the numbering.
CITATIONS_METADATA_KEY = "citations"

# Highest citation number a conversation will ever issue. A citation list is a
# reading aid, not a corpus dump, and past this the numbers stop being something
# a reader can hold.
#
# The exact value is set by the renderer, not by taste: ``processCitationBadges``
# matches ``[\d{1,2}]``, so a ``[100]`` in an answer stays plain text instead of
# becoming a chip that scrolls to its source. Widening that regex would make it
# match three-digit array indices in prose, so the ceiling gives way instead.
#
# Registration past the cap is dropped rather than raising -- losing a citation
# must never fail the search that produced it.
MAX_CITATION_NUMBER = 99

# Per-field caps. Every value here originates in a RAG backend response, so it
# is untrusted and bounded before it reaches the transcript. ``DocumentMetadata``
# already validates and truncates these; the caps are repeated because the
# register also accepts plain dicts (replayed metadata, MCP-shaped results).
_MAX_LABEL_CHARS = 200
_MAX_CITATION_CHARS = 500
_MAX_SOURCE_CHARS = 200
_MAX_SNIPPET_CHARS = 600
# Snippets are evidence, shown in the expanded citation area. More than a
# handful per document turns the panel into the corpus it is summarizing.
_MAX_SNIPPETS = 3


# A backend citation string often opens with its own bracketed number -- the
# ATLAS-RAG reference mock emits ``[1] "API Authentication Guide", tech-001.txt
# available: ...``. That number indexes *its* response, so beside the register's
# own (conversation-stable) number it reads as a contradiction: entry 5 whose
# text starts "[1]". The register owns the numbering, so the backend's is
# stripped rather than shown twice.
_LEADING_MARKER = re.compile(r"^\s*\[\d{1,3}\]\s*")


def _clean(value: Any, limit: int) -> Optional[str]:
    """Coerce an untrusted backend value to a bounded plain string."""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    # Control characters only. Unlike the old markdown path there is nothing to
    # escape for: these values are delivered as JSON and rendered as text nodes,
    # never concatenated into markup or into a prompt.
    cleaned = "".join(ch for ch in value if ch >= " " or ch in "\t\n").strip()
    if not cleaned:
        return None
    return cleaned[:limit]


def _strip_leading_marker(value: Optional[str]) -> Optional[str]:
    """Drop a backend-assigned ``[n]`` prefix from a citation string."""
    if not value:
        return value
    stripped = _LEADING_MARKER.sub("", value).strip()
    return stripped or None


def _identity(data_source: Optional[str], entry: Dict[str, Any]) -> str:
    """Stable key for "the same document, seen again".

    A URL is the strongest identifier and is preferred, with the filename next.
    ``document_ref`` comes **last**, despite sounding like the authoritative
    document id: in the ATLAS-RAG contract it is an index into *one response's*
    result list, so the same document comes back as ``document_ref`` 3 from one
    search and 5 from the next. Trusting it first is what made a repeated hit
    take two citation numbers -- observed against the reference mock, where two
    searches in one turn each returned "Code of Conduct, pol-003.txt" and it was
    numbered twice.

    The key is scoped by data source because filename is in the chain: two
    corpora may each hold a ``policy.pdf`` that are not the same document, and
    merging them under one number would credit a claim to the wrong source.
    """
    scope = data_source or ""
    for field in ("url", "filename", "document_ref"):
        value = entry.get(field)
        if value not in (None, ""):
            return f"{scope}\x00{field}\x00{value}"
    # Nothing identifying at all: fall back to the whole entry so it still gets
    # a number rather than colliding with every other anonymous document.
    return f"{scope}\x00repr\x00{sorted(entry.items())!r}"


class CitationRegister:
    """Assigns stable ``[n]`` numbers to documents seen during a turn.

    Not thread-safe, and deliberately so: a turn's tool calls are awaited by a
    single loop, and ``asyncio.gather`` over source groups resolves back into
    that loop before any registration happens.
    """

    def __init__(
        self,
        start_index: int = 0,
        known: Optional[Dict[str, int]] = None,
    ) -> None:
        # Where this turn's numbering picks up. Seeded from the conversation's
        # previously persisted citations so numbers never repeat across turns.
        self._next = max(0, int(start_index)) + 1
        # Identities the conversation has already numbered, from earlier turns.
        # Kept apart from ``_entries`` on purpose: a document cited three turns
        # ago must keep its number if it comes back, but must not be listed
        # under an answer that did not cite it.
        self._known: Dict[str, int] = dict(known or {})
        self._by_identity: Dict[str, int] = {}
        self._entries: Dict[int, Dict[str, Any]] = {}

    def register(self, data_source: Optional[str], entry: Dict[str, Any]) -> Optional[int]:
        """Register one document, returning its citation number.

        Returns the existing number when the document was already seen this
        turn, and ``None`` when the entry carries nothing worth citing or the
        conversation cap is reached.
        """
        cleaned = {
            "filename": _clean(entry.get("filename"), _MAX_LABEL_CHARS),
            "citation": _strip_leading_marker(
                _clean(entry.get("citation"), _MAX_CITATION_CHARS)
            ),
            "url": _clean(entry.get("url"), _MAX_LABEL_CHARS),
            "data_source": _clean(data_source, _MAX_SOURCE_CHARS),
        }
        doc_ref = entry.get("document_ref")
        if isinstance(doc_ref, int):
            cleaned["document_ref"] = doc_ref

        snippets = [
            snippet
            for snippet in (
                _clean(text, _MAX_SNIPPET_CHARS)
                for text in (entry.get("snippets") or [])
            )
            if snippet
        ][:_MAX_SNIPPETS]
        if snippets:
            cleaned["snippets"] = snippets

        # A URL that is not http(s) is dropped rather than rendered: the entry
        # becomes a link in the UI, and ``javascript:`` in a backend response
        # must not become a clickable href.
        url = cleaned.get("url")
        if url and not (url.startswith("http://") or url.startswith("https://")):
            cleaned["url"] = None

        identifying = {k: v for k, v in cleaned.items() if v not in (None, "")}
        if not any(identifying.get(k) for k in ("filename", "url", "citation")):
            # Nothing a reader could use to find the document. Numbering it
            # would put an empty row in the references panel.
            return None

        key = _identity(cleaned.get("data_source"), identifying)

        # Seen in an earlier turn: reuse that number and list it under this
        # answer too, so the reader sees the same document under the same
        # number wherever it is cited in the transcript.
        if key not in self._by_identity and key in self._known:
            number = self._known[key]
            self._by_identity[key] = number
            self._entries[number] = {"n": number, **identifying}
            return number

        existing = self._by_identity.get(key)
        if existing is not None:
            # Seen again in a later call this turn. Merge in anything the
            # earlier sighting lacked (a second backend may return a URL the
            # first omitted) without renumbering.
            for field, value in identifying.items():
                self._entries[existing].setdefault(field, value)
            return existing

        # Conversation-wide, not per turn: ``_next`` is seeded from the numbers
        # already issued, so counting this turn's entries instead would let a
        # conversation that has reached the ceiling start a fresh register at
        # zero and carry on past it.
        if self._next > MAX_CITATION_NUMBER:
            logger.debug(
                "Citation numbering exhausted at %d; dropping entry", MAX_CITATION_NUMBER
            )
            return None

        number = self._next
        self._next += 1
        self._by_identity[key] = number
        self._entries[number] = {"n": number, **identifying}
        return number

    def entry(self, number: int) -> Optional[Dict[str, Any]]:
        """The registered entry for a number, or ``None`` if unknown."""
        return self._entries.get(number)

    def entries(self) -> List[Dict[str, Any]]:
        """This turn's citations plus any seeded from earlier turns, in order."""
        return [self._entries[n] for n in sorted(self._entries)]

    def __len__(self) -> int:
        return len(self._entries)

    def __bool__(self) -> bool:
        return bool(self._entries)


def _prior_citations(messages: Sequence[Any]) -> tuple:
    """Numbers and identities this conversation has already handed out.

    Read back from persisted assistant-message metadata, which is the only
    record that survives a turn: the register itself dies with the turn that
    built it, and a reloaded conversation has never had one.
    """
    highest = 0
    known: Dict[str, int] = {}
    for message in messages or []:
        metadata = getattr(message, "metadata", None)
        if not isinstance(metadata, dict):
            continue
        for entry in metadata.get(CITATIONS_METADATA_KEY) or []:
            if not isinstance(entry, dict):
                continue
            number = entry.get("n")
            if not isinstance(number, int):
                continue
            if number > highest:
                highest = number
            identifying = {
                field: entry[field]
                for field in ("document_ref", "url", "filename")
                if entry.get(field)
            }
            if identifying:
                # Later turns win: if a number was somehow reused, the newest
                # meaning is the one a reader is looking at.
                known[_identity(entry.get("data_source"), identifying)] = number
    return highest, known


def highest_citation_number(messages: Sequence[Any]) -> int:
    """Highest citation number already used in this conversation.

    Returns ``0`` for a conversation that has never cited anything.
    """
    return _prior_citations(messages)[0]


def new_register(messages: Sequence[Any]) -> CitationRegister:
    """Build a turn's register, continuing the conversation's numbering.

    Carries forward both the count (so a new document never reuses a number)
    and the identities (so a document cited again keeps the number it had).
    """
    highest, known = _prior_citations(messages)
    return CitationRegister(start_index=highest, known=known)
