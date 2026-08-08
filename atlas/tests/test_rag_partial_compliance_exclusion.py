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
        "denied:corpus": _denial("denied"),
    })

    successful, exclusions = await caller._query_all_rag_sources(
        ["allowed:corpus", "denied:corpus"],
        service,
        "user@test.com",
        [{"role": "user", "content": "q"}],
    )

    assert [response.content for _label, response in successful] == ["good context"]
    assert len(exclusions) == 1
    assert "denied" in exclusions[0]


@pytest.mark.asyncio
async def test_all_groups_denied_raises():
    """With nothing left to answer from, degrading silently is the bug."""
    caller = _make_caller()
    service = _rag_service({
        "a:corpus": _denial("a"),
        "b:corpus": _denial("b"),
    })

    with pytest.raises(DataSourcePermissionError):
        await caller._query_all_rag_sources(
            ["a:corpus", "b:corpus"],
            service,
            "user@test.com",
            [{"role": "user", "content": "q"}],
        )


@pytest.mark.asyncio
async def test_non_permission_failures_still_degrade_quietly():
    """An unreachable backend is not an authorization problem: keep the old
    best-effort behaviour rather than failing the turn."""
    caller = _make_caller()
    service = _rag_service({
        "allowed:corpus": RAGResponse(content="good context"),
        "broken:corpus": RuntimeError("connection refused"),
    })

    successful, exclusions = await caller._query_all_rag_sources(
        ["allowed:corpus", "broken:corpus"],
        service,
        "user@test.com",
        [{"role": "user", "content": "q"}],
    )

    assert len(successful) == 1
    assert exclusions == []


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
        "denied:corpus": _denial("denied"),
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
