"""Circuit breaker state machine (CLOSED -> OPEN -> HALF_OPEN -> CLOSED)."""

from __future__ import annotations

import time
from typing import Callable, Optional


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold <= 0:
            raise ValueError("failure_threshold must be > 0")
        self.failure_threshold = int(failure_threshold)
        self.recovery_timeout = float(recovery_timeout)
        self._clock = clock
        self.state = "CLOSED"
        self.failure_count = 0
        self.opened_at: Optional[float] = None

    def allow_execution(self) -> bool:
        if self.state == "CLOSED":
            return True
        if self.state == "OPEN":
            now = self._clock()
            if self.opened_at is not None and (now - self.opened_at) >= self.recovery_timeout:
                self.state = "HALF_OPEN"
                return True
            return False
        # HALF_OPEN allows a probe request
        return True

    def record_success(self) -> None:
        # BUG 4: Does not reset state to CLOSED when in HALF_OPEN
        self.failure_count = 0

    def record_failure(self) -> None:
        now = self._clock()
        self.failure_count += 1
        if self.state == "HALF_OPEN" or self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            self.opened_at = now
