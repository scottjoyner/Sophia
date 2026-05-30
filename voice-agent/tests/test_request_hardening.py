from fastapi import FastAPI
from fastapi.testclient import TestClient

from voice_agent.server.request_hardening import install_request_hardening, make_request_id


def test_make_request_id_reuses_reasonable_existing_id():
    assert make_request_id("abc-123") == "abc-123"


def test_make_request_id_generates_when_missing_or_too_long():
    assert len(make_request_id()) == 32
    assert len(make_request_id("x" * 200)) == 32


def test_request_hardening_adds_headers():
    app = FastAPI()
    install_request_hardening(app)

    @app.get("/demo")
    async def demo():
        return {"ok": True}

    client = TestClient(app)
    res = client.get("/demo", headers={"X-Request-ID": "req-test"})

    assert res.status_code == 200
    assert res.headers["X-Request-ID"] == "req-test"
    assert "X-Response-Time-ms" in res.headers
    assert res.headers["X-Content-Type-Options"] == "nosniff"
    assert res.headers["X-Frame-Options"] == "DENY"
    assert res.headers["Referrer-Policy"] == "no-referrer"
    assert "microphone" in res.headers["Permissions-Policy"]
