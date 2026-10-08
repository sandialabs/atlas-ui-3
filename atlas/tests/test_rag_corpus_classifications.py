"""Per-corpus classifications for HTTP RAG sources (issue #1035).

Covers the opt-in legacy ``compliance_level`` mapping, the shared effective
calculation, and query-time enforcement of each requested corpus across the
single, batch, v1/v2 and agent-tool paths.
"""

import asyncio
import importlib
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from atlas.core.compliance import (
    ComplianceLevelManager,
    reset_active_compliance_context,
    reset_model_classification_floor,
    set_active_compliance_context,
    set_model_classification_floor,
)
from atlas.domain.errors import DataSourcePermissionError
from atlas.domain.rag_corpus_classifications import (
    CORPUS_DISCOVERY_FAILURE_SECONDS,
    CORPUS_METADATA_MIN_REFRESH_SECONDS,
    CorpusMetadataCache,
    corpus_classifications,
    legacy_corpus_classifications,
)
from atlas.domain.unified_rag_service import UnifiedRAGService
from atlas.modules.config.models import RAGSourceConfig, RAGSourcesConfig
from atlas.modules.rag.client import DataSource, RAGResponse

USER = "u@test.com"


@pytest.fixture
def manager(tmp_path, monkeypatch):
    path = tmp_path / "compliance-levels.json"
    path.write_text(json.dumps({"levels": [
        {"name": "UUR", "aliases": ["Unclassified"]},
        {"name": "ITAR"},
        {"name": "ECI"},
    ]}))
    mgr = ComplianceLevelManager(path)
    for target in (
        "atlas.core.compliance.get_compliance_manager",
        "atlas.domain.unified_rag_service.get_compliance_manager",
    ):
        monkeypatch.setattr(target, lambda: mgr)
    return mgr


def _server(legacy=True, classifications=("UUR", "ITAR"), api_version="v1"):
    return RAGSourceConfig(
        type="http", url="http://rag", api_version=api_version,
        allowed_data_classifications=list(classifications) if classifications is not None else None,
        legacy_corpus_classifications=legacy,
    )


def _ds(id, **fields):
    return DataSource(id=id, label=id, **fields)


# --- The effective calculation ------------------------------------------------


def test_missing_compliance_level_is_not_classified_cui():
    ds = DataSource(id="a", label="A")
    assert ds.compliance_level is None
    assert legacy_corpus_classifications(ds) is None


def test_camel_case_legacy_level_is_read(manager):
    ds = DataSource(**{"id": "a", "label": "A", "complianceLevel": "UUR"})
    assert corpus_classifications(ds, _server()) == ["UUR"]


def test_legacy_mapping_is_opt_in(manager):
    ds = _ds("a", compliance_level="UUR")
    assert corpus_classifications(ds, _server(legacy=False)) == ["UUR", "ITAR"]
    assert corpus_classifications(ds, _server(legacy=True)) == ["UUR"]


@pytest.mark.parametrize("fields, expected", [
    # Explicit modern list wins over the legacy level, including an empty one.
    ({"compliance_level": "UUR", "allowed_data_classifications": ["ITAR"]}, ["ITAR"]),
    ({"compliance_level": "UUR", "allowed_data_classifications": []}, []),
    # A missing or null legacy level inherits the server's list.
    ({}, ["UUR", "ITAR"]),
    ({"compliance_level": None}, ["UUR", "ITAR"]),
    # Aliases survive narrowing and resolve to the canonical level later.
    ({"compliance_level": "Unclassified"}, ["Unclassified"]),
    # Unknown, out-of-server, blank and malformed levels approve nothing.
    ({"compliance_level": "SECRET"}, []),
    ({"compliance_level": "ECI"}, []),
    ({"compliance_level": "  "}, []),
    ({"compliance_level": 7}, []),
    ({"compliance_level": ["UUR"]}, []),
])
def test_legacy_effective_classifications(manager, fields, expected):
    assert corpus_classifications(_ds("a", **fields), _server()) == expected


