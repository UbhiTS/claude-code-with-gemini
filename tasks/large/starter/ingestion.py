"""Multi-currency cloud billing telemetry normalization and deduplication."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Set


FX_RATES_TO_USD: Dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "JPY": 0.0067,
}


@dataclass(frozen=True)
class BillingRecord:
    event_id: str
    date: str
    project_id: str
    service: str
    sku: str
    gross_cost_usd: float
    credits_usd: float
    net_cost_usd: float


@dataclass(frozen=True)
class DailyServiceSpend:
    date: str
    project_id: str
    service: str
    net_cost_usd: float


def normalize_billing_events(raw_events: List[Dict[str, Any]]) -> List[BillingRecord]:
    records: List[BillingRecord] = []
    # BUG 1: Missing event_id deduplication and divides by FX rate instead of multiplying
    for ev in raw_events:
        event_id = str(ev["event_id"])
        currency = str(ev.get("currency", "USD")).upper()
        if currency not in FX_RATES_TO_USD:
            raise ValueError(f"Unsupported currency: {currency}")
        rate = FX_RATES_TO_USD[currency]
        gross_raw = float(ev.get("cost", 0.0))
        credits_raw = float(ev.get("credits", 0.0))

        gross_usd = round(gross_raw / rate, 4)
        credits_usd = round(abs(credits_raw) / rate, 4)
        net_usd = max(0.0, round(gross_usd - credits_usd, 4))

        records.append(
            BillingRecord(
                event_id=event_id,
                date=str(ev["date"]),
                project_id=str(ev["project_id"]),
                service=str(ev["service"]),
                sku=str(ev.get("sku", "default")),
                gross_cost_usd=gross_usd,
                credits_usd=credits_usd,
                net_cost_usd=net_usd,
            )
        )
    return records


def aggregate_daily_spend(records: List[BillingRecord]) -> List[DailyServiceSpend]:
    totals: Dict[tuple[str, str, str], float] = {}
    for r in records:
        key = (r.date, r.project_id, r.service)
        totals[key] = round(totals.get(key, 0.0) + r.net_cost_usd, 4)

    out = [
        DailyServiceSpend(date=d, project_id=p, service=s, net_cost_usd=val)
        for (d, p, s), val in sorted(totals.items())
    ]
    return out
