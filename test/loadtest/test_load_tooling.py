"""Unit tests for the load tooling itself: no replicas, no podman, ~2 seconds.

Run: ``python -m pytest test/loadtest/test_load_tooling.py`` (from the repo
root, with this checkout on PYTHONPATH). ``test_load_smoke.py`` is the
end-to-end check that needs Redis and Postgres.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from atlas_load.__main__ import PROFILE_DIR, apply_scale, load_profile  # noqa: E402
from atlas_load.metrics import Metrics, classify_error, percentile  # noqa: E402
from atlas_load.report import build_report, check_slo, render_text  # noqa: E402
from atlas_load.runner import Runner, directive  # noqa: E402

PHASE_KINDS = {name[len("phase_"):] for name in dir(Runner) if name.startswith("phase_")}


def test_percentile_interpolates_between_ranks():
    assert percentile([], 50) is None
    assert percentile([5.0], 95) == 5.0
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile(range(1, 101), 95) == pytest.approx(95.05)
    assert percentile([3, 1, 2], 100) == 3
    with pytest.raises(ValueError):
        percentile([1], 101)


def test_failures_do_not_count_toward_latency_percentiles():
    metrics = Metrics()
    for value in (0.1, 0.2, 0.3):
        metrics.record("op", value)
    metrics.record("op", 0.001, "http_503", "fast failure")
    summary = metrics.summary()["operations"]["op"]
    assert summary["count"] == 4 and summary["ok"] == 3 and summary["errors"] == 1
    assert summary["error_rate"] == 0.25
    assert summary["p50_ms"] == 200.0
    assert summary["error_classes"] == {"http_503": 1}


def test_classify_error_prefers_http_status():
    class Response:
        status_code = 429

    class StatusError(Exception):
        response = Response()

    assert classify_error(StatusError()) == "http_429"
    assert classify_error(TimeoutError()) == "TimeoutError"


def test_peaks_flatten_nested_samples():
    metrics = Metrics()
    metrics.add_sample({"client": {"open_connections": 3}, "server": {"replicas": {"A": {"rss_mb": 10.0}}}})
    metrics.add_sample({"client": {"open_connections": 7}, "server": {"replicas": {"A": {"rss_mb": 9.0}}}})
    peaks = metrics.peaks()
    assert peaks["client.open_connections"] == 7
    assert peaks["server.replicas.A.rss_mb"] == 10.0
    assert "t" not in peaks


def test_slo_wildcard_min_count_and_counters():
    summary = {
        "operations": {
            "ws_connect": {"error_rate": 0.0, "p95_ms": 12.0, "ok": 10},
            "chat_turn": {"error_rate": 0.2, "p95_ms": 900.0, "ok": 8},
        },
        "counters": {"oidc_relogin": 3},
    }
    profile = {"slo": {"max_error_rate": {"*": 0, "chat_turn": 0.25},
                       "p95_ms": {"ws_connect": 10, "never_ran": 5},
                       "min_count": {"chat_turn": 8},
                       "counters_max": {"oidc_relogin": 2}}}
    results = {c["check"]: c["pass"] for c in check_slo(profile, summary)}
    assert results["ws_connect.error_rate <= 0"] is True
    assert results["chat_turn.error_rate <= 0.25"] is True  # explicit limit beats "*"
    assert results["ws_connect.p95_ms <= 10"] is False
    assert results["never_ran.p95_ms <= 5"] is False  # an op that never ran cannot pass
    assert results["chat_turn.ok >= 8"] is True
    assert results["oidc_relogin <= 2"] is False


def test_scale_multiplies_counts_including_nested_phases():
    profile = {"users": 10, "slo": {"min_count": {"ws_connect": 250}}, "phases": [
        {"kind": "connect", "connections": 20, "ramp_seconds": 2},
        {"kind": "parallel", "phases": [{"kind": "api_runs", "runs": 3, "users": 1}]},
    ]}
    scaled = apply_scale(profile, 2.5)
    assert scaled["users"] == 25
    assert scaled["phases"][0]["connections"] == 50
    assert scaled["phases"][0]["ramp_seconds"] == 2  # durations are not scaled
    assert scaled["phases"][1]["phases"][0] == {"kind": "api_runs", "runs": 8, "users": 2}
    assert scaled["slo"]["min_count"]["ws_connect"] == 625
    assert apply_scale(profile, 0.2)["slo"]["min_count"]["ws_connect"] == 50
    assert profile["phases"][0]["connections"] == 20  # the input is untouched


@pytest.mark.parametrize("path", sorted(PROFILE_DIR.glob("*.json")), ids=lambda p: p.stem)
def test_bundled_profiles_are_valid(path):
    profile = load_profile(path.stem)
    assert profile["description"]
    assert isinstance(profile["seed"], int)
    assert profile.get("auth", "header") in ("header", "oidc")
    phases = list(profile["phases"])
    assert phases
    while phases:
        phase = phases.pop()
        assert phase["kind"] in PHASE_KINDS, phase
        phases.extend(phase.get("phases", []))
    assert profile.get("slo"), "every bundled profile states what passing means"


def test_directive_matches_the_scripted_mock_grammar():
    assert directive({}, "x") == "load test turn x"
    text = directive({"tool": "sleep", "sleep": 2, "steps": 3}, "t0")
    assert text.startswith("label=t0 ")
    assert {"tool=sleep", "sleep=2", "steps=3", "words=20", "stream_ms=10"} <= set(text.split())


def test_report_renders_and_passes_through_slo():
    class Env:
        local = False

        def describe(self):
            return {"mode": "remote", "targets": ["http://x"], "auth": "header"}

        def mock_stats(self):
            return {}

    metrics = Metrics()
    metrics.record("ws_connect", 0.01)
    metrics.add_sample({"client": {"open_connections": 1}})
    report = build_report({"name": "t", "seed": 1, "slo": {"max_error_rate": {"*": 0}}}, Env(), metrics, 1.0)
    assert report["passed"] is True
    json.dumps(report)  # the report must be plain JSON
    text = render_text(report)
    assert "ws_connect" in text and "RESULT: PASS" in text


def _load_mock_oidc(monkeypatch):
    monkeypatch.setenv("OIDC_ISSUER", "http://idp.test")
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "s3")
    spec = importlib.util.spec_from_file_location("mock_oidc_under_test", HERE / "atlas_load" / "mock_oidc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mock_idp_code_flow_and_token_exchange(monkeypatch):
    from fastapi.testclient import TestClient

    idp = _load_mock_oidc(monkeypatch)
    client = TestClient(idp.app)
    verifier = "v" * 50
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    redirect = client.get("/authorize", params={
        "response_type": "code", "client_id": "atlas-loadtest", "redirect_uri": "http://atlas/cb",
        "code_challenge": challenge, "nonce": "n-1", "state": "st", "login_hint": "loaduser-0007",
    }, follow_redirects=False)
    assert redirect.status_code == 302
    query = parse_qs(urlsplit(redirect.headers["location"]).query)
    assert query["state"] == ["st"]
    basic = base64.b64encode(b"atlas-loadtest:s3").decode()
    tokens = client.post("/token", headers={"Authorization": f"Basic {basic}"}, data={
        "grant_type": "authorization_code", "code": query["code"][0],
        "redirect_uri": "http://atlas/cb", "code_verifier": verifier}).json()
    public_key = idp.PRIVATE_KEY.public_key()
    id_claims = jwt.decode(tokens["id_token"], public_key, algorithms=["RS256"], audience="atlas-loadtest")
    assert id_claims["nonce"] == "n-1" and id_claims["email"] == "loaduser-0007@load.test"

    # The code is single-use, and the wrong client secret is refused.
    reused = client.post("/token", headers={"Authorization": f"Basic {basic}"}, data={
        "grant_type": "authorization_code", "code": query["code"][0],
        "redirect_uri": "http://atlas/cb", "code_verifier": verifier})
    assert reused.status_code == 400
    bad = base64.b64encode(b"atlas-loadtest:nope").decode()
    assert client.post("/token", headers={"Authorization": f"Basic {bad}"},
                       data={"grant_type": "refresh_token"}).status_code == 401

    exchanged = client.post("/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "client_id": "atlas-loadtest",
        "client_secret": "s3", "subject_token": tokens["access_token"],
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token", "audience": "mock-litellm"}).json()
    claims = jwt.decode(exchanged["access_token"], public_key, algorithms=["RS256"], audience="mock-litellm")
    assert claims["sub"] == "loaduser-0007"
    refused = client.post("/token", data={
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange", "client_id": "atlas-loadtest",
        "client_secret": "s3", "subject_token": tokens["access_token"],
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token", "audience": "somewhere-else"})
    assert refused.status_code == 400
    stats = client.get("/stats").json()
    assert stats["authorization_code"] == 2 and stats["authorization_code_failed"] == 1
    assert stats["token_exchange"] == 2 and stats["token_exchange_failed"] == 1


def test_same_seed_gives_the_same_plan():
    class Env:
        urls = ["http://a", "http://b", "http://c"]

    def plan(seed):
        runner = Runner({"seed": seed, "phases": []}, Env(), Metrics())
        picks = [runner._pick(list(range(100)), 0.3) for _ in range(3)]
        return picks, [runner.replica_url(i) for i in range(5)]

    assert plan(1129) == plan(1129)
    assert plan(1129)[0] != plan(1130)[0]
    assert plan(1)[1] == ["http://a", "http://b", "http://c", "http://a", "http://b"]


def test_server_side_closes_are_counted_unless_expected():
    import asyncio

    import websockets.exceptions
    from atlas_load.clients import Connection

    class ClosingSocket:
        """Delivers one frame, then the server closes the connection."""

        close_code, close_reason = 1011, "overloaded"

        def __aiter__(self):
            return self

        def __init__(self):
            self.sent = False

        async def __anext__(self):
            if not self.sent:
                self.sent = True
                return '{"type": "token_stream"}'
            raise websockets.exceptions.ConnectionClosedError(None, None)

    async def scenario(expected: bool) -> Metrics:
        metrics = Metrics()
        conn = Connection(user=None, base_url="http://x", metrics=metrics)
        conn.ws = ClosingSocket()
        conn.expect_close = expected
        await conn._read()
        frames = [conn._frames.get_nowait() for _ in range(conn._frames.qsize())]
        assert frames[-1] == {"type": "__closed__", "code": 1011, "reason": "overloaded"}
        return metrics

    assert asyncio.run(scenario(False)).counters["connections_closed_by_server"] == 1
    assert "connections_closed_by_server" not in asyncio.run(scenario(True)).counters
