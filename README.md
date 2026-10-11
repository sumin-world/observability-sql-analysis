# Observability SQL Analysis

SQL-based analysis toolkit for container observability metrics. Collects
time-series data from Prometheus, stores it in SQLite, and runs analytical
queries to detect anomalies, measure SLO compliance, and produce
visualisations.

## Why SQL for Observability?

PromQL is excellent for real-time dashboards, but SQL unlocks a different
set of capabilities: ad-hoc joins across metric types, window functions for
trend analysis, z-score anomaly detection with pure SQL, and portable
reporting without a running Prometheus instance.

This project bridges the gap — collect with PromQL, analyse with SQL.

## Architecture

```
┌────────────┐      ┌──────────────┐      ┌──────────────┐
│ Prometheus │─────▶│  Collector   │─────▶│   SQLite DB  │
│ (PromQL)   │      │  (Python)    │      │  metrics.db  │
└────────────┘      └──────────────┘      └──────┬───────┘
                                                  │
                    ┌──────────────┐               │
                    │  Analyzer    │◀──────────────┘
                    │  (11 SQL     │
                    │   queries)   │──────▶ Terminal / JSON
                    └──────────────┘
                    ┌──────────────┐
                    │  Visualizer  │──────▶ PNG charts
                    │  (matplotlib)│
                    └──────────────┘
                    ┌──────────────┐
                    │  Reporter    │──────▶ Markdown report
                    └──────────────┘
```

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Option A: Generate sample data (no Prometheus needed)
python scripts/generate_sample_data.py

# Option B: Collect from a live Prometheus instance
python -m src collect --prometheus http://localhost:9090

# Run all SQL analyses
python -m src analyze

# Generate charts
python -m src visualize

# Full report (SQL results + charts + Markdown)
python -m src report
```

## SQL Analysis Queries

The analyzer includes 11 pre-built SQL queries organised by category:

### Memory Analysis
| Query | Description |
|---|---|
| `memory_growth_rate` | Linear trend: start MB → end MB, growth rate per minute |
| `memory_5min_windows` | Aggregated min/avg/max per 5-minute window |
| `memory_anomaly_zscore` | Flag samples with z-score > 2.0 as anomalies |
| `leak_correlation` | Join leaked chunks with heap allocation to compute MB/chunk |

### Latency Analysis
| Query | Description |
|---|---|
| `latency_percentile_trend` | P50/P90/P99 trend with SLO breach detection |
| `latency_slo_breach_windows` | Time windows where P99 > 500ms |

### Error Rate & Traffic
| Query | Description |
|---|---|
| `error_rate_trend` | Severity classification (Normal/Elevated/Warning/Critical) |
| `traffic_by_endpoint` | Request rate breakdown by path |
| `traffic_by_status_code` | Request rate breakdown by HTTP status |

### Resource Utilisation
| Query | Description |
|---|---|
| `cpu_memory_correlation` | CPU vs memory with pressure-level classification |

### Overview
| Query | Description |
|---|---|
| `data_summary` | Row count, time range, min/avg/max per metric |

## Visualisations

The visualiser generates 5 charts:

1. **Memory Timeline** — Usage over time with anomaly highlights and alert threshold
2. **Latency Percentiles** — P50/P90/P99 bands with SLO line
3. **Error Rate** — Severity-coloured bar chart with critical threshold
4. **Leak Correlation** — Scatter plot: chunks vs heap size with trend line
5. **Resource Dashboard** — 4-panel overview (CPU, memory, latency, error rate)

## Custom Queries

```bash
# Run any SQL against the metrics database
python -m src query "SELECT metric_name, COUNT(*) FROM metrics GROUP BY metric_name"

# JSON output
python -m src query --json "SELECT * FROM metrics WHERE metric_name='p99_latency' LIMIT 5"
```

## Database Schema

```sql
CREATE TABLE metrics (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name  TEXT NOT NULL,
    labels       TEXT,              -- JSON string of Prometheus labels
    timestamp    TEXT NOT NULL,     -- ISO-8601 UTC
    value        REAL NOT NULL,
    collected_at TEXT NOT NULL
);

CREATE TABLE collection_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    queries_run   INTEGER DEFAULT 0,
    rows_inserted INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'running'
);
```

Indexed on `metric_name`, `timestamp`, and the composite `(metric_name, timestamp)`.

## Sample Data Scenario

The `generate_sample_data.py` script creates a 60-minute synthetic incident:

| Phase | Minutes | What Happens |
|---|---|---|
| Baseline | 0–20 | Stable at ~30 MB, low latency, low errors |
| Leak ramp | 20–45 | Memory grows linearly to ~200 MB |
| Incident | 45–55 | Latency spikes, error rate jumps to ~15% |
| Recovery | 55–60 | `/reset` called, memory drops, metrics normalise |

## Running Tests

```bash
pytest tests/ -v
```

## Project Structure

```
observability-sql-analysis/
├── src/
│   ├── __init__.py
│   ├── __main__.py          # python -m src entry point
│   ├── cli.py               # CLI argument parsing
│   ├── collector.py         # Prometheus → SQLite ingestion
│   ├── analyzer.py          # 11 pre-built SQL analysis queries
│   ├── visualizer.py        # matplotlib chart generation
│   └── report.py            # Markdown report generator
├── tests/
│   ├── test_analyzer.py     # SQL query correctness tests
│   └── test_collector.py    # Schema and helper tests
├── scripts/
│   └── generate_sample_data.py  # Synthetic incident data generator
├── data/                    # SQLite databases (gitignored)
├── output/                  # Reports and figures (gitignored)
├── requirements.txt
├── .gitignore
└── README.md
```

## Related Projects

- [container-observability-stack](https://github.com/sumin-world/container-observability-stack) — The Go API + Docker Compose stack that generates the metrics
- [observability-agent](https://github.com/sumin-world/observability-agent) — LangGraph agent for real-time anomaly detection

## Author

**Sumin Kim** — Applied Statistics, Yonsei University
- [GitHub](https://github.com/sumin-world)
