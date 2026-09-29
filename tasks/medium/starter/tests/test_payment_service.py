"""Comprehensive unit tests for the Payment & Order Fulfillment Microservice."""

from __future__ import annotations

import pytest

from circuit_breaker import CircuitBreaker
from idempotency import IdempotencyStore
from models import (
    ChargeRequest,
    ChargeResponse,
    CircuitOpenError,
    GatewayError,
    IdempotencyConflictError,
    WebhookSignatureError,
)
from security import compute_signature, verify_webhook
from service import PaymentFulfillmentService


class ManualClock:
    def __init__(self, initial: float = 1000.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_webhook_signature_and_replay_protection() -> None:
    secret = "whsec_live_987654"
    payload = '{"event":"payment.captured","order_id":"ord_101"}'
    ts = 1700000000
    sig = compute_signature(secret, ts, payload)
    assert sig.startswith("v1=")

    # Different timestamp must produce a different signature
    assert compute_signature(secret, ts + 1, payload) != sig

    # Valid within 300s window
    assert verify_webhook(secret, payload, sig, timestamp=ts, now=ts + 120) is True
    # Stale timestamp (>300s old)
    assert verify_webhook(secret, payload, sig, timestamp=ts, now=ts + 301) is False
    # Future-skewed timestamp (>300s in future)
    assert verify_webhook(secret, payload, sig, timestamp=ts + 500, now=ts) is False


def test_idempotency_replay_conflict_and_ttl() -> None:
    clock = ManualClock(100.0)
    store = IdempotencyStore(ttl_seconds=60.0, clock=clock)
    req1 = ChargeRequest(order_id="ord_1", customer_id="cust_1", amount_cents=2500)
    resp1 = ChargeResponse(
        transaction_id="tx_1",
        order_id="ord_1",
        status="SUCCEEDED",
        amount_cents=2500,
        currency="USD",
    )

    assert store.get_or_verify("idem-1", req1.canonical_payload()) is None
    store.put("idem-1", req1.canonical_payload(), resp1)

    replayed = store.get_or_verify("idem-1", req1.canonical_payload())
    assert replayed is not None
    assert replayed.replayed is True
    assert replayed.transaction_id == "tx_1"

    # Reusing key with different amount must raise IdempotencyConflictError
    req_mutated = ChargeRequest(order_id="ord_1", customer_id="cust_1", amount_cents=9900)
    with pytest.raises(IdempotencyConflictError):
        store.get_or_verify("idem-1", req_mutated.canonical_payload())

    # After TTL expiration, key can be reused cleanly
    clock.advance(61.0)
    assert store.get_or_verify("idem-1", req_mutated.canonical_payload()) is None


def test_circuit_breaker_lifecycle_and_service_integration() -> None:
    clock = ManualClock(500.0)
    store = IdempotencyStore(ttl_seconds=300.0, clock=clock)
    breaker = CircuitBreaker(failure_threshold=2, recovery_timeout=30.0, clock=clock)

    should_fail = True
    call_counter = 0

    def mock_gateway(req: ChargeRequest) -> ChargeResponse:
        nonlocal call_counter
        call_counter += 1
        if should_fail:
            raise GatewayError("Upstream processor 503")
        return ChargeResponse(
            transaction_id=f"tx_{call_counter}",
            order_id=req.order_id,
            status="SUCCEEDED",
            amount_cents=req.amount_cents,
            currency=req.currency,
        )

    svc = PaymentFulfillmentService(
        gateway=mock_gateway,
        idempotency_store=store,
        circuit_breaker=breaker,
    )
    req = ChargeRequest(order_id="ord_50", customer_id="cust_9", amount_cents=4200)

    # 2 consecutive gateway failures trip the circuit breaker to OPEN
    with pytest.raises(GatewayError):
        svc.process_charge(req, idempotency_key="k1")
    assert breaker.state == "CLOSED"

    with pytest.raises(GatewayError):
        svc.process_charge(req, idempotency_key="k2")
    assert breaker.state == "OPEN"

    # Subsequent request is blocked immediately by CircuitOpenError without calling gateway
    prev_calls = call_counter
    with pytest.raises(CircuitOpenError):
        svc.process_charge(req, idempotency_key="k3")
    assert call_counter == prev_calls

    # Advance past recovery timeout -> HALF_OPEN -> success resets to CLOSED
    clock.advance(35.0)
    should_fail = False
    res = svc.process_charge(req, idempotency_key="k4")
    assert res.status == "SUCCEEDED"
    assert res.replayed is False
    assert breaker.state == "CLOSED"
    assert breaker.failure_count == 0

    # Replaying k4 returns cached response without invoking gateway again
    replay_res = svc.process_charge(req, idempotency_key="k4")
    assert replay_res.replayed is True
    assert replay_res.transaction_id == res.transaction_id
    assert len(svc.audit_log) == 1
