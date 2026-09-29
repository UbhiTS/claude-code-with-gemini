# Task (Medium): Payment & Order Fulfillment Microservice

## Objective
Fix and complete the multi-module Payment & Order Fulfillment Microservice (`models.py`, `security.py`, `idempotency.py`, `circuit_breaker.py`, `service.py`) so that all unit tests in `tests/test_payment_service.py` pass with zero failures.

## Architecture & Bug Report
1. **Webhook HMAC Verification (`security.py`)**:
   - `compute_signature(secret: str, timestamp: int, payload_body: str) -> str`:
     - Must compute HMAC-SHA256 over the canonical string `f"{timestamp}.{payload_body}"` and return `"v1=" + hex_digest`.
   - `verify_webhook(secret: str, payload_body: str, signature_header: str, timestamp: int, now: int, tolerance_seconds: int = 300) -> bool`:
     - Must reject stale OR future-skewed timestamps where `abs(now - timestamp) > tolerance_seconds` (currently only checks `now - timestamp > tolerance_seconds`).
     - Must compare the expected signature using `hmac.compare_digest`.

2. **Idempotency Store (`idempotency.py`)**:
   - `IdempotencyStore` tracks `IdempotencyRecord(key, payload_hash, response, created_at)` with TTL expiration (`ttl_seconds`).
   - If the same `key` is reused before TTL expiry:
     - If `payload_hash` matches, return the cached `response` with `replayed=True`.
     - If `payload_hash` differs, raise `IdempotencyConflictError` (currently overwrites instead of raising!).
   - If the existing record has expired (`now - record.created_at >= ttl_seconds`), evict it and allow storing a fresh record.

3. **Circuit Breaker (`circuit_breaker.py`)**:
   - States: `"CLOSED"`, `"OPEN"`, `"HALF_OPEN"`.
   - Transitions from `"CLOSED"` to `"OPEN"` when `failure_count >= failure_threshold`.
   - When `"OPEN"`, if `now - opened_at >= recovery_timeout`, transition to `"HALF_OPEN"` on `allow_execution()` and return `True`; otherwise return `False`.
   - When `"HALF_OPEN"`, a single `record_success()` must reset `state = "CLOSED"` and `failure_count = 0`; a `record_failure()` must immediately re-open the circuit (`state = "OPEN"`, `opened_at = now`).

4. **Payment Fulfillment Service (`service.py`)**:
   - `process_charge(request: ChargeRequest, idempotency_key: str) -> ChargeResponse`:
     - Check idempotency first.
     - Check circuit breaker (`allow_execution()`): if open, raise `CircuitOpenError` without calling gateway.
     - Call `gateway(request)`: on success, record circuit breaker success, store idempotency response, and append an audit log entry; on `GatewayError`, record circuit breaker failure and re-raise.
