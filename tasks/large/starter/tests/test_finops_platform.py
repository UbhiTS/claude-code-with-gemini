"""End-to-end unit and integration tests for the Cloud FinOps Anomaly Platform."""

from __future__ import annotations

import pytest

from api import FinOpsAPI
from detector import detect_anomalies
from ingestion import DailyServiceSpend, aggregate_daily_spend, normalize_billing_events
from repository import FinOpsRepository


def test_fx_normalization_and_deduplication() -> None:
    raw = [
        {
            "event_id": "ev-100",
            "date": "2026-09-01",
            "project_id": "proj-ai",
            "service": "Vertex AI",
            "cost": 100.0,
            "credits": 10.0,
            "currency": "EUR",  # 100 * 1.08 = 108.0 USD gross, 10.8 USD credit -> 97.20 USD net
        },
        {
            "event_id": "ev-100",  # Duplicate event_id must be ignored
            "date": "2026-09-01",
            "project_id": "proj-ai",
            "service": "Vertex AI",
            "cost": 999.0,
            "credits": 0.0,
            "currency": "USD",
        },
        {
            "event_id": "ev-101",
            "date": "2026-09-01",
            "project_id": "proj-ai",
            "service": "Vertex AI",
            "cost": 100.0,
            "credits": 0.0,
            "currency": "GBP",  # 100 * 1.27 = 127.0 USD
        },
    ]
    records = normalize_billing_events(raw)
    assert len(records) == 2
    assert records[0].gross_cost_usd == pytest.approx(108.0)
    assert records[0].credits_usd == pytest.approx(10.8)
    assert records[0].net_cost_usd == pytest.approx(97.2)
    assert records[1].net_cost_usd == pytest.approx(127.0)

    daily = aggregate_daily_spend(records)
    assert len(daily) == 1
    assert daily[0].net_cost_usd == pytest.approx(224.2)


def test_rolling_zscore_baseline_excludes_current_day() -> None:
    # 7 days of steady spend around $100 (+/- $2), followed by a $165 spike on Day 8.
    # Preceding 7-day baseline: [98, 102, 99, 101, 100, 98, 102] -> mean=100.0, pstdev=1.6036
    # Day 8 ($165): delta=$65.0, z_score = 65.0 / 1.6036 = 40.53 (CRITICAL)
    # Wait: if current day ($165) is erroneously included in the 7-day window, std explodes to ~22.6 and z_score drops!
    # Let's test a subtle spike where off-by-one window inclusion causes the detector to miss the anomaly:
    # Baseline 7 days: [100, 100, 100, 100, 100, 100, 100] -> std=0.0 -> any spike >= $50 gets z_score=99.0!
    # If Day 8 ($160) is included in the window [100,100,100,100,100,100,160], mean=108.57, std=21.0, z=2.44 (< 2.5 threshold -> MISSED!).
    series = [
        DailyServiceSpend(date=f"2026-09-0{d}", project_id="p1", service="BigQuery", net_cost_usd=100.0)
        for d in range(1, 8)
    ]
    series.append(
        DailyServiceSpend(date="2026-09-08", project_id="p1", service="BigQuery", net_cost_usd=160.0)
    )

    anomalies = detect_anomalies(series, window=7, z_threshold=2.5, min_spend_delta_usd=50.0)
    assert len(anomalies) == 1
    anom = anomalies[0]
    assert anom.date == "2026-09-08"
    assert anom.baseline_mean_usd == pytest.approx(100.0)
    assert anom.baseline_std_usd == pytest.approx(0.0)
    assert anom.delta_usd == pytest.approx(60.0)
    assert anom.severity == "CRITICAL"


def test_end_to_end_api_and_acknowledgement_workflow() -> None:
    api = FinOpsAPI(window=5, z_threshold=2.5, min_spend_delta_usd=50.0)
    events = [
        {
            "event_id": f"e-{d}",
            "date": f"2026-09-0{d}",
            "project_id": "prod-core",
            "service": "Cloud Run",
            "cost": 100.0,
            "credits": 0.0,
            "currency": "USD",
        }
        for d in range(1, 6)
    ]
    events.append(
        {
            "event_id": "e-6",
            "date": "2026-09-06",
            "project_id": "prod-core",
            "service": "Cloud Run",
            "cost": 220.0,
            "credits": 0.0,
            "currency": "USD",
        }
    )

    res = api.ingest_and_analyze(events)
    assert res["detected_anomalies"] == 1

    summary1 = api.get_summary()
    assert summary1["anomaly_count"] == 1
    assert summary1["unacknowledged_count"] == 1
    anomaly_id = summary1["anomalies"][0]["anomaly_id"]

    # Non-existent anomaly ID must return acknowledged=False
    bad_ack = api.acknowledge("anom:does-not-exist", owner="sre@example.com")
    assert bad_ack["acknowledged"] is False

    # Valid anomaly acknowledgement updates unacknowledged_count to 0
    ok_ack = api.acknowledge(anomaly_id, owner="finops@example.com")
    assert ok_ack["acknowledged"] is True

    summary2 = api.get_summary()
    assert summary2["anomaly_count"] == 1
    assert summary2["unacknowledged_count"] == 0

    html = api.render_dashboard_html()
    assert "$720.00" in html
    assert '<div class="val" id="unack-count">0</div>' in html
