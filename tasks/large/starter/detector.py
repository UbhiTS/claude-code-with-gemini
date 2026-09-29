"""Rolling Z-score spend anomaly detector across cloud projects and services."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

from ingestion import DailyServiceSpend


@dataclass(frozen=True)
class SpendAnomaly:
    anomaly_id: str
    date: str
    project_id: str
    service: str
    observed_usd: float
    baseline_mean_usd: float
    baseline_std_usd: float
    delta_usd: float
    z_score: float
    severity: str
    acknowledged: bool = False
    acknowledged_by: str = ""


def detect_anomalies(
    daily_series: List[DailyServiceSpend],
    window: int = 7,
    z_threshold: float = 2.5,
    min_spend_delta_usd: float = 50.0,
) -> List[SpendAnomaly]:
    if window < 2:
        raise ValueError("window must be >= 2")

    grouped: Dict[Tuple[str, str], List[DailyServiceSpend]] = defaultdict(list)
    for item in daily_series:
        grouped[(item.project_id, item.service)].append(item)

    anomalies: List[SpendAnomaly] = []
    for (project_id, service), series in sorted(grouped.items()):
        ordered = sorted(series, key=lambda x: x.date)
        for i in range(window, len(ordered)):
            current = ordered[i]
            # BUG 2: Off-by-one slice includes current point ordered[i] in baseline window
            baseline_vals = [x.net_cost_usd for x in ordered[i - window + 1 : i + 1]]
            mean_val = statistics.fmean(baseline_vals)
            std_val = statistics.pstdev(baseline_vals)
            delta = round(current.net_cost_usd - mean_val, 4)

            if std_val == 0.0:
                z_score = 99.0 if delta >= min_spend_delta_usd else 0.0
            else:
                z_score = round(delta / std_val, 4)

            if z_score >= z_threshold and delta >= min_spend_delta_usd:
                severity = "CRITICAL" if (z_score >= 4.0 or delta >= 500.0) else "HIGH"
                anomaly_id = f"anom:{project_id}:{service}:{current.date}"
                anomalies.append(
                    SpendAnomaly(
                        anomaly_id=anomaly_id,
                        date=current.date,
                        project_id=project_id,
                        service=service,
                        observed_usd=round(current.net_cost_usd, 4),
                        baseline_mean_usd=round(mean_val, 4),
                        baseline_std_usd=round(std_val, 4),
                        delta_usd=delta,
                        z_score=z_score,
                        severity=severity,
                    )
                )
    return anomalies
