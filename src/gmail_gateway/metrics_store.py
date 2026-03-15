from __future__ import annotations

import sqlite3
from pathlib import Path


class PersistentMetricsStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path)
        con.row_factory = sqlite3.Row
        return con

    def _ensure_table(self) -> None:
        with self._connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS gateway_metrics_counters (
                    metric_key TEXT PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            con.commit()

    def inc(self, metric_key: str, *, delta: int = 1) -> None:
        if delta <= 0:
            return
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO gateway_metrics_counters(metric_key, count, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(metric_key) DO UPDATE SET
                    count = count + excluded.count,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (metric_key, delta),
            )
            con.commit()

    def snapshot(self) -> dict[str, int]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT metric_key, count FROM gateway_metrics_counters ORDER BY metric_key ASC"
            ).fetchall()
        return {str(row["metric_key"]): int(row["count"]) for row in rows}
