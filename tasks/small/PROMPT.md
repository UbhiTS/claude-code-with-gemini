# Task (Small): Token-Bucket API Rate Limiter & Tiered Burst CLI

## Objective
Fix and complete the token-bucket rate limiter (`rate_limiter.py`) and CLI inspector (`cli.py`) so that all unit tests in `tests/test_rate_limiter.py` pass with zero failures.

## Requirements
1. **`TokenBucket` (`rate_limiter.py`)**:
   - Initialize with `capacity: float`, `refill_rate: float` (tokens per second), and optional `clock` callable (defaults to `time.monotonic`).
   - Raise `ValueError` if `capacity <= 0` or `refill_rate <= 0`.
   - Start with a full bucket (`tokens = float(capacity)`).
   - `_refill()` must replenish tokens based on elapsed time `(now - last_refill) * refill_rate`, capped at `capacity` (currently buggy: it forgets to cap at `capacity` and uses subtraction instead of addition).
   - `allow_request(cost: float = 1.0) -> RateLimitDecision`:
     - Raise `ValueError` if `cost <= 0`.
     - If `cost > capacity`, immediately reject (`allowed=False`, `remaining=self.tokens`, `retry_after=float("inf")`).
     - If `self.tokens >= cost`, deduct `cost` and return `RateLimitDecision(allowed=True, remaining=round(self.tokens, 6), retry_after=0.0)`.
     - Otherwise, do NOT deduct tokens; return `RateLimitDecision(allowed=False, remaining=round(self.tokens, 6), retry_after=round((cost - self.tokens) / self.refill_rate, 6))`.

2. **`TieredRateLimiter` (`rate_limiter.py`)**:
   - Support three tiers in `TIER_CONFIGS`:
     - `"free"`: `capacity=10.0`, `refill_rate=1.0`
     - `"pro"`: `capacity=100.0`, `refill_rate=10.0`
     - `"enterprise"`: `capacity=1000.0`, `refill_rate=100.0`
   - `register_tenant(tenant_id: str, tier: str = "free")` must validate that `tier` is in `TIER_CONFIGS` (raising `ValueError` for unknown tiers).
   - `check(tenant_id: str, cost: float = 1.0) -> RateLimitDecision` must lazily create a `"free"` tier bucket if `tenant_id` was not previously registered.

3. **CLI Summary (`cli.py`)**:
   - `simulate_workload(tenant_id: str, tier: str, requests: list[float], step_seconds: float = 0.0) -> dict`:
     - Run each request cost against a `TieredRateLimiter` advancing a simulated clock by `step_seconds` between requests.
     - Return `{"tenant_id": tenant_id, "tier": tier, "total": len(requests), "allowed": allowed_count, "rejected": rejected_count, "final_remaining": last_remaining}`.
