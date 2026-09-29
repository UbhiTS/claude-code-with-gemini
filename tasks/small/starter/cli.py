"""CLI workload simulator for the Token-Bucket Rate Limiter."""

from __future__ import annotations

import argparse
import json
from typing import Any, Dict, List

from rate_limiter import TieredRateLimiter


def simulate_workload(
    tenant_id: str,
    tier: str,
    requests: List[float],
    step_seconds: float = 0.0,
) -> Dict[str, Any]:
    now = 1000.0

    def fake_clock() -> float:
        return now

    limiter = TieredRateLimiter(clock=fake_clock)
    limiter.register_tenant(tenant_id, tier=tier)

    allowed_count = 0
    rejected_count = 0
    last_remaining = 0.0

    for idx, cost in enumerate(requests):
        if idx > 0 and step_seconds > 0:
            now += step_seconds
        decision = limiter.check(tenant_id, cost=cost)
        if decision.allowed:
            allowed_count += 1
        else:
            rejected_count += 1
        last_remaining = decision.remaining

    # BUG 5: Returns wrong keys ("ok" instead of "allowed")
    return {
        "tenant_id": tenant_id,
        "tier": tier,
        "total": len(requests),
        "ok": allowed_count,
        "rejected": rejected_count,
        "final_remaining": last_remaining,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate rate limiter workload")
    parser.add_argument("--tenant", default="acme-corp")
    parser.add_argument("--tier", default="free")
    parser.add_argument("--requests", nargs="*", type=float, default=[2.0, 4.0, 5.0])
    parser.add_argument("--step", type=float, default=1.0)
    args = parser.parse_args()
    summary = simulate_workload(args.tenant, args.tier, args.requests, args.step)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
