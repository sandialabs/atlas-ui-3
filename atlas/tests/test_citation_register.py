"""Citation numbering for atlas_search results (issue #874).

The behaviour that matters is stability: a document keeps one number across
every search in a turn and across the turns that follow, because the ``[3]`` in
an answer has to keep meaning the same thing as a reader scrolls.
"""

import pytest

from atlas.application.chat.utilities.citation_publishing import (
    attach_citations,
    publish_citations,
)
from atlas.domain.chat.citation_register import (
    CITATIONS_METADATA_KEY,
    MAX_CITATIONS,
    CitationRegister,
    highest_citation_number,
    new_register,
)
from atlas.domain.messages.models import ConversationHistory, Message, MessageRole


class _Doc:
    """Minimal ``DocumentMetadata`` stand-in."""

    def __init__(self, title=None, url=None, citation=None, document_ref=None, sections=()):
        self.title = title
        self.source = None
        self.url = url
        self.citation = citation
        self.document_ref = document_ref
        self.sections = list(sections)


class TestNumberingIsStable:
    def test_same_document_twice_in_one_turn_keeps_its_number(self):
        register = CitationRegister()
        first = register.register("srv:docs", {"filename": "policy.pdf", "url": "https://x/p"})
        second = register.register("srv:docs", {"filename": "policy.pdf", "url": "https://x/p"})
        assert first == second == 1
        assert len(register) == 1

    def test_a_second_search_continues_rather_than_restarting(self):
        register = CitationRegister()
        register.register_documents("srv:docs", [_Doc(title="a.pdf"), _Doc(title="b.pdf")])
        # A later atlas_search in the same turn returns one old and one new hit.
        refs = register.register_documents("srv:docs", [_Doc(title="b.pdf"), _Doc(title="c.pdf")])
        assert [r["n"] for r in refs] == [2, 3]
        assert [e["n"] for e in register.entries()] == [1, 2, 3]

    def test_a_repeated_hit_is_one_number_even_as_document_ref_shifts(self):
        """``document_ref`` indexes one response's results, not the corpus, so
        the same document comes back under a different ref from each search.
        Numbering off it is what made a repeated hit take two numbers."""
        register = CitationRegister()
        first = register.register(
            "srv:docs", {"document_ref": 3, "filename": "Code of Conduct, pol-003.txt"}
        )
        second = register.register(
            "srv:docs", {"document_ref": 5, "filename": "Code of Conduct, pol-003.txt"}
        )
        assert first == second == 1
        assert len(register) == 1

    def test_a_url_identifies_a_document_through_a_retitling(self):
        register = CitationRegister()
        first = register.register("srv:docs", {"filename": "Old title", "url": "https://x/doc"})
        second = register.register("srv:docs", {"filename": "New title", "url": "https://x/doc"})
        assert first == second

    def test_same_filename_in_two_corpora_is_two_documents(self):
        register = CitationRegister()
        a = register.register("srv:policies", {"filename": "index.md"})
        b = register.register("srv:techdocs", {"filename": "index.md"})
        assert a != b

    def test_numbering_continues_across_turns(self):
        history = ConversationHistory()
        history.add_message(Message(
            role=MessageRole.ASSISTANT,
            content="answer [1][2]",
            metadata={CITATIONS_METADATA_KEY: [{"n": 1, "filename": "a"}, {"n": 2, "filename": "b"}]},
        ))
        register = new_register(history.messages)
        assert register.register("srv:docs", {"filename": "c.pdf"}) == 3

    def test_a_conversation_that_never_cited_starts_at_one(self):
        assert highest_citation_number([]) == 0
        assert new_register([]).register("s:d", {"filename": "a"}) == 1


class TestUntrustedBackendValues:
    def test_non_http_url_is_dropped_but_the_document_is_still_cited(self):
        register = CitationRegister()
        number = register.register("s:d", {"filename": "doc", "url": "javascript:alert(1)"})
        assert number == 1
        assert register.entries()[0].get("url") is None

    def test_an_entry_with_nothing_identifying_is_not_numbered(self):
        register = CitationRegister()
        assert register.register("s:d", {"document_ref": 3}) is None
        assert len(register) == 0

    def test_control_characters_are_stripped(self):
        register = CitationRegister()
        register.register("s:d", {"filename": "we\x00ird\x07name"})
        assert register.entries()[0]["filename"] == "weirdname"

    def test_registration_stops_at_the_cap_without_raising(self):
        register = CitationRegister()
        for index in range(MAX_CITATIONS + 10):
            register.register("s:d", {"filename": f"doc-{index}.pdf"})
        assert len(register) == MAX_CITATIONS


