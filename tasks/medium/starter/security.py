"""Webhook HMAC-SHA256 signature verification."""

from __future__ import annotations

import hashlib
import hmac


def compute_signature(secret: str, timestamp: int, payload_body: str) -> str:
    # BUG 1: Missing timestamp prefix f"{timestamp}.{payload_body}"
    digest = hmac.new(
        secret.encode("utf-8"),
        payload_body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"v1={digest}"


def verify_webhook(
    secret: str,
    payload_body: str,
    signature_header: str,
    timestamp: int,
    now: int,
    tolerance_seconds: int = 300,
) -> bool:
    if not secret or not signature_header:
        return False
    # BUG 2: Does not use abs(now - timestamp) to reject future-skewed timestamps
    if (now - timestamp) > tolerance_seconds:
        return False
    expected = compute_signature(secret, timestamp, payload_body)
    return hmac.compare_digest(expected, signature_header.strip())
