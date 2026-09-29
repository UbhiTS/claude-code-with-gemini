"""Token-Bucket API Rate Limiter with Tiered Tenant Quotas."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict


TIER_CONFIGS: Dict[str, Dict[str, float]] = {
    "free": {"capacity": 10.0, "refill_rate": 1.0},
    "pro": {"capacity": 100.0, "refill_rate": 10.0},
    "enterprise": {"capacity": 1000.0, "refill_rate": 100.0},
}


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    remaining: float
    retry_after: float


class TokenBucket:
    """Deterministic token bucket supporting injectable monotonic clock."""

    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # BUG 1: Missing validation for non-positive capacity or refill_rate
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self._clock = clock
        self.tokens = float(capacity)
        self._last_refill = self._clock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        self._last_refill = now
        # BUG 2: Subtracts instead of adding and fails to cap at self.capacity
        self.tokens = self.tokens - (elapsed * self.refill_rate)

    def allow_request(self, cost: float = 1.0) -> RateLimitDecision:
        if cost <= 0:
            raise ValueError("Request cost must be positive")
        self._refill()
        if cost > self.capacity:
            return RateLimitDecision(
                allowed=False,
                remaining=round(self.tokens, 6),
                retry_after=float("inf"),
            )
        if self.tokens >= cost:
            self.tokens -= cost
            return RateLimitDecision(
                allowed=True,
                remaining=round(self.tokens, 6),
                retry_after=0.0,
            )
        # BUG 3: retry_after calculation is inverted
        deficit = cost - self.tokens
        retry_after = round(self.refill_rate / deficit, 6)
        return RateLimitDecision(
            allowed=False,
            remaining=round(self.tokens, 6),
            retry_after=retry_after,
        )


class TieredRateLimiter:
    """Multi-tenant rate limiter with free, pro, and enterprise tiers."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: Dict[str, TokenBucket] = {}
        self._tiers: Dict[str, str] = {}

    def register_tenant(self, tenant_id: str, tier: str = "free") -> None:
        # BUG 4: Does not validate unknown tier names
        cfg = TIER_CONFIGS.get(tier, TIER_CONFIGS["free"])
        self._tiers[tenant_id] = tier
        self._buckets[tenant_id] = TokenBucket(
            capacity=cfg["capacity"],
            refill_rate=cfg["refill_rate"],
            clock=self._clock,
        )

    def check(self, tenant_id: str, cost: float = 1.0) -> RateLimitDecision:
        if tenant_id not in self._buckets:
            self.register_tenant(tenant_id, "free")
        return self._buckets[tenant_id].allow_request(cost=cost)
