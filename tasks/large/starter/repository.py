"""SQLite persistence layer for Cloud FinOps spend records and anomalies."""

from __future__ import annotations

import sqlite3
from typing import Any, Dict, List

from detector import SpendAnomaly
from ingestion import DailyServiceSpend


class FinOpsRepository:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS daily_spend (
                    date TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    service TEXT NOT NULL,
                    net_cost_usd REAL NOT NULL,
                    PRIMARY KEY (date, project_id, service)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS anomalies (
                    anomaly_id TEXT PRIMARY KEY,
                    date TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    service TEXT NOT NULL,
                    observed_usd REAL NOT NULL,
                    baseline_mean_usd REAL NOT NULL,
                    baseline_std_usd REAL NOT NULL,
                    delta_usd REAL NOT NULL,
                    z_score REAL NOT NULL,
                    severity TEXT NOT NULL,
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    acknowledged_by TEXT NOT NULL DEFAULT ''
                )
                """
            )

    def save_daily_spend(self, items: List[DailyServiceSpend]) -> None:
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO daily_spend (date, project_id, service, net_cost_usd)
                VALUES (?, ?, ?, ?)
                """,
                [(x.date, x.project_id, x.service, x.net_cost_usd) for x in items],
            )

    def save_anomalies(self, anomalies: List[SpendAnomaly]) -> None:
        with self.conn:
            self.conn.executemany(
                """
                INSERT OR REPLACE INTO anomalies (
                    anomaly_id, date, project_id, service,
                    observed_usd, baseline_mean_usd, baseline_std_usd,
                    delta_usd, z_score, severity, acknowledged, acknowledged_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        a.anomaly_id,
                        a.date,
                        a.project_id,
                        a.service,
                        a.observed_usd,
                        a.baseline_mean_usd,
                        a.baseline_std_usd,
                        a.delta_usd,
                        a.z_score,
                        a.severity,
                        1 if a.acknowledged else 0,
                        a.acknowledged_by,
                    )
                    for a in anomalies
                ],
            )

    def acknowledge_anomaly(self, anomaly_id: str, owner: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE anomalies
                SET acknowledged = 1, acknowledged_by = ?
                WHERE anomaly_id = ?
                """,
                (owner, anomaly_id),
            )
            # BUG 3: Always returns True even when anomaly_id does not exist
            return True

    def list_anomalies(self) -> List[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM anomalies ORDER BY date ASC, anomaly_id ASC")
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            d["acknowledged"] = bool(d["acknowledged"])
            rows.append(d)
        return rows

    def spend_by_service(self) -> Dict[str, float]:
        cur = self.conn.execute(
            "SELECT service, ROUND(SUM(net_cost_usd), 4) AS total_usd FROM daily_spend GROUP BY service"
        )
        return {str(r["service"]): float(r["total_usd"]) for r in cur.fetchall()}
