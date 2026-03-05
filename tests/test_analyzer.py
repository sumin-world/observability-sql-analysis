"""Tests for the SQL analyzer module."""

import sqlite3
import tempfile
import os

import pytest

from src.collector import SCHEMA_SQL
from src.analyzer import run_analysis, run_custom_query, ANALYSIS_QUERIES


@pytest.fixture
def sample_db():
    """Create a temporary database with sample data."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)

    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_SQL)

    # Insert sample memory data (growing from 30MB to 150MB)
    rows = []
    for i in range(20):
        ts = f"2025-03-15T10:{i:02d}:00+00:00"
        mem = 30_000_000 + i * 6_000_000  # 30MB → 144MB
        heap = 25_000_000 + i * 5_000_000
        rows.append(("container_memory_bytes", "{}", ts, float(mem), "2025-03-15T10:00:00"))
        rows.append(("heap_alloc_bytes", "{}", ts, float(heap), "2025-03-15T10:00:00"))
        rows.append(("leaked_chunks", "{}", ts, float(i * 6), "2025-03-15T10:00:00"))
        rows.append(("p50_latency", "{}", ts, 0.025 + i * 0.002, "2025-03-15T10:00:00"))
        rows.append(("p90_latency", "{}", ts, 0.080 + i * 0.005, "2025-03-15T10:00:00"))
        rows.append(("p99_latency", "{}", ts, 0.150 + i * 0.020, "2025-03-15T10:00:00"))
        rows.append(("http_error_rate_5m", "{}", ts, 0.01 + i * 0.008, "2025-03-15T10:00:00"))
        rows.append(("container_cpu_seconds", "{}", ts, 0.05 + i * 0.01, "2025-03-15T10:00:00"))

    conn.executemany(
        "INSERT INTO metrics (metric_name, labels, timestamp, value, collected_at) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()

    yield path
    os.unlink(path)


class TestRunAnalysis:
    def test_returns_all_queries_by_default(self, sample_db):
        results = run_analysis(sample_db)
        assert len(results) > 0
        names = {r.name for r in results}
        assert "data_summary" in names
        assert "memory_growth_rate" in names

    def test_subset_queries(self, sample_db):
        results = run_analysis(sample_db, query_names=["data_summary"])
        assert len(results) == 1
        assert results[0].name == "data_summary"

    def test_data_summary_has_all_metrics(self, sample_db):
        results = run_analysis(sample_db, query_names=["data_summary"])
        summary = results[0]
        metric_names = {row[0] for row in summary.rows}
        assert "container_memory_bytes" in metric_names
        assert "heap_alloc_bytes" in metric_names
        assert "leaked_chunks" in metric_names

    def test_memory_growth_rate_positive(self, sample_db):
        results = run_analysis(sample_db, query_names=["memory_growth_rate"])
        result = results[0]
        assert not result.is_empty
        row = result.rows[0]
        cols = dict(zip(result.columns, row))
        assert cols["delta_mb"] > 0
        assert cols["growth_mb_per_min"] > 0

    def test_memory_anomaly_detects_spike(self, sample_db):
        """Insert a spike value and verify z-score anomaly detection."""
        conn = sqlite3.connect(sample_db)
        # Add a massive spike at the end
        conn.execute(
            "INSERT INTO metrics (metric_name, labels, timestamp, value, collected_at) "
            "VALUES ('container_memory_bytes', '{}', '2025-03-15T10:30:00+00:00', 500000000, '2025-03-15T10:00:00')"
        )
        conn.commit()
        conn.close()

        results = run_analysis(sample_db, query_names=["memory_anomaly_zscore"])
        result = results[0]
        statuses = [row[-1] for row in result.rows]
        assert "ANOMALY" in statuses

    def test_error_rate_severity_classification(self, sample_db):
        results = run_analysis(sample_db, query_names=["error_rate_trend"])
        result = results[0]
        severities = {row[-1] for row in result.rows}
        # Data goes from 1% to 16%, should have multiple severity levels
        assert len(severities) > 1


class TestCustomQuery:
    def test_simple_count(self, sample_db):
        result = run_custom_query(sample_db, "SELECT COUNT(*) AS cnt FROM metrics")
        assert result.columns == ["cnt"]
        assert result.rows[0][0] > 0

    def test_to_dicts(self, sample_db):
        result = run_custom_query(sample_db, "SELECT metric_name, COUNT(*) AS cnt FROM metrics GROUP BY metric_name LIMIT 3")
        dicts = result.to_dicts()
        assert len(dicts) > 0
        assert "metric_name" in dicts[0]
        assert "cnt" in dicts[0]
