"""Payment & Order Fulfillment orchestration service."""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from circuit_breaker import CircuitBreaker
from idempotency import IdempotencyStore
from models import (
    ChargeRequest,
    ChargeResponse,
    CircuitOpenError,
    GatewayError,
    WebhookSignatureError,
)
from security import verify_webhook


class PaymentFulfillmentService:
    def __init__(
        self,
        gateway: Callable[[ChargeRequest], ChargeResponse],
        idempotency_store: IdempotencyStore,
        circuit_breaker: CircuitBreaker,
        webhook_secret: str = "whsec_test_secret",
    ) -> None:
        self._gateway = gateway
        self._idempotency = idempotency_store
        self._breaker = circuit_breaker
        self._webhook_secret = webhook_secret
        self.audit_log: List[Dict[str, Any]] = []

    def process_charge(self, request: ChargeRequest, idempotency_key: str) -> ChargeResponse:
        if request.amount_cents <= 0:
            raise ValueError("amount_cents must be positive")

        canonical = request.canonical_payload()
        cached = self._idempotency.get_or_verify(idempotency_key, canonical)
        if cached is not None:
            return cached

        if not self._breaker.allow_execution():
            raise CircuitOpenError(f"Circuit breaker is {self._breaker.state} for order {request.order_id}")

        try:
            response = self._gateway(request)
        except GatewayError:
            # BUG 5: Forgets to record failure on circuit breaker when GatewayError is raised
            raise

        self._breaker.record_success()
        self._idempotency.put(idempotency_key, canonical, response)
        self.audit_log.append(
            {
                "event": "charge_succeeded",
                "order_id": request.order_id,
                "transaction_id": response.transaction_id,
                "amount_cents": request.amount_cents,
                "idempotency_key": idempotency_key,
            }
        )
        return response

    def handle_webhook(
        self,
        payload_body: str,
        signature_header: str,
        timestamp: int,
        now: int,
    ) -> Dict[str, Any]:
        if not verify_webhook(
            secret=self._webhook_secret,
            payload_body=payload_body,
            signature_header=signature_header,
            timestamp=timestamp,
            now=now,
        ):
            raise WebhookSignatureError("Invalid webhook signature or timestamp")
        self.audit_log.append({"event": "webhook_verified", "timestamp": timestamp})
        return {"verified": True, "timestamp": timestamp}
