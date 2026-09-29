# Task (Large): Multi-Module Cloud FinOps Anomaly Detector & Full-Stack Dashboard

## Objective
Fix and complete the 5-module Cloud FinOps Cost Anomaly Detection Platform (`ingestion.py`, `detector.py`, `repository.py`, `api.py`, `dashboard.html`) so that all unit and integration tests in `tests/test_finops_platform.py` pass with zero failures.

## Architecture & Defect Report
1. **Billing Telemetry Ingestion (`ingestion.py`)**:
   - `normalize_billing_events(raw_events: list[dict]) -> list[BillingRecord]`:
     - Deduplicate events by `event_id` (preserving the first seen record).
     - Convert non-USD costs to USD using `FX_RATES_TO_USD` (`"USD": 1.0`, `"EUR": 1.08`, `"GBP": 1.27`, `"JPY": 0.0067`) by multiplying `amount * FX_RATES_TO_USD[currency]` (currently divides instead of multiplying and does not deduplicate `event_id`!).
     - Subtract `credits_usd` (after FX conversion of `credits` or direct `credits_usd`) so `net_cost_usd = max(0.0, round(gross_usd - credits_usd, 4))`.

2. **Statistical Anomaly Detector (`detector.py`)**:
   - `detect_anomalies(daily_series: list[DailyServiceSpend], window: int = 7, z_threshold: float = 2.5, min_spend_delta_usd: float = 50.0) -> list[SpendAnomaly]`:
     - Group records by `(project_id, service)` sorted chronologically by `date`.
     - For each point at index `i >= window`, compute baseline mean and population standard deviation (`pstdev`) over the preceding `window` days `series[i - window : i]` (currently includes the current day `series[i]` inside the baseline window, contaminating the baseline!).
     - If `std == 0.0`, treat `z_score = 99.0` when `current - mean >= min_spend_delta_usd` else `0.0`.
     - Flag an anomaly when `z_score >= z_threshold` AND `(current - mean) >= min_spend_delta_usd`.
     - Classify severity: `"CRITICAL"` if `z_score >= 4.0` or `delta_usd >= 500.0`, else `"HIGH"`.

3. **SQLite Repository (`repository.py`)**:
   - `FinOpsRepository` persists daily service spend and detected anomalies in an in-memory or file-backed SQLite database.
   - `acknowledge_anomaly(anomaly_id: str, owner: str) -> bool` must update `acknowledged = 1` and `acknowledged_by = owner` and return `True` if a row was updated (`cursor.rowcount > 0`), or `False` if `anomaly_id` does not exist.

4. **REST API Handler (`api.py`)**:
   - `FinOpsAPI` exposes `.ingest_and_analyze(events: list[dict]) -> dict`, `.get_summary() -> dict`, and `.acknowledge(anomaly_id: str, owner: str) -> dict`.
   - `.get_summary()` must return `{"total_net_spend_usd": ..., "anomaly_count": ..., "unacknowledged_count": ..., "by_service": ..., "anomalies": ...}` where `unacknowledged_count` counts only anomalies with `acknowledged is False` (currently returns total anomalies instead of unacknowledged).