def test_server_without_declaration_leaves_corpus_undeclared(manager):
    ds = _ds("a", compliance_level="UUR")
    assert corpus_classifications(ds, _server(classifications=None)) is None


def test_alias_is_approved_for_its_canonical_level(manager):
    effective = corpus_classifications(_ds("a", compliance_level="Unclassified"), _server())
    assert manager.classification_permits("UUR", effective)
    assert not manager.classification_permits("ITAR", effective)


# --- Service harness ----------------------------------------------------------


class _Backend:
    """A fake HTTP RAG client: discovery answers plus recorded queries."""

    def __init__(self, sources):
        self.sources = sources
        self.discover_data_sources = AsyncMock(side_effect=lambda user: list(self.sources))
        self.query_rag = AsyncMock(return_value=RAGResponse(content="v1", metadata=None))
        self.query_v2 = AsyncMock(return_value=RAGResponse(content="v2", metadata=None))

    @property
    def queried(self):
        return self.query_rag.await_count + self.query_v2.await_count


def _service(backend, **server_kwargs):
    cm = SimpleNamespace(rag_sources_config=RAGSourcesConfig(sources={
        "legacy": _server(**server_kwargs),
    }))
    service = UnifiedRAGService(config_manager=cm)
    service._get_http_client = lambda name, cfg: backend
    return service


MIXED = [
    _ds("uur_legacy", compliance_level="UUR"),
    _ds("itar_legacy", compliance_level="ITAR"),
    _ds("modern_itar", compliance_level="UUR", allowed_data_classifications=["ITAR"]),
    _ds("silent"),
]


class _Turn:
    """The trusted per-turn compliance context ChatService sets."""

    def __init__(self, level):
        self.level = level

    def __enter__(self):
        self.token = set_active_compliance_context(self.level, enforce=bool(self.level))

    def __exit__(self, *exc):
        reset_active_compliance_context(self.token)


# --- Discovery ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_applies_legacy_levels_and_exposes_effective_list(manager):
    service = _service(_Backend(MIXED))
    info = await service._discover_http_source("legacy", service.config_manager.rag_sources_config.sources["legacy"], USER, "ITAR")
    offered = {s["id"]: s for s in info["sources"]}
    assert sorted(offered) == ["itar_legacy", "modern_itar", "silent"]
    assert offered["itar_legacy"]["allowedDataClassifications"] == ["ITAR"]
    assert offered["silent"]["allowedDataClassifications"] == ["UUR", "ITAR"]
    assert offered["silent"]["complianceLevel"] is None
    assert offered["itar_legacy"]["complianceLevel"] == "ITAR"


@pytest.mark.asyncio
async def test_discovery_without_opt_in_keeps_previous_behavior(manager):
    service = _service(_Backend(MIXED), legacy=False)
    info = await service._discover_http_source("legacy", service.config_manager.rag_sources_config.sources["legacy"], USER, "ITAR")
    assert sorted(s["id"] for s in info["sources"]) == ["itar_legacy", "modern_itar", "silent", "uur_legacy"]


# --- Query time ----------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("api_version", ["v1", "v2"])
async def test_single_query_to_hidden_corpus_is_denied(manager, api_version):
    backend = _Backend(MIXED)
    service = _service(backend, api_version=api_version)
    with _Turn("ITAR"):
        with pytest.raises(DataSourcePermissionError, match="'uur_legacy' is not approved") as exc:
            await service.query_rag(USER, "legacy:uur_legacy", [{"role": "user", "content": "q"}])
        assert exc.value.code == "DATA_SOURCE_COMPLIANCE_MISMATCH"
        assert backend.queried == 0
        response = await service.query_rag(USER, "legacy:itar_legacy", [{"role": "user", "content": "q"}])
    assert response.content == api_version
    assert backend.queried == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("api_version", ["v1", "v2"])
