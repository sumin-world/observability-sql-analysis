"""Generate realistic sample observability data in SQLite.

This script creates a synthetic dataset that simulates a memory-leak
incident so the analysis pipeline can be demonstrated without a live
Prometheus instance.

Scenario (60-minute window):
  0–20 min : stable baseline
  20–45 min: memory leak ramp (linear growth)
  45–55 min: high error rate + latency spike
  55–60 min: recovery after /reset
"""

from __future__ import annotations

import json
import math
import os
import random
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.collector import SCHEMA_SQL

SEED = 42
random.seed(SEED)

DB_PATH = "data/metrics.db"
START = datetime(2025, 3, 15, 10, 0, 0, tzinfo=timezone.utc)
DURATION_MIN = 60
STEP_SEC = 15
NUM_POINTS = DURATION_MIN * 60 // STEP_SEC  # 240 points


def ts(i: int) -> str:
    return (START + timedelta(seconds=i * STEP_SEC)).isoformat()


def noise(base: float, pct: float = 0.03) -> float:
    return base * (1 + random.uniform(-pct, pct))


def generate() -> None:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA_SQL)

    now_iso = datetime.now(timezone.utc).isoformat()
    rows: list[tuple] = []

    for i in range(NUM_POINTS):
        t = ts(i)
        minute = i * STEP_SEC / 60.0

        # ── Memory ─────────────────────────────────────────────────────
        if minute < 20:
            mem_bytes = noise(30_000_000)     # ~30 MB baseline
            heap_bytes = noise(25_000_000)
            chunks = 0
        elif minute < 45:
            progress = (minute - 20) / 25.0   # 0 → 1 over 25 min
            mem_bytes = noise(30_000_000 + progress * 170_000_000)  # ramp to ~200 MB
            heap_bytes = noise(25_000_000 + progress * 150_000_000)
            chunks = int(progress * 170)
        elif minute < 55:
            mem_bytes = noise(200_000_000)     # plateau at ~200 MB
            heap_bytes = noise(175_000_000)
            chunks = 170
        else:
            # Recovery: /reset called
            recovery = (minute - 55) / 5.0
            mem_bytes = noise(200_000_000 - recovery * 170_000_000)
            heap_bytes = noise(175_000_000 - recovery * 150_000_000)
            chunks = max(0, int(170 - recovery * 170))

        rows.append(("container_memory_bytes", "{}", t, mem_bytes, now_iso))
        rows.append(("heap_alloc_bytes", "{}", t, heap_bytes, now_iso))
        rows.append(("leaked_chunks", "{}", t, float(chunks), now_iso))

        # ── CPU ────────────────────────────────────────────────────────
        base_cpu = 0.05
        if 20 <= minute < 45:
            base_cpu = 0.05 + 0.15 * ((minute - 20) / 25.0)
        elif 45 <= minute < 55:
            base_cpu = 0.25
        rows.append(("container_cpu_seconds", "{}", t, noise(base_cpu, 0.1), now_iso))

        # ── Latency ───────────────────────────────────────────────────
        if minute < 45:
            p50 = noise(0.025, 0.15)
            p90 = noise(0.080, 0.15)
            p99 = noise(0.150, 0.15)
        elif minute < 55:
            # Latency spike during incident
            p50 = noise(0.100, 0.2)
            p90 = noise(0.350, 0.2)
            p99 = noise(0.650, 0.2)  # breaches 500ms SLO
        else:
            p50 = noise(0.030, 0.15)
            p90 = noise(0.090, 0.15)
            p99 = noise(0.160, 0.15)

        rows.append(("p50_latency", "{}", t, p50, now_iso))
        rows.append(("p90_latency", "{}", t, p90, now_iso))
        rows.append(("p99_latency", "{}", t, p99, now_iso))

        # ── Error rate ────────────────────────────────────────────────
        if minute < 45:
            err_rate = noise(0.008, 0.3)      # ~0.8% normal
        elif minute < 55:
            err_rate = noise(0.15, 0.2)        # ~15% during incident
        else:
            err_rate = noise(0.01, 0.3)        # recovery
        rows.append(("http_error_rate_5m", "{}", t, err_rate, now_iso))

        # ── Request rate by path ──────────────────────────────────────
        for path, base_rate in [("/", 8.0), ("/leak", 2.0), ("/health", 5.0), ("/slow", 1.5), ("/error", 1.0)]:
            if 45 <= minute < 55 and path == "/error":
                rate = noise(base_rate * 3, 0.15)
            else:
                rate = noise(base_rate, 0.1)
            labels = json.dumps({"path": path})
            rows.append(("request_rate_by_path", labels, t, rate, now_iso))

        # ── Request rate by status ────────────────────────────────────
        if minute < 45:
            r200 = noise(16.0, 0.1)
            r500 = noise(0.15, 0.3)
        elif minute < 55:
            r200 = noise(12.0, 0.15)
            r500 = noise(3.0, 0.2)
        else:
            r200 = noise(16.0, 0.1)
            r500 = noise(0.2, 0.3)

        rows.append(("request_rate_by_status", json.dumps({"status": "200"}), t, r200, now_iso))
        rows.append(("request_rate_by_status", json.dumps({"status": "500"}), t, r500, now_iso))

    # Bulk insert
    conn.executemany(
        "INSERT INTO metrics (metric_name, labels, timestamp, value, collected_at) VALUES (?, ?, ?, ?, ?)",
        rows,
    )

    # Record the synthetic run
    conn.execute(
        "INSERT INTO collection_runs (started_at, finished_at, queries_run, rows_inserted, status) VALUES (?, ?, ?, ?, 'completed')",
        (now_iso, datetime.now(timezone.utc).isoformat(), 11, len(rows)),
    )

    conn.commit()
    print(f"Generated {len(rows):,} rows across {NUM_POINTS} time steps")
    print(f"Database: {DB_PATH}")
    conn.close()


if __name__ == "__main__":
    generate()
