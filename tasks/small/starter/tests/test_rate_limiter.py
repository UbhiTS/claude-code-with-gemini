"""Unit tests for Token-Bucket Rate Limiter & Tiered Burst CLI."""

from __future__ import annotations

import math
import pytest

from rate_limiter import TIER_CONFIGS, RateLimitDecision, TieredRateLimiter, TokenBucket
from cli import simulate_workload


class ManualClock:
    def __init__(self, initial: float = 0.0) -> None:
        self.current = initial

    def __call__(self) -> float:
        return self.current

    def advance(self, seconds: float) -> None:
        self.current += seconds


def test_bucket_validation() -> None:
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_rate=1.0)
    with pytest.raises(ValueError):
        TokenBucket(capacity=10, refill_rate=-2.0)

    bucket = TokenBucket(capacity=10, refill_rate=1.0)
    with pytest.raises(ValueError):
        bucket.allow_request(cost=0)


def test_refill_and_cap() -> None:
    clock = ManualClock(100.0)
    bucket = TokenBucket(capacity=10.0, refill_rate=2.0, clock=clock)

    d1 = bucket.allow_request(6.0)
    assert d1 == RateLimitDecision(allowed=True, remaining=4.0, retry_after=0.0)

    # Advance 2 seconds -> +4.0 tokens -> 8.0 tokens
    clock.advance(2.0)
    d2 = bucket.allow_request(5.0)
    assert d2 == RateLimitDecision(allowed=True, remaining=3.0, retry_after=0.0)

    # Advance 20 seconds -> should cap at capacity (10.0)
    clock.advance(20.0)
    d3 = bucket.allow_request(4.0)
    assert d3 == RateLimitDecision(allowed=True, remaining=6.0, retry_after=0.0)


def test_rejection_and_retry_after() -> None:
    clock = ManualClock(0.0)
    bucket = TokenBucket(capacity=10.0, refill_rate=2.0, clock=clock)

    assert bucket.allow_request(8.0).allowed is True
    # 2.0 tokens left; requesting 5.0 needs 3.0 more tokens at 2.0/sec -> 1.5s retry_after
    rejected = bucket.allow_request(5.0)
    assert rejected.allowed is False
    assert rejected.remaining == 2.0
    assert rejected.retry_after == pytest.approx(1.5)

    # Request exceeding max bucket capacity returns inf retry_after
    impossible = bucket.allow_request(25.0)
    assert impossible.allowed is False
    assert math.isinf(impossible.retry_after)


def test_tiered_limiter_and_unknown_tier() -> None:
    clock = ManualClock(0.0)
    limiter = TieredRateLimiter(clock=clock)

    with pytest.raises(ValueError):
        limiter.register_tenant("bad-tenant", tier="ultra-platinum")

    # Default lazy tenant registration uses "free" tier (capacity=10.0)
    d_free = limiter.check("lazy-tenant", cost=9.0)
    assert d_free.allowed is True
    assert d_free.remaining == 1.0

    limiter.register_tenant("pro-tenant", tier="pro")
    d_pro = limiter.check("pro-tenant", cost=80.0)
    assert d_pro.allowed is True
    assert d_pro.remaining == 20.0


def test_cli_simulate_workload() -> None:
    summary = simulate_workload(
        tenant_id="acme",
        tier="free",
        requests=[6.0, 6.0, 4.0],
        step_seconds=1.0,
    )
    assert summary["tenant_id"] == "acme"
    assert summary["tier"] == "free"
    assert summary["total"] == 3
    assert summary["allowed"] == 2
    assert summary["rejected"] == 1
    assert summary["final_remaining"] == pytest.approx(2.0)
