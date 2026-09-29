"""Domain models and exceptions for Payment & Order Fulfillment Microservice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class PaymentError(Exception):
    """Base exception for payment processing errors."""


class IdempotencyConflictError(PaymentError):
    """Raised when an idempotency key is reused with a different request payload."""


class CircuitOpenError(PaymentError):
    """Raised when downstream gateway calls are blocked by an open circuit breaker."""


class GatewayError(PaymentError):
    """Raised when the upstream payment gateway fails."""


class WebhookSignatureError(PaymentError):
    """Raised when webhook signature or timestamp verification fails."""


@dataclass(frozen=True)
class ChargeRequest:
    order_id: str
    customer_id: str
    amount_cents: int
    currency: str = "USD"

    def canonical_payload(self) -> str:
        return f"{self.order_id}:{self.customer_id}:{self.amount_cents}:{self.currency.upper()}"


@dataclass(frozen=True)
class ChargeResponse:
    transaction_id: str
    order_id: str
    status: str
    amount_cents: int
    currency: str
    replayed: bool = False
    error_message: Optional[str] = None
