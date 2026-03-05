"""SQL-based observability data analysis.

Pre-built analytical queries that demonstrate common observability
patterns expressed in SQL: trend detection, anomaly flagging,
percentile estimation, and capacity forecasting.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class QueryResult:
    """Holds a named query result with column headers and rows."""

    name: str
    description: str
    columns: list[str]
    rows: list[tuple[Any, ...]]

    @property
    def is_empty(self) -> bool:
        return len(self.rows) == 0

    def to_dicts(self) -> list[dict[str, Any]]:
        """Convert rows to a list of dictionaries."""
        return [dict(zip(self.columns, row)) for row in self.rows]


# ── Analytical SQL queries ─────────────────────────────────────────────────

ANALYSIS_QUERIES: dict[str, tuple[str, str]] = {
    # ── Memory Analysis ────────────────────────────────────────────────────
    "memory_growth_rate": (
        "Memory growth rate per minute (linear trend)",
        """\
        WITH ordered AS (
            SELECT
                timestamp,
                value,
                ROW_NUMBER() OVER (ORDER BY timestamp) AS rn,
                COUNT(*) OVER () AS total
            FROM metrics
            WHERE metric_name = 'container_memory_bytes'
        ),
        endpoints AS (
            SELECT
                MIN(CASE WHEN rn = 1 THEN value END)     AS first_val,
                MAX(CASE WHEN rn = total THEN value END)  AS last_val,
                MIN(CASE WHEN rn = 1 THEN timestamp END)  AS first_ts,
                MAX(CASE WHEN rn = total THEN timestamp END) AS last_ts,
                total
            FROM ordered
        )
        SELECT
            ROUND(first_val / 1048576.0, 2)  AS start_mb,
            ROUND(last_val  / 1048576.0, 2)  AS end_mb,
            ROUND((last_val - first_val) / 1048576.0, 2) AS delta_mb,
            first_ts  AS start_time,
            last_ts   AS end_time,
            total     AS data_points,
            ROUND(
                (last_val - first_val)
                / NULLIF(
                    (JULIANDAY(last_ts) - JULIANDAY(first_ts)) * 24 * 60,
                    0
                )
                / 1048576.0,
                4
            ) AS growth_mb_per_min
        FROM endpoints
        """,
    ),
    "memory_5min_windows": (
        "Memory usage aggregated into 5-minute windows",
        """\
        SELECT
            STRFTIME('%Y-%m-%d %H:%M', timestamp, 'start of minute',
                     '-' || (CAST(STRFTIME('%M', timestamp) AS INTEGER) % 5) || ' minutes'
            ) AS window_start,
            COUNT(*)                              AS samples,
            ROUND(MIN(value) / 1048576.0, 2)      AS min_mb,
            ROUND(AVG(value) / 1048576.0, 2)      AS avg_mb,
            ROUND(MAX(value) / 1048576.0, 2)      AS max_mb,
            ROUND((MAX(value) - MIN(value)) / 1048576.0, 2) AS range_mb
        FROM metrics
        WHERE metric_name = 'container_memory_bytes'
        GROUP BY window_start
        ORDER BY window_start
        """,
    ),
    "memory_anomaly_zscore": (
        "Flag memory samples whose z-score exceeds 2.0",
        """\
        WITH stats AS (
            SELECT
                AVG(value) AS mean_val,
                -- Population standard deviation via SQLite primitives
                SQRT(AVG(value * value) - AVG(value) * AVG(value)) AS std_val
            FROM metrics
            WHERE metric_name = 'container_memory_bytes'
        )
        SELECT
            m.timestamp,
            ROUND(m.value / 1048576.0, 2) AS value_mb,
            ROUND((m.value - s.mean_val) / NULLIF(s.std_val, 0), 3) AS z_score,
            CASE
                WHEN ABS((m.value - s.mean_val) / NULLIF(s.std_val, 0)) > 2.0
                THEN 'ANOMALY'
                ELSE 'normal'
            END AS status
        FROM metrics m, stats s
        WHERE m.metric_name = 'container_memory_bytes'
        ORDER BY m.timestamp
        """,
    ),
    "leak_correlation": (
        "Correlate leaked chunks with heap allocation growth",
        """\
        WITH chunks AS (
            SELECT timestamp, value AS leaked_chunks
            FROM metrics WHERE metric_name = 'leaked_chunks'
        ),
        heap AS (
            SELECT timestamp, ROUND(value / 1048576.0, 2) AS heap_mb
            FROM metrics WHERE metric_name = 'heap_alloc_bytes'
        )
        SELECT
            c.timestamp,
            c.leaked_chunks,
            h.heap_mb,
            ROUND(h.heap_mb / NULLIF(c.leaked_chunks, 0), 2) AS mb_per_chunk
        FROM chunks c
        INNER JOIN heap h ON c.timestamp = h.timestamp
        ORDER BY c.timestamp
        """,
    ),
    # ── Latency Analysis ───────────────────────────────────────────────────
    "latency_percentile_trend": (
        "P50 / P90 / P99 latency trend over time",
        """\
        WITH p50 AS (
            SELECT timestamp, ROUND(value * 1000, 2) AS p50_ms
            FROM metrics WHERE metric_name = 'p50_latency'
        ),
        p90 AS (
            SELECT timestamp, ROUND(value * 1000, 2) AS p90_ms
            FROM metrics WHERE metric_name = 'p90_latency'
        ),
        p99 AS (
            SELECT timestamp, ROUND(value * 1000, 2) AS p99_ms
            FROM metrics WHERE metric_name = 'p99_latency'
        )
        SELECT
            p50.timestamp,
            p50.p50_ms,
            p90.p90_ms,
            p99.p99_ms,
            CASE WHEN p99.p99_ms > 500 THEN 'SLO_BREACH' ELSE 'ok' END AS slo_status
        FROM p50
        LEFT JOIN p90 ON p50.timestamp = p90.timestamp
        LEFT JOIN p99 ON p50.timestamp = p99.timestamp
        ORDER BY p50.timestamp
        """,
    ),
    "latency_slo_breach_windows": (
        "Time windows where P99 latency exceeded 500ms SLO",
        """\
        SELECT
            timestamp,
            ROUND(value * 1000, 2) AS p99_ms,
            ROUND(value * 1000 - 500, 2) AS breach_delta_ms
        FROM metrics
        WHERE metric_name = 'p99_latency' AND value > 0.5
        ORDER BY timestamp
        """,
    ),
    # ── Error Rate Analysis ────────────────────────────────────────────────
    "error_rate_trend": (
        "5-minute error rate trend with severity classification",
        """\
        SELECT
            timestamp,
            ROUND(value * 100, 3) AS error_rate_pct,
            CASE
                WHEN value > 0.10 THEN 'CRITICAL'
                WHEN value > 0.05 THEN 'WARNING'
                WHEN value > 0.01 THEN 'ELEVATED'
                ELSE 'NORMAL'
            END AS severity
        FROM metrics
        WHERE metric_name = 'http_error_rate_5m'
        ORDER BY timestamp
        """,
    ),
    "traffic_by_endpoint": (
        "Request rate breakdown by endpoint",
        """\
        SELECT
            JSON_EXTRACT(labels, '$.path') AS endpoint,
            COUNT(*) AS samples,
            ROUND(MIN(value), 4) AS min_rps,
            ROUND(AVG(value), 4) AS avg_rps,
            ROUND(MAX(value), 4) AS max_rps
        FROM metrics
        WHERE metric_name = 'request_rate_by_path'
        GROUP BY endpoint
        ORDER BY avg_rps DESC
        """,
    ),
    "traffic_by_status_code": (
        "Request rate breakdown by HTTP status code",
        """\
        SELECT
            JSON_EXTRACT(labels, '$.status') AS status_code,
            COUNT(*) AS samples,
            ROUND(AVG(value), 4) AS avg_rps,
            ROUND(SUM(value), 2) AS total_rate_sum
        FROM metrics
        WHERE metric_name = 'request_rate_by_status'
        GROUP BY status_code
        ORDER BY status_code
        """,
    ),
    # ── Resource Utilisation ───────────────────────────────────────────────
    "cpu_memory_correlation": (
        "CPU vs memory usage to detect resource pressure patterns",
        """\
        WITH cpu AS (
            SELECT timestamp, ROUND(value * 100, 3) AS cpu_pct
            FROM metrics WHERE metric_name = 'container_cpu_seconds'
        ),
        mem AS (
            SELECT timestamp, ROUND(value / 1048576.0, 2) AS mem_mb
            FROM metrics WHERE metric_name = 'container_memory_bytes'
        )
        SELECT
            cpu.timestamp,
            cpu.cpu_pct,
            mem.mem_mb,
            CASE
                WHEN cpu.cpu_pct > 50 AND mem.mem_mb > 200 THEN 'HIGH_PRESSURE'
                WHEN cpu.cpu_pct > 30 OR mem.mem_mb > 150  THEN 'MODERATE'
                ELSE 'LOW'
            END AS pressure_level
        FROM cpu
        INNER JOIN mem ON cpu.timestamp = mem.timestamp
        ORDER BY cpu.timestamp
        """,
    ),
    # ── Collection Summary ─────────────────────────────────────────────────
    "data_summary": (
        "Overview of collected metric data",
        """\
        SELECT
            metric_name,
            COUNT(*)                    AS total_rows,
            MIN(timestamp)              AS earliest,
            MAX(timestamp)              AS latest,
            ROUND(MIN(value), 4)        AS min_value,
            ROUND(AVG(value), 4)        AS avg_value,
            ROUND(MAX(value), 4)        AS max_value
        FROM metrics
        GROUP BY metric_name
        ORDER BY metric_name
        """,
    ),
}


def run_analysis(
    db_path: str,
    query_names: list[str] | None = None,
) -> list[QueryResult]:
    """Execute analytical queries and return structured results.

    Parameters
    ----------
    db_path : path to the SQLite database
    query_names : subset of ANALYSIS_QUERIES keys to run (default: all)
    """
    conn = sqlite3.connect(db_path)

    names = query_names or list(ANALYSIS_QUERIES.keys())
    results: list[QueryResult] = []

    for name in names:
        if name not in ANALYSIS_QUERIES:
            continue
        description, sql = ANALYSIS_QUERIES[name]
        try:
            cur = conn.execute(sql)
            columns = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            results.append(QueryResult(name=name, description=description, columns=columns, rows=rows))
        except Exception as exc:
            logger.warning("Query '%s' failed: %s", name, exc)

    conn.close()
    return results


# ── Custom query execution ─────────────────────────────────────────────────

def run_custom_query(db_path: str, sql: str) -> QueryResult:
    """Execute an arbitrary SQL query against the metrics database."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(sql)
    columns = [desc[0] for desc in cur.description]
    rows = cur.fetchall()
    conn.close()
    return QueryResult(name="custom", description="User-defined query", columns=columns, rows=rows)


import logging

logger = logging.getLogger(__name__)
