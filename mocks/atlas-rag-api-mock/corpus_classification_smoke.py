"""Smoke-test per-corpus classifications against this mock (issue #1035).

Drives the real ``UnifiedRAGService`` over HTTP against a running mock, with
and without ``legacy_corpus_classifications``, on both API versions, in a
``Public`` session. The mock sends ``compliance_level`` per corpus
(``product-knowledge`` is Public, the others Internal), so with the flag on
only ``product-knowledge`` may be queried.

Usage (from the repository root):

    ATLAS_RAG_MOCK_PORT=8002 ATLAS_RAG_SHARED_KEY=mock-key \\
        python mocks/atlas-rag-api-mock/main.py &
    PYTHONPATH=. python mocks/atlas-rag-api-mock/corpus_classification_smoke.py \\
        --url http://127.0.0.1:8002 --key mock-key

Exits non-zero if any outcome differs from the expected one.
"""

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import atlas.core.compliance as compliance
import atlas.domain.unified_rag_service as unified
from atlas.core.compliance import (
    ComplianceLevelManager,
    reset_active_compliance_context,
    set_active_compliance_context,
)
from atlas.domain.errors import DataSourcePermissionError
from atlas.modules.config.models import RAGSourcesConfig

USER = "test@test.com"
MESSAGES = [{"role": "user", "content": "How do I get started?"}]


def _use_levels() -> None:
    path = Path(tempfile.mkdtemp()) / "compliance-levels.json"
    path.write_text(json.dumps({"levels": [{"name": "Public"}, {"name": "Internal"}]}))
    manager = ComplianceLevelManager(path)
    compliance.get_compliance_manager = lambda: manager
    unified.get_compliance_manager = lambda: manager


def _service(url: str, key: str, api_version: str, legacy: bool):
    cm = SimpleNamespace(rag_sources_config=RAGSourcesConfig(sources={"mock": {
        "type": "http", "url": url, "bearer_token": key, "api_version": api_version,
        "allowed_data_classifications": ["Public", "Internal"],
        "legacy_corpus_classifications": legacy,
    }}))
    service = unified.UnifiedRAGService(config_manager=cm)
    client = service._get_http_client("mock", cm.rag_sources_config.sources["mock"])
    calls = []
    original = client.discover_data_sources

    async def counted(*args, **kwargs):
        calls.append(1)
        return await original(*args, **kwargs)

    client.discover_data_sources = counted
    return service, calls


async def _outcome(coro) -> str:
    try:
        await coro
        return "ALLOWED"
    except DataSourcePermissionError as exc:
        return exc.code


async def run(url: str, key: str) -> int:
    _use_levels()
    failures = 0
    for api_version in ("v1", "v2"):
        for legacy in (False, True):
            service, calls = _service(url, key, api_version, legacy)
            print(f"== api_version={api_version} legacy_corpus_classifications={legacy} session=Public")
            discovered = await service.discover_data_sources(USER, user_compliance_level="Public")
            offered = sorted(s["id"] for s in discovered[0]["sources"]) if discovered else []
            print(f"  discovery offers: {offered}")
            # An Internal corpus is out of bounds only when its level is read.
            internal = "DATA_SOURCE_COMPLIANCE_MISMATCH" if legacy else "ALLOWED"
            expected = {
                "single product-knowledge": "ALLOWED",
                "single technical-docs": internal,
                "batch product-knowledge + technical-docs": internal,
                "single invented corpus": "DATA_SOURCE_NOT_LISTED",
            }
            token = set_active_compliance_context("Public", enforce=True)
            try:
                actual = {
                    "single product-knowledge": await _outcome(
                        service.query_rag(USER, "mock:product-knowledge", MESSAGES)),
                    "single technical-docs": await _outcome(
                        service.query_rag(USER, "mock:technical-docs", MESSAGES)),
                    "batch product-knowledge + technical-docs": await _outcome(
                        service.query_rag_batch(
                            USER, ["mock:product-knowledge", "mock:technical-docs"], MESSAGES)),
                    "single invented corpus": await _outcome(
                        service.query_rag(USER, "mock:invented", MESSAGES)),
                }
            finally:
                reset_active_compliance_context(token)
            for name, outcome in actual.items():
                ok = outcome == expected[name]
                failures += not ok
                print(f"  {name}: {outcome}{'' if ok else f'  (expected {expected[name]})'}")
            print(f"  discovery round trips: {len(calls)}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://127.0.0.1:8002")
    parser.add_argument("--key", default="test-atlas-rag-token")
    args = parser.parse_args()
    failures = asyncio.run(run(args.url, args.key))
    print("OK" if not failures else f"{failures} unexpected outcome(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
