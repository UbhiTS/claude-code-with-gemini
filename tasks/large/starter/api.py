"""REST API facade and HTML dashboard data provider for the Cloud FinOps platform."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from detector import detect_anomalies
from ingestion import aggregate_daily_spend, normalize_billing_events
from repository import FinOpsRepository


class FinOpsAPI:
    def __init__(
        self,
        repository: FinOpsRepository | None = None,
        window: int = 7,
        z_threshold: float = 2.5,
        min_spend_delta_usd: float = 50.0,
    ) -> None:
        self.repo = repository or FinOpsRepository(":memory:")
        self.window = window
        self.z_threshold = z_threshold
        self.min_spend_delta_usd = min_spend_delta_usd

    def ingest_and_analyze(self, raw_events: List[Dict[str, Any]]) -> Dict[str, Any]:
        records = normalize_billing_events(raw_events)
        daily = aggregate_daily_spend(records)
        self.repo.save_daily_spend(daily)
        anomalies = detect_anomalies(
            daily,
            window=self.window,
            z_threshold=self.z_threshold,
            min_spend_delta_usd=self.min_spend_delta_usd,
        )
        self.repo.save_anomalies(anomalies)
        return {
            "ingested_records": len(records),
            "daily_points": len(daily),
            "detected_anomalies": len(anomalies),
        }

    def acknowledge(self, anomaly_id: str, owner: str) -> Dict[str, Any]:
        updated = self.repo.acknowledge_anomaly(anomaly_id, owner)
        return {"anomaly_id": anomaly_id, "acknowledged": updated, "owner": owner if updated else ""}

    def get_summary(self) -> Dict[str, Any]:
        by_service = self.repo.spend_by_service()
        total_spend = round(sum(by_service.values()), 4)
        anomalies = self.repo.list_anomalies()
        # BUG 4: Counts all anomalies instead of only unacknowledged anomalies
        unack_count = len(anomalies)
        return {
            "total_net_spend_usd": total_spend,
            "anomaly_count": len(anomalies),
            "unacknowledged_count": unack_count,
            "by_service": by_service,
            "anomalies": anomalies,
        }

    def render_dashboard_html(self) -> str:
        summary = self.get_summary()
        tpl_path = Path(__file__).parent / "dashboard.html"
        html = tpl_path.read_text(encoding="utf-8")
        return (
            html.replace("{{TOTAL_SPEND_USD}}", f"${summary['total_net_spend_usd']:,.2f}")
            .replace("{{ANOMALY_COUNT}}", str(summary["anomaly_count"]))
            .replace("{{UNACK_COUNT}}", str(summary["unacknowledged_count"]))
        )