class TestTurnClosing:
    def test_citations_ride_on_the_closing_message_metadata(self):
        register = CitationRegister()
        register.register("s:d", {"filename": "a.pdf"})
        metadata = attach_citations({"tools": []}, register)
        assert metadata[CITATIONS_METADATA_KEY] == [
            {"n": 1, "filename": "a.pdf", "data_source": "s:d"}
        ]
        # The caller's dict is not mutated.
        assert "tools" in metadata

    def test_a_turn_that_searched_nothing_adds_no_metadata(self):
        assert attach_citations({"tools": []}, CitationRegister()) == {"tools": []}
        assert attach_citations({"tools": []}, None) == {"tools": []}

    @pytest.mark.asyncio
    async def test_nothing_is_published_for_an_empty_register(self):
        published = []

        class _Publisher:
            async def publish_citations(self, citations):
                published.append(citations)

        await publish_citations(_Publisher(), CitationRegister())
        assert published == []

    @pytest.mark.asyncio
    async def test_a_publisher_without_the_method_does_not_fail_the_turn(self):
        register = CitationRegister()
        register.register("s:d", {"filename": "a.pdf"})

        class _Old:
            pass

        await publish_citations(_Old(), register)  # must not raise

    @pytest.mark.asyncio
    async def test_a_dead_transport_does_not_fail_the_turn(self):
        register = CitationRegister()
        register.register("s:d", {"filename": "a.pdf"})

        class _Broken:
            async def publish_citations(self, citations):
                raise RuntimeError("connection closed")

        await publish_citations(_Broken(), register)  # must not raise


class TestReplayedToTheModel:
    def test_the_numbers_are_folded_into_the_next_turn(self):
        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="q"))
        history.add_message(Message(
            role=MessageRole.ASSISTANT,
            content="answer [1]",
            metadata={CITATIONS_METADATA_KEY: [{"n": 1, "filename": "policy.pdf"}]},
        ))
        history.add_message(Message(role=MessageRole.USER, content="follow up"))
        replayed = history.get_messages_for_llm()
        assert "[1] policy.pdf" in replayed[1]["content"]
        # The answer text itself survives untouched ahead of the fold.
        assert replayed[1]["content"].startswith("answer [1]")

    def test_a_conversation_without_citations_gains_nothing(self):
        history = ConversationHistory()
        history.add_message(Message(role=MessageRole.USER, content="hello"))
        history.add_message(Message(role=MessageRole.ASSISTANT, content="hi"))
        assert history.get_messages_for_llm() == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]

    def test_a_newer_turn_wins_when_a_number_was_reused(self):
        # Defensive: a hand-edited or replayed conversation could carry the same
        # number twice. The reader sees the newest answer's meaning, so that is
        # the one replayed.
        history = ConversationHistory()
        history.add_message(Message(
            role=MessageRole.ASSISTANT, content="old",
            metadata={CITATIONS_METADATA_KEY: [{"n": 1, "filename": "old.pdf"}]},
        ))
        history.add_message(Message(
            role=MessageRole.ASSISTANT, content="new",
            metadata={CITATIONS_METADATA_KEY: [{"n": 1, "filename": "new.pdf"}]},
        ))
        folded = history.get_messages_for_llm()[-1]["content"]
        assert "new.pdf" in folded
        assert "old.pdf" not in folded


class TestBackendCitationStrings:
    def test_a_backend_assigned_marker_is_stripped(self):
        """The backend numbers its own response; the register numbers the
        conversation. Showing both makes entry 5 read '[1] ...'."""
        register = CitationRegister()
        register.register("s:d", {
            "filename": "API Authentication Guide, tech-001.txt",
            "citation": '[1] "API Authentication Guide", tech-001.txt available: https://x/a',
        })
        assert register.entries()[0]["citation"].startswith('"API Authentication Guide"')

    def test_a_citation_that_is_only_a_marker_is_dropped(self):
        register = CitationRegister()
        assert register.register("s:d", {"citation": "[2]"}) is None

    def test_a_bracketed_number_later_in_the_string_survives(self):
        register = CitationRegister()
        register.register("s:d", {"filename": "doc", "citation": "See section [4] of the guide"})
        assert register.entries()[0]["citation"] == "See section [4] of the guide"


class TestNumberingSurvivesTheTurnBoundary:
    """A number means one document for the whole transcript, so a document
    cited again in a later turn keeps the number it already had -- and is still
    listed under the answer that cites it."""

    @staticmethod
    def _history_with(entries):
        history = ConversationHistory()
        history.add_message(Message(
            role=MessageRole.ASSISTANT,
            content="earlier answer",
            metadata={CITATIONS_METADATA_KEY: entries},
        ))
        return history

    def test_a_document_cited_again_keeps_its_number(self):
        history = self._history_with([
            {"n": 5, "filename": "Microservices Architecture, tech-004.txt", "data_source": "s:d"},
        ])
        register = new_register(history.messages)
        assert register.register(
            "s:d", {"filename": "Microservices Architecture, tech-004.txt"}
        ) == 5

    def test_it_is_still_listed_under_the_answer_that_re_cites_it(self):
        history = self._history_with([{"n": 5, "filename": "old.pdf", "data_source": "s:d"}])
        register = new_register(history.messages)
        register.register("s:d", {"filename": "old.pdf"})
        assert [e["n"] for e in register.entries()] == [5]

    def test_a_turn_that_cites_nothing_old_lists_nothing_old(self):
        history = self._history_with([{"n": 5, "filename": "old.pdf", "data_source": "s:d"}])
        register = new_register(history.messages)
        register.register("s:d", {"filename": "new.pdf"})
        assert [e["n"] for e in register.entries()] == [6]

    def test_a_new_document_never_reuses_a_retired_number(self):
        history = self._history_with([
            {"n": 1, "filename": "a.pdf", "data_source": "s:d"},
            {"n": 2, "filename": "b.pdf", "data_source": "s:d"},
        ])
        register = new_register(history.messages)
        assert register.register("s:d", {"filename": "c.pdf"}) == 3
