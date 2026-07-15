from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


@dataclass(frozen=True)
class RateLimitRule:
    prefix: str
    limit: int
    window_seconds: int
    methods: frozenset[str] = frozenset({"POST"})

    def matches(self, request: Request) -> bool:
        if request.method.upper() not in self.methods:
            return False
        return request.url.path.startswith(self.prefix)


DEFAULT_RATE_LIMIT_RULES = (
    RateLimitRule("/auth/login", 10, 60),
    RateLimitRule("/auth/voice-login", 20, 60),
    RateLimitRule("/auth/verify", 20, 60),
    RateLimitRule("/api/chat/stream", 30, 60),
    RateLimitRule("/voiceprints/enroll", 10, 60),
    RateLimitRule("/voiceprints/owner-override-enroll", 5, 300),
    RateLimitRule("/meeting/process", 6, 300),
    RateLimitRule("/graph/outbox/replay", 10, 60),
    RateLimitRule("/dispatch/to-assistx", 30, 60),
    RateLimitRule("/session/clear", 30, 60),
)


class InMemoryRateLimiter:
    """Small per-process sliding-window limiter for local/edge deployments.

    This protects sensitive endpoints from accidental retry loops and basic abuse.
    It is intentionally not a distributed quota system; use an edge proxy or Redis
    limiter if Sophia is scaled across multiple app instances.
    """

    def __init__(self, rules: Iterable[RateLimitRule] = DEFAULT_RATE_LIMIT_RULES, *, max_keys: int = 4096) -> None:
        self.rules = tuple(rules)
        self.max_keys = max_keys
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def client_key(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        if forwarded:
            return forwarded
        return request.client.host if request.client else "unknown"

    def matching_rule(self, request: Request) -> RateLimitRule | None:
        for rule in self.rules:
            if rule.matches(request):
                return rule
        return None

    def check(self, request: Request) -> tuple[bool, RateLimitRule | None, int, int]:
        rule = self.matching_rule(request)
        if not rule:
            return True, None, 0, 0
        now = time.monotonic()
        key = (self.client_key(request), rule.prefix)
        bucket = self._hits[key]
        cutoff = now - rule.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        remaining = max(0, rule.limit - len(bucket))
        if remaining <= 0:
            retry_after = max(1, int(rule.window_seconds - (now - bucket[0]))) if bucket else rule.window_seconds
            return False, rule, 0, retry_after
        bucket.append(now)
        self._prune_if_needed(now)
        return True, rule, rule.limit - len(bucket), 0

    def _prune_if_needed(self, now: float) -> None:
        if len(self._hits) <= self.max_keys:
            return
        stale_keys = []
        for key, bucket in self._hits.items():
            if not bucket or now - bucket[-1] > 3600:
                stale_keys.append(key)
        for key in stale_keys[: max(0, len(self._hits) - self.max_keys)]:
            self._hits.pop(key, None)


def install_rate_limiter(app: FastAPI, limiter: InMemoryRateLimiter | None = None) -> InMemoryRateLimiter:
    limiter = limiter or InMemoryRateLimiter()
    app.state.rate_limiter = limiter

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next: Callable):
        allowed, rule, remaining, retry_after = limiter.check(request)
        if not allowed and rule:
            return JSONResponse(
                status_code=429,
                content={
                    "ok": False,
                    "error": "rate_limited",
                    "detail": f"Too many requests for {rule.prefix}; retry later.",
                    "retry_after_seconds": retry_after,
                },
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(rule.limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Window-Seconds": str(rule.window_seconds),
                },
            )
        response = await call_next(request)
        if rule:
            response.headers.setdefault("X-RateLimit-Limit", str(rule.limit))
            response.headers.setdefault("X-RateLimit-Remaining", str(remaining))
            response.headers.setdefault("X-RateLimit-Window-Seconds", str(rule.window_seconds))
        return response

    return limiter