async def test_batch_with_one_disallowed_corpus_runs_nothing(manager, api_version):
    backend = _Backend(MIXED)
    service = _service(backend, api_version=api_version)
    with _Turn("ITAR"), pytest.raises(DataSourcePermissionError) as exc:
        await service.query_rag_batch(
            USER, ["legacy:itar_legacy", "legacy:uur_legacy", "legacy:silent"],
            [{"role": "user", "content": "q"}],
        )
    # Only the offending corpus is named; nothing reached the backend.
    assert "'uur_legacy'" in str(exc.value)
    assert "'itar_legacy'" not in str(exc.value)
    assert backend.queried == 0


@pytest.mark.asyncio
async def test_allowed_batch_runs(manager):
    backend = _Backend(MIXED)
    service = _service(backend)
    with _Turn("ITAR"):
        await service.query_rag_batch(
            USER, ["legacy:itar_legacy", "legacy:modern_itar"], [{"role": "user", "content": "q"}],
        )
    assert backend.query_rag.await_count == 1


@pytest.mark.asyncio
async def test_corpus_unknown_to_discovery_is_denied(manager):
    backend = _Backend(MIXED)
    service = _service(backend)
    with _Turn("ITAR"), pytest.raises(DataSourcePermissionError, match="not offered to you"):
        await service.query_rag(USER, "legacy:invented", [{"role": "user", "content": "q"}])
    assert backend.queried == 0


@pytest.mark.asyncio
async def test_unanswered_discovery_fails_closed(manager):
    backend = _Backend([])
    service = _service(backend)
    with _Turn("UUR"), pytest.raises(DataSourcePermissionError, match="did not answer") as exc:
        await service.query_rag(USER, "legacy:silent", [{"role": "user", "content": "q"}])
    assert exc.value.code == "DATA_SOURCE_UNVERIFIED"
    assert str(exc.value).startswith("The data source 'silent' is not verifiable right now:")

    backend.discover_data_sources.side_effect = RuntimeError("down")
    with _Turn("UUR"), pytest.raises(DataSourcePermissionError, match="did not answer"):
        await service.query_rag(USER, "legacy:silent", [{"role": "user", "content": "q"}])
    assert backend.queried == 0


@pytest.mark.asyncio
async def test_floor_only_turn_survives_a_discovery_outage(manager):
    """No level selected: the server floor passed, so an outage is not a denial
    -- unless the server opted in to per-corpus levels, which then must be read."""
    backend = _Backend([])
    backend.discover_data_sources.side_effect = RuntimeError("down")
    token = set_model_classification_floor(["UUR"])
    try:
        await _service(backend, legacy=False).query_rag(
            USER, "legacy:silent", [{"role": "user", "content": "q"}])
        assert backend.queried == 1
        with pytest.raises(DataSourcePermissionError) as exc:
            await _service(backend, legacy=True).query_rag(
                USER, "legacy:silent", [{"role": "user", "content": "q"}])
        assert exc.value.code == "DATA_SOURCE_UNVERIFIED"
    finally:
        reset_model_classification_floor(token)
    assert backend.queried == 1


@pytest.mark.asyncio
async def test_unclassified_turn_without_floor_skips_corpus_checks(manager):
    backend = _Backend([])
    service = _service(backend)
    await service.query_rag(USER, "legacy:anything", [{"role": "user", "content": "q"}])
    backend.discover_data_sources.assert_not_called()
    assert backend.queried == 1


@pytest.mark.asyncio
async def test_model_floor_applies_to_corpora_with_their_own_list(manager):
    backend = _Backend(MIXED)
    service = _service(backend)
    token = set_model_classification_floor(["UUR"])
    try:
        with pytest.raises(DataSourcePermissionError, match="selected model"):
            await service.query_rag(USER, "legacy:itar_legacy", [{"role": "user", "content": "q"}])
        # Inherits the server's list, which already shares UUR with the model.
        await service.query_rag(USER, "legacy:silent", [{"role": "user", "content": "q"}])
        await service.query_rag(USER, "legacy:uur_legacy", [{"role": "user", "content": "q"}])
    finally:
        reset_model_classification_floor(token)
    assert backend.queried == 2


