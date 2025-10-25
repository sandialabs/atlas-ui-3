from core.http_client import create_rag_client


def test_http_client_stub_returns_object():
    client = create_rag_client("http://example", 5.0)
    assert client is not None
    # ensure object has query coroutine
    assert hasattr(client, "query")
