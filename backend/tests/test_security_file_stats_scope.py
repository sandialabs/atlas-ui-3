from starlette.testclient import TestClient

from main import app


def test_user_stats_enforces_self_scope(monkeypatch):
    client = TestClient(app)

    # user stats for self should pass (even if backend returns arbitrary data)
    r_ok = client.get(
        "/api/users/alice@example.com/files/stats",
        headers={"X-User-Email": "alice@example.com"},
    )
    # Endpoint may error if mock S3 down; allow 200 or 500, but importantly not 403
    assert r_ok.status_code in (200, 500)

    # user cannot view others
    r_forbid = client.get(
        "/api/users/bob@example.com/files/stats",
        headers={"X-User-Email": "alice@example.com"},
    )
    assert r_forbid.status_code == 403