@pytest.mark.asyncio
async def test_denial_logs_do_not_carry_classification_labels(manager, caplog):
    service = _service(_Backend(MIXED))
    with caplog.at_level(logging.WARNING), _Turn("ITAR"), pytest.raises(DataSourcePermissionError):
        await service.query_rag(USER, "legacy:uur_legacy", [{"role": "user", "content": "q"}])
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "uur_legacy" in logged
    assert "ITAR" not in logged.replace("uur_legacy", "")
    assert "UUR" not in logged.replace("uur_legacy", "")


# --- Cache ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_answer_is_reused_by_the_query(manager):
    backend = _Backend(MIXED)
    service = _service(backend)
    cfg = service.config_manager.rag_sources_config.sources["legacy"]
    await service._discover_http_source("legacy", cfg, USER, "ITAR")
    with _Turn("ITAR"):
        await service.query_rag(USER, "legacy:itar_legacy", [{"role": "user", "content": "q"}])
    assert backend.discover_data_sources.await_count == 1


@pytest.mark.asyncio
async def test_stale_cache_is_refreshed(manager, monkeypatch):
    backend = _Backend([_ds("doc", compliance_level="ITAR")])
    service = _service(backend)
    with _Turn("ITAR"):
        await service.query_rag(USER, "legacy:doc", [{"role": "user", "content": "q"}])

    # The backend reclassifies the corpus; once the answer expires it is used.
    backend.sources = [_ds("doc", compliance_level="UUR")]
    clock = [CorpusMetadataCache._now() + service._corpus_metadata.ttl_seconds + 1]
    monkeypatch.setattr(CorpusMetadataCache, "_now", staticmethod(lambda: clock[0]))
    with _Turn("ITAR"), pytest.raises(DataSourcePermissionError):
        await service.query_rag(USER, "legacy:doc", [{"role": "user", "content": "q"}])
    assert backend.discover_data_sources.await_count == 2


def _advance(monkeypatch, seconds):
    clock = [CorpusMetadataCache._now() + seconds]
    monkeypatch.setattr(CorpusMetadataCache, "_now", staticmethod(lambda: clock[0]))


@pytest.mark.asyncio
async def test_corpus_missing_from_cache_triggers_one_refresh(manager, monkeypatch):
    backend = _Backend([_ds("old", compliance_level="ITAR")])
    service = _service(backend)
    with _Turn("ITAR"):
        await service.query_rag(USER, "legacy:old", [{"role": "user", "content": "q"}])
        backend.sources = [_ds("old", compliance_level="ITAR"), _ds("new", compliance_level="ITAR")]
        _advance(monkeypatch, CORPUS_METADATA_MIN_REFRESH_SECONDS + 1)
        await service.query_rag(USER, "legacy:new", [{"role": "user", "content": "q"}])
    assert backend.discover_data_sources.await_count == 2


@pytest.mark.asyncio
async def test_unlisted_corpus_has_its_own_code(manager):
    service = _service(_Backend(MIXED))
    with _Turn("ITAR"), pytest.raises(DataSourcePermissionError) as exc:
        await service.query_rag(USER, "legacy:invented", [{"role": "user", "content": "q"}])
    assert exc.value.code == "DATA_SOURCE_NOT_LISTED"
    assert "try again later" not in str(exc.value).lower()


@pytest.mark.asyncio
async def test_outage_is_not_retried_on_every_query(manager, monkeypatch):
    """A failed discovery is remembered briefly, so queries do not each wait on it."""
    backend = _Backend([])
    backend.discover_data_sources.side_effect = RuntimeError("down")
    service = _service(backend)
    with _Turn("UUR"):
        for _ in range(3):
            with pytest.raises(DataSourcePermissionError, match="did not answer"):
                await service.query_rag(USER, "legacy:silent", [{"role": "user", "content": "q"}])
        assert backend.discover_data_sources.await_count == 1
        # Recovered backend: asked again once the failure window passes.
        backend.discover_data_sources.side_effect = lambda user: list(MIXED)
        _advance(monkeypatch, CORPUS_DISCOVERY_FAILURE_SECONDS + 1)
        await service.query_rag(USER, "legacy:silent", [{"role": "user", "content": "q"}])
    assert backend.discover_data_sources.await_count == 2


