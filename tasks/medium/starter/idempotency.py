"""Idempotency store with TTL eviction and payload fingerprint validation."""

from __future__ import annotations

import dataclasses
import hashlib
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from models import ChargeResponse, IdempotencyConflictError


def hash_payload(canonical_payload: str) -> str:
    return hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()


@dataclass
class IdempotencyRecord:
    key: str
    payload_hash: str
    response: ChargeResponse
    created_at: float


class IdempotencyStore:
    def __init__(
        self,
        ttl_seconds: float = 3600.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock
        self._records: Dict[str, IdempotencyRecord] = {}

    def get_or_verify(self, key: str, canonical_payload: str) -> Optional[ChargeResponse]:
        now = self._clock()
        rec = self._records.get(key)
        if rec is None:
            return None
        if (now - rec.created_at) >= self.ttl_seconds:
            del self._records[key]
            return None

        incoming_hash = hash_payload(canonical_payload)
        # BUG 3: Fails to raise IdempotencyConflictError when payload_hash differs
        if rec.payload_hash != incoming_hash:
            return None

        return dataclasses.replace(rec.response, replayed=True)

    def put(self, key: str, canonical_payload: str, response: ChargeResponse) -> None:
        now = self._clock()
        self._records[key] = IdempotencyRecord(
            key=key,
            payload_hash=hash_payload(canonical_payload),
            response=dataclasses.replace(response, replayed=False),
            created_at=now,
        )
