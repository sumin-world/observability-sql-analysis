"""Collect metrics from Prometheus HTTP API and store in SQLite.

This module fetches time-series data via PromQL range queries and
persists the results into a normalised relational schema so that
the same data can be explored with standard SQL.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ── Default PromQL queries to collect ──────────────────────────────────────

DEFAULT_QUERIES: dict[str, str] = {
    "container_memory_bytes": 'container_memory_usage_bytes{name="go-api"}',
    "heap_alloc_bytes": "heap_alloc_bytes",
    "leaked_chunks": "leaked_chunks_total",
    "http_requests_total": "http_requests_total",
    "http_error_rate_5m": 'sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))',
    "request_rate_by_path": "sum by (path) (rate(http_requests_total[5m]))",
    "request_rate_by_status": "sum by (status) (rate(http_requests_total[5m]))",
    "p50_latency": "histogram_quantile(0.50, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))",
    "p90_latency": "histogram_quantile(0.90, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))",
    "p99_latency": "histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))",
    "container_cpu_seconds": 'rate(container_cpu_usage_seconds_total{name="go-api"}[5m])',
}


@dataclass
class PrometheusConfig:
    """Connection settings for Prometheus."""

    base_url: str = "http://localhost:9090"
    timeout: int = 30


def _ts_to_iso(ts: float) -> str:
    """Convert a UNIX timestamp to an ISO-8601 UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ── SQLite schema ──────────────────────────────────────────────────────────

SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT    NOT NULL,
    labels      TEXT,            -- JSON string of Prometheus labels
    timestamp   TEXT    NOT NULL, -- ISO-8601 UTC
    value       REAL    NOT NULL,
    collected_at TEXT   NOT NULL  -- when the collector ran
);

CREATE INDEX IF NOT EXISTS idx_metrics_name      ON metrics (metric_name);
CREATE INDEX IF NOT EXISTS idx_metrics_timestamp  ON metrics (timestamp);
CREATE INDEX IF NOT EXISTS idx_metrics_name_ts    ON metrics (metric_name, timestamp);

CREATE TABLE IF NOT EXISTS collection_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    queries_run   INTEGER DEFAULT 0,
    rows_inserted INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'running'
);
"""


def init_db(db_path: str) -> sqlite3.Connection:
    """Create the database and tables if they do not exist."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_SQL)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ── Prometheus client ──────────────────────────────────────────────────────

def query_range(
    cfg: PrometheusConfig,
    query: str,
    start: str,
    end: str,
    step: str = "15s",
) -> list[dict[str, Any]]:
    """Execute a Prometheus range query and return the result list.

    Parameters
    ----------
    cfg : PrometheusConfig
    query : PromQL expression
    start / end : RFC-3339 or UNIX timestamp
    step : resolution step (e.g. "15s", "1m")
    """
    url = f"{cfg.base_url}/api/v1/query_range"
    params = {"query": query, "start": start, "end": end, "step": step}

    resp = requests.get(url, params=params, timeout=cfg.timeout)
    resp.raise_for_status()

    body = resp.json()
    if body.get("status") != "success":
        raise RuntimeError(f"Prometheus query failed: {body}")

    return body["data"]["result"]


# ── Collection logic ───────────────────────────────────────────────────────

def collect_and_store(
    db_path: str,
    prometheus_url: str = "http://localhost:9090",
    start: str = "",
    end: str = "",
    step: str = "15s",
    queries: dict[str, str] | None = None,
) -> dict[str, int]:
    """Run all configured queries and store results in SQLite.

    Returns a summary dict with keys ``queries_run`` and ``rows_inserted``.
    """
    import json

    cfg = PrometheusConfig(base_url=prometheus_url)
    queries = queries or DEFAULT_QUERIES

    now_iso = datetime.now(timezone.utc).isoformat()
    if not end:
        end = now_iso
    if not start:
        # Default: last 1 hour
        from datetime import timedelta

        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        start = (end_dt - timedelta(hours=1)).isoformat()

    conn = init_db(db_path)
    cur = conn.cursor()

    # Record collection run
    cur.execute(
        "INSERT INTO collection_runs (started_at, status) VALUES (?, 'running')",
        (now_iso,),
    )
    run_id = cur.lastrowid

    total_rows = 0
    queries_run = 0

    for metric_name, promql in queries.items():
        try:
            results = query_range(cfg, promql, start, end, step)
            queries_run += 1
        except Exception as exc:
            logger.warning("Query '%s' failed: %s", metric_name, exc)
            continue

        rows = []
        for series in results:
            labels_json = json.dumps(series.get("metric", {}), sort_keys=True)
            for ts, val in series.get("values", []):
                try:
                    value = float(val)
                except (ValueError, TypeError):
                    continue
                rows.append((metric_name, labels_json, _ts_to_iso(ts), value, now_iso))

        if rows:
            cur.executemany(
                "INSERT INTO metrics (metric_name, labels, timestamp, value, collected_at) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
            total_rows += len(rows)
            logger.info("Collected %d rows for '%s'", len(rows), metric_name)

    # Finalise run record
    finished = datetime.now(timezone.utc).isoformat()
    cur.execute(
        "UPDATE collection_runs SET finished_at=?, queries_run=?, rows_inserted=?, status='completed' WHERE id=?",
        (finished, queries_run, total_rows, run_id),
    )

    conn.commit()
    conn.close()

    return {"queries_run": queries_run, "rows_inserted": total_rows}