@pytest.mark.asyncio
async def test_floor_only_pass_without_metadata_is_logged(manager, caplog):
    """Under the floor, a corpus discovery does not list is let through on the
    server's floor check, with a WARNING naming it (server without the legacy
    opt-in; with it, the corpus is refused -- see the outage test above)."""
    backend = _Backend(MIXED)
    service = _service(backend, legacy=False)
    token = set_model_classification_floor(["UUR"])
    try:
        with caplog.at_level(logging.WARNING):
            await service.query_rag(USER, "legacy:unlisted", [{"role": "user", "content": "q"}])
    finally:
        reset_model_classification_floor(token)
    assert backend.queried == 1
    assert any(
        "under the model floor without per-corpus metadata" in r.getMessage()
        and "unlisted" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_concurrent_misses_share_one_discovery(manager):
    backend = _Backend(MIXED)
    release = asyncio.Event()

    async def slow(user):
        await release.wait()
        return list(MIXED)

    backend.discover_data_sources.side_effect = slow
    service = _service(backend)
    with _Turn("ITAR"):
        queries = asyncio.gather(*[
            service.query_rag(USER, "legacy:itar_legacy", [{"role": "user", "content": "q"}])
            for _ in range(3)
        ])
        await asyncio.sleep(0)
        release.set()
        await queries
    assert backend.discover_data_sources.await_count == 1
    assert backend.queried == 3


@pytest.mark.asyncio
async def test_hung_discovery_is_bounded(manager, monkeypatch):
    monkeypatch.setattr(
        "atlas.domain.unified_rag_service.CORPUS_DISCOVERY_TIMEOUT_SECONDS", 0.05
    )
    backend = _Backend(MIXED)

    async def hang(user):
        await asyncio.sleep(30)

    backend.discover_data_sources.side_effect = hang
    service = _service(backend)
    with _Turn("ITAR"), pytest.raises(DataSourcePermissionError) as exc:
        await asyncio.wait_for(
            service.query_rag(USER, "legacy:itar_legacy", [{"role": "user", "content": "q"}]),
            timeout=5,
        )
    assert exc.value.code == "DATA_SOURCE_UNVERIFIED"


@pytest.mark.asyncio
async def test_refusals_name_every_corpus_in_plural(manager):
    backend = _Backend([])
    service = _service(backend)
    with _Turn("UUR"), pytest.raises(DataSourcePermissionError) as exc:
        await service.query_rag_batch(
            USER, ["legacy:a", "legacy:b"], [{"role": "user", "content": "q"}])
    assert str(exc.value).startswith(
        "The data sources 'a' and 'b' are not verifiable right now: the RAG backend "
        "did not answer, so their data classifications cannot be checked."
    )
    service = _service(_Backend(MIXED))
    with _Turn("ITAR"), pytest.raises(DataSourcePermissionError) as exc:
        await service.query_rag_batch(
            USER, ["legacy:x", "legacy:y"], [{"role": "user", "content": "q"}])
    assert "'x' and 'y' are not offered to you by the RAG backend, so they cannot" in str(exc.value)


@pytest.mark.asyncio
async def test_failed_picker_discovery_opens_the_failure_window(manager):
    backend = _Backend([])
    service = _service(backend)
    cfg = service.config_manager.rag_sources_config.sources["legacy"]
    assert await service._discover_http_source("legacy", cfg, USER, "UUR") is None
    with _Turn("UUR"), pytest.raises(DataSourcePermissionError, match="did not answer"):
        await service.query_rag(USER, "legacy:silent", [{"role": "user", "content": "q"}])
    # The query did not ask the backend again within the window.
    assert backend.discover_data_sources.await_count == 1


@pytest.mark.asyncio
async def test_unknown_corpus_cannot_force_a_refresh_per_query(manager):
    backend = _Backend(MIXED)
    service = _service(backend)
    with _Turn("ITAR"):
        for _ in range(3):
            with pytest.raises(DataSourcePermissionError, match="not offered to you"):
                await service.query_rag(USER, "legacy:invented", [{"role": "user", "content": "q"}])
    assert backend.discover_data_sources.await_count == 1


@pytest.mark.asyncio
async def test_failed_refresh_keeps_a_fresh_answer(manager, monkeypatch):
    backend = _Backend([_ds("doc", compliance_level="ITAR")])
    service = _service(backend)
    with _Turn("ITAR"):
        await service.query_rag(USER, "legacy:doc", [{"role": "user", "content": "q"}])
        backend.sources = []  # discovery now fails (reported as an empty list)
        _advance(monkeypatch, CORPUS_METADATA_MIN_REFRESH_SECONDS + 1)
        with pytest.raises(DataSourcePermissionError, match="did not answer") as exc:
            await service.query_rag(USER, "legacy:other", [{"role": "user", "content": "q"}])
        assert exc.value.code == "DATA_SOURCE_UNVERIFIED"
        # The still-fresh answer was not discarded by the failed refresh.
        await service.query_rag(USER, "legacy:doc", [{"role": "user", "content": "q"}])
    assert backend.queried == 2


def test_cache_is_per_user_and_invalidates():
    cache = CorpusMetadataCache()
    cache.store("s", "a@x", [_ds("c")])
    assert cache.lookup("s", "b@x") is None
    assert "c" in cache.lookup("s", "a@x")
    cache.store("s", "a@x", [])  # an empty answer is not an answer
    assert "c" in cache.lookup("s", "a@x")
    cache.store("s", "b@x", [])
    assert cache.lookup("s", "b@x") is None
    cache.invalidate("s")
    assert cache.lookup("s", "a@x") is None


def test_cache_is_bounded():
    cache = CorpusMetadataCache(max_entries=2)
    for user in ("a", "b", "c"):
        cache.store("s", user, [_ds("c")])
    assert cache.lookup("s", "a") is None
    assert cache.lookup("s", "c") is not None


# --- Agent tool path -------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_tool_cannot_reach_a_disallowed_corpus(manager, monkeypatch):
    """atlas_search goes through the same query-time corpus check.

    The tool bounds itself by discovery; here discovery is made to offer every
    corpus (a stale allow-list), and the query path must still refuse.
    """
    from atlas.domain.messages.models import ToolCall
    from atlas.modules.mcp_tools import client as mcp_client
    from atlas.modules.mcp_tools.client import MCPToolManager

    settings = mcp_client.config_manager.app_settings
    monkeypatch.setattr(settings, "feature_rag_enabled", True, raising=False)
    monkeypatch.setattr(settings, "feature_atlas_rag_tools_enabled", True, raising=False)

    backend = _Backend(MIXED)
    service = _service(backend)

    async def stale_discovery(username, user_compliance_level=None, **_):
        return [{"server": "legacy", "sources": [{"id": ds.id} for ds in MIXED]}]

    service.discover_data_sources = stale_discovery
    factory = SimpleNamespace(
        get_unified_rag_service=lambda: service, get_rag_mcp_service=lambda: None,
    )
    monkeypatch.setattr(
        importlib.import_module("atlas.infrastructure.app_factory"), "app_factory", factory
    )

    manager_ = MCPToolManager(config_path="/tmp/atlas-noop-mcp.json")
    with _Turn("ITAR"):
        result = await manager_.execute_tool(
            ToolCall(id="c1", name="atlas_search", arguments={"query": "q"}),
            context={
                "user_email": USER,
                "compliance_level": "ITAR",
                "selected_data_sources": ["legacy:uur_legacy", "legacy:itar_legacy"],
            },
        )
    assert backend.queried == 0
    assert result.success is False
    (error,) = json.loads(result.content)["results"]["errors"]
    assert "'uur_legacy' is not approved" in error["error"]


def test_rag_source_config_flag_defaults_off():
    assert RAGSourceConfig(type="http", url="http://x").legacy_corpus_classifications is False
