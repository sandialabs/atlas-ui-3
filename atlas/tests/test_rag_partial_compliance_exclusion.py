"""One out-of-boundary RAG source must not discard the rest of the turn.

``_query_all_rag_sources`` fans out one request per server group. A denial from
a single group used to propagate immediately, throwing away results already
retrieved from the other groups. It now drops the rejected groups, answers from
the remainder, and reports the exclusion; the hard error is reserved for the
case where every group was rejected.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from atlas.domain.errors import DataSourcePermissionError
from atlas.modules.rag.client import RAGResponse


def _make_caller():
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    caller = LiteLLMCaller.__new__(LiteLLMCaller)
    caller._rag_service = MagicMock()
    caller._llm_config = MagicMock()
    caller._model_configs = {}
    return caller


def _denial(source: str) -> DataSourcePermissionError:
    """A denial shaped like the real one, which names the *corpus* the user
    selected rather than the ``rag-sources.json`` server key."""
    return DataSourcePermissionError(
        f"The data source '{source}' is not accessible at the compliance level "
        "of the selected model. Deselect it, or switch to a model cleared for "
        "that source.",
        code="DATA_SOURCE_COMPLIANCE_MISMATCH",
    )


def _rag_service(responses):
    """A RAG service whose single-source query is keyed by qualified source.

    ``responses`` maps a qualified source to either a RAGResponse or an
    exception to raise.
    """
    service = MagicMock()

    async def query_rag(user_email, source, messages):
        outcome = responses[source]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    service.query_rag = AsyncMock(side_effect=query_rag)
    return service


@pytest.mark.asyncio
async def test_one_denied_group_does_not_discard_the_others():
    caller = _make_caller()
    service = _rag_service({
        "allowed:corpus": RAGResponse(content="good context"),
        "denied:corpus": _denial("denied-corpus"),
    })

    successful, exclusions, failures = await caller._query_all_rag_sources(
        ["allowed:corpus", "denied:corpus"],
        service,
        "user@test.com",
        [{"role": "user", "content": "q"}],
    )

    assert [response.content for _label, response in successful] == ["good context"]
    assert len(exclusions) == 1
    assert "denied-corpus" in exclusions[0]
    assert failures == []


@pytest.mark.asyncio
async def test_all_groups_denied_raises():
    """With nothing left to answer from, degrading silently is the bug."""
    caller = _make_caller()
    service = _rag_service({
        "a:corpus": _denial("a-corpus"),
        "b:corpus": _denial("b-corpus"),
    })

    with pytest.raises(DataSourcePermissionError):
        await caller._query_all_rag_sources(
            ["a:corpus", "b:corpus"],
            service,
            "user@test.com",
            [{"role": "user", "content": "q"}],
        )


@pytest.mark.asyncio
async def test_non_permission_failures_are_reported_not_silent():
    """An unreachable backend is not an authorization problem, so the turn
    must keep going (no raise). But the failure is no longer dropped silently
    (GH #844): it is returned so the LLM can tell the user which source could
    not be queried."""
    caller = _make_caller()
    service = _rag_service({
        "allowed:policies": RAGResponse(content="good context"),
        "broken:techdocs": RuntimeError("connection refused"),
    })

    successful, exclusions, failures = await caller._query_all_rag_sources(
        ["allowed:policies", "broken:techdocs"],
        service,
        "user@test.com",
        [{"role": "user", "content": "q"}],
    )

    assert len(successful) == 1
    assert exclusions == []
    assert len(failures) == 1
    # The corpus name (not the raw error) reaches the user-facing message:
    # _parse_qualified_data_source strips the server prefix, so the display
    # name is the corpus the user selected.
    assert "techdocs" in failures[0]
    assert "connection refused" not in failures[0]


@pytest.mark.asyncio
async def test_all_sources_fail_does_not_raise():
    """Every source erroring is still not an authorization problem; the turn
    does not raise (the caller informs the LLM instead)."""
    caller = _make_caller()
    service = _rag_service({
        "broken-a:techdocs": RuntimeError("500: RAG service error"),
        "broken-b:policies": RuntimeError("connection refused"),
    })

    successful, exclusions, failures = await caller._query_all_rag_sources(
        ["broken-a:techdocs", "broken-b:policies"],
        service,
        "user@test.com",
        [{"role": "user", "content": "q"}],
    )

    assert successful == []
    assert exclusions == []
    assert len(failures) == 2
    assert any("techdocs" in f for f in failures)
    assert any("policies" in f for f in failures)


def test_exclusion_notice_names_each_dropped_source():
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    notice = LiteLLMCaller._build_rag_exclusion_notice(
        ["The data source 'secret-docs' is not accessible ..."]
    )

    assert "secret-docs" in notice
    assert "NOT searched" in notice


def test_exclusion_notice_is_empty_when_nothing_was_dropped():
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    assert LiteLLMCaller._build_rag_exclusion_notice([]) == ""


@pytest.mark.asyncio
async def test_exclusion_notice_reaches_the_rag_system_message():
    """The user must be told, so the notice rides in the RAG context block."""
    caller = _make_caller()
    service = _rag_service({
        "allowed:corpus": RAGResponse(content="good context"),
        "denied:corpus": _denial("denied-corpus"),
    })

    captured = {}

    async def fake_call_plain(model_name, messages, **kwargs):
        captured["messages"] = messages
        return "answer"

    caller.call_plain = AsyncMock(side_effect=fake_call_plain)

    await caller.call_with_rag(
        "test-model",
        [{"role": "user", "content": "q"}],
        ["allowed:corpus", "denied:corpus"],
        "user@test.com",
        rag_service=service,
    )

    system_blocks = [
        m["content"] for m in captured["messages"] if m["role"] == "system"
    ]
    assert any("denied" in block and "NOT searched" in block for block in system_blocks)


# -- RAG query failures (GH #844) -------------------------------------------


def test_failure_notice_names_each_failed_source():
    """The failure notice names the source and instructs the LLM to tell the user."""
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    notice = LiteLLMCaller._build_rag_failure_notice(
        ["The data source 'atlas_rag-prod' could not be queried because the "
         "RAG service returned an error."]
    )

    assert "atlas_rag-prod" in notice
    assert "could NOT be queried" in notice
    # The LLM must be instructed to tell the user, not answer silently.
    assert "tell the user" in notice.lower()


def test_failure_notice_is_empty_when_nothing_failed():
    from atlas.modules.llm.litellm_caller import LiteLLMCaller

    assert LiteLLMCaller._build_rag_failure_notice([]) == ""


@pytest.mark.asyncio
async def test_partial_failure_notice_reaches_the_rag_system_message():
    """A partial failure (some sources ok, some broken) rides in the RAG
    context block alongside the retrieved context (GH #844)."""
    caller = _make_caller()
    service = _rag_service({
        "allowed:policies": RAGResponse(content="good context"),
        "broken:techdocs": RuntimeError("500: RAG service error"),
    })

    captured = {}

    async def fake_call_plain(model_name, messages, **kwargs):
        captured["messages"] = messages
        return "answer"

    caller.call_plain = AsyncMock(side_effect=fake_call_plain)

    await caller.call_with_rag(
        "test-model",
        [{"role": "user", "content": "q"}],
        ["allowed:policies", "broken:techdocs"],
        "user@test.com",
        rag_service=service,
    )

    system_blocks = [
        m["content"] for m in captured["messages"] if m["role"] == "system"
    ]
    # The retrieved context is still present...
    assert any("good context" in block for block in system_blocks)
    # ...and the failure notice names the broken corpus and instructs the LLM.
    assert any(
        "techdocs" in block and "could NOT be queried" in block
        for block in system_blocks
    )


@pytest.mark.asyncio
async def test_all_sources_fail_informs_llm_instead_of_silent_fallback():
    """When every RAG source fails, the LLM must be told the query failed and
    instructed to tell the user -- not silently fall back to a plain call with
    no indication anything went wrong (GH #844)."""
    caller = _make_caller()
    service = _rag_service({
        "broken:techdocs": RuntimeError("500: RAG service error"),
    })

    captured = {}

    async def fake_call_plain(model_name, messages, **kwargs):
        captured["messages"] = messages
        return "I was unable to retrieve context from your data sources."

    caller.call_plain = AsyncMock(side_effect=fake_call_plain)

    result = await caller.call_with_rag(
        "test-model",
        [{"role": "user", "content": "q"}],
        ["broken:techdocs"],
        "user@test.com",
        rag_service=service,
    )

    # The LLM was still called (no raise)...
    caller.call_plain.assert_awaited_once()
    # ...but with a system message telling it the RAG query failed.
    system_blocks = [
        m["content"] for m in captured["messages"] if m["role"] == "system"
    ]
    assert system_blocks, "expected a system message informing the LLM of the failure"
    assert any(
        "every query failed" in block and "techdocs" in block and "tell" in block.lower()
        for block in system_blocks
    )
    assert result == "I was unable to retrieve context from your data sources."
