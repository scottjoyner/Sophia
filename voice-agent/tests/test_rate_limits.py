from fastapi import FastAPI
from fastapi.testclient import TestClient

from voice_agent.server.rate_limits import InMemoryRateLimiter, RateLimitRule, install_rate_limiter


def test_rate_limiter_allows_then_rejects_sensitive_route():
    app = FastAPI()
    limiter = InMemoryRateLimiter([RateLimitRule("/auth/verify", 2, 60)])
    install_rate_limiter(app, limiter)

    @app.post("/auth/verify")
    async def verify():
        return {"ok": True}

    client = TestClient(app)

    first = client.post("/auth/verify")
    second = client.post("/auth/verify")
    third = client.post("/auth/verify")

    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert second.status_code == 200
    assert second.headers["X-RateLimit-Remaining"] == "0"
    assert third.status_code == 429
    assert third.json()["error"] == "rate_limited"
    assert "Retry-After" in third.headers


def test_rate_limiter_does_not_limit_unmatched_routes():
    app = FastAPI()
    limiter = InMemoryRateLimiter([RateLimitRule("/auth/verify", 1, 60)])
    install_rate_limiter(app, limiter)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    client = TestClient(app)
    for _ in range(5):
        res = client.get("/healthz")
        assert res.status_code == 200
        assert "X-RateLimit-Limit" not in res.headers
