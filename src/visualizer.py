"""Generate matplotlib visualisations from SQL analysis results.

Each public function produces a standalone figure saved to disk.
All functions accept a database path and an output directory.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from datetime import datetime


# ── Style defaults ─────────────────────────────────────────────────────────

FIGSIZE = (14, 6)
COLORS = {
    "primary": "#2563EB",
    "secondary": "#7C3AED",
    "accent": "#059669",
    "danger": "#DC2626",
    "warning": "#D97706",
    "muted": "#6B7280",
    "bg": "#FAFAFA",
}

plt.rcParams.update({
    "figure.facecolor": COLORS["bg"],
    "axes.facecolor": "#FFFFFF",
    "axes.grid": True,
    "axes.spines.top": False,
    "axes.spines.right": False,
    "grid.alpha": 0.3,
    "font.size": 11,
    "font.family": "sans-serif",
})


def _parse_timestamps(rows: list[tuple], ts_col: int = 0) -> list[datetime]:
    """Parse ISO timestamp strings into datetime objects."""
    result = []
    for row in rows:
        ts = row[ts_col]
        if isinstance(ts, str):
            # Handle various ISO formats
            ts = ts.replace("Z", "+00:00")
            try:
                result.append(datetime.fromisoformat(ts))
            except ValueError:
                result.append(datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
        else:
            result.append(ts)
    return result


def _ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


# ── 1. Memory Growth Timeline ─────────────────────────────────────────────

def plot_memory_timeline(db_path: str, output_dir: str) -> str:
    """Container memory usage over time with anomaly highlights."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT timestamp, value FROM metrics "
        "WHERE metric_name = 'container_memory_bytes' ORDER BY timestamp"
    ).fetchall()

    stats = conn.execute(
        "SELECT AVG(value), "
        "SQRT(AVG(value*value) - AVG(value)*AVG(value)) "
        "FROM metrics WHERE metric_name = 'container_memory_bytes'"
    ).fetchone()
    conn.close()

    if not rows:
        return ""

    timestamps = _parse_timestamps(rows)
    values_mb = [r[1] / 1048576.0 for r in rows]
    mean_val, std_val = stats[0] / 1048576.0, (stats[1] or 0) / 1048576.0

    fig, ax = plt.subplots(figsize=FIGSIZE)

    ax.plot(timestamps, values_mb, color=COLORS["primary"], linewidth=2, label="Container Memory")
    ax.fill_between(timestamps, values_mb, alpha=0.1, color=COLORS["primary"])

    # Anomaly threshold (mean + 2*std)
    threshold = mean_val + 2 * std_val
    ax.axhline(y=threshold, color=COLORS["danger"], linestyle="--", linewidth=1.5,
               label=f"Anomaly Threshold ({threshold:.1f} MB)")
    ax.axhline(y=100, color=COLORS["warning"], linestyle=":", linewidth=1.5,
               label="Alert Threshold (100 MB)")

    # Highlight anomalous points
    anomaly_ts = [t for t, v in zip(timestamps, values_mb) if v > threshold]
    anomaly_vals = [v for v in values_mb if v > threshold]
    if anomaly_ts:
        ax.scatter(anomaly_ts, anomaly_vals, color=COLORS["danger"], s=30, zorder=5, label="Anomalies")

    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Memory (MB)")
    ax.set_title("Container Memory Usage Over Time", fontsize=14, fontweight="bold")
    ax.legend(loc="upper left", framealpha=0.9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    _ensure_dir(output_dir)
    path = os.path.join(output_dir, "memory_timeline.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# ── 2. Latency Percentile Bands ───────────────────────────────────────────

def plot_latency_percentiles(db_path: str, output_dir: str) -> str:
    """P50 / P90 / P99 latency with SLO threshold."""
    conn = sqlite3.connect(db_path)

    p50_rows = conn.execute(
        "SELECT timestamp, value * 1000 FROM metrics WHERE metric_name='p50_latency' ORDER BY timestamp"
    ).fetchall()
    p90_rows = conn.execute(
        "SELECT timestamp, value * 1000 FROM metrics WHERE metric_name='p90_latency' ORDER BY timestamp"
    ).fetchall()
    p99_rows = conn.execute(
        "SELECT timestamp, value * 1000 FROM metrics WHERE metric_name='p99_latency' ORDER BY timestamp"
    ).fetchall()
    conn.close()

    if not p50_rows:
        return ""

    fig, ax = plt.subplots(figsize=FIGSIZE)

    ts50 = _parse_timestamps(p50_rows)
    ts90 = _parse_timestamps(p90_rows)
    ts99 = _parse_timestamps(p99_rows)

    ax.plot(ts50, [r[1] for r in p50_rows], color=COLORS["accent"], linewidth=2, label="P50")
    ax.plot(ts90, [r[1] for r in p90_rows], color=COLORS["warning"], linewidth=2, label="P90")
    ax.plot(ts99, [r[1] for r in p99_rows], color=COLORS["danger"], linewidth=2, label="P99")

    # SLO line
    ax.axhline(y=500, color=COLORS["danger"], linestyle="--", linewidth=1.5, alpha=0.7, label="SLO (500ms)")

    # Fill between P50 and P99
    if len(ts50) == len(ts99):
        ax.fill_between(ts50, [r[1] for r in p50_rows], [r[1] for r in p99_rows],
                         alpha=0.1, color=COLORS["secondary"])

    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Latency (ms)")
    ax.set_title("Request Latency Percentiles", fontsize=14, fontweight="bold")
    ax.legend(loc="upper left", framealpha=0.9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    _ensure_dir(output_dir)
    path = os.path.join(output_dir, "latency_percentiles.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# ── 3. Error Rate Heatmap ─────────────────────────────────────────────────

def plot_error_rate(db_path: str, output_dir: str) -> str:
    """Error rate over time with severity bands."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT timestamp, value FROM metrics WHERE metric_name='http_error_rate_5m' ORDER BY timestamp"
    ).fetchall()
    conn.close()

    if not rows:
        return ""

    timestamps = _parse_timestamps(rows)
    rates_pct = [r[1] * 100 for r in rows]

    fig, ax = plt.subplots(figsize=FIGSIZE)

    # Severity bands
    ax.axhspan(0, 1, alpha=0.05, color=COLORS["accent"], label="Normal (<1%)")
    ax.axhspan(1, 5, alpha=0.08, color=COLORS["warning"], label="Elevated (1–5%)")
    ax.axhspan(5, 10, alpha=0.08, color=COLORS["danger"])
    ax.axhline(y=10, color=COLORS["danger"], linestyle="--", linewidth=1.5, label="Critical (>10%)")

    # Colour points by severity
    colors = []
    for r in rates_pct:
        if r > 10:
            colors.append(COLORS["danger"])
        elif r > 5:
            colors.append(COLORS["warning"])
        elif r > 1:
            colors.append(COLORS["warning"])
        else:
            colors.append(COLORS["accent"])

    ax.bar(timestamps, rates_pct, width=0.0003, color=colors, alpha=0.8)
    ax.plot(timestamps, rates_pct, color=COLORS["muted"], linewidth=1, alpha=0.5)

    ax.set_xlabel("Time (UTC)")
    ax.set_ylabel("Error Rate (%)")
    ax.set_title("HTTP Error Rate (5-minute rolling)", fontsize=14, fontweight="bold")
    ax.legend(loc="upper left", framealpha=0.9)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    _ensure_dir(output_dir)
    path = os.path.join(output_dir, "error_rate.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# ── 4. Leak Correlation Scatter ────────────────────────────────────────────

def plot_leak_correlation(db_path: str, output_dir: str) -> str:
    """Scatter plot: leaked chunks vs heap allocation."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""\
        WITH c AS (
            SELECT timestamp, value AS chunks FROM metrics WHERE metric_name='leaked_chunks'
        ),
        h AS (
            SELECT timestamp, value / 1048576.0 AS heap_mb FROM metrics WHERE metric_name='heap_alloc_bytes'
        )
        SELECT c.chunks, h.heap_mb FROM c
        INNER JOIN h ON c.timestamp = h.timestamp
        WHERE c.chunks > 0
    """).fetchall()
    conn.close()

    if not rows:
        return ""

    chunks = [r[0] for r in rows]
    heap_mb = [r[1] for r in rows]

    fig, ax = plt.subplots(figsize=(10, 8))

    ax.scatter(chunks, heap_mb, c=COLORS["primary"], alpha=0.6, s=50, edgecolors="white", linewidth=0.5)

    # Trend line
    if len(chunks) > 1:
        z = np.polyfit(chunks, heap_mb, 1)
        p = np.poly1d(z)
        x_line = np.linspace(min(chunks), max(chunks), 100)
        ax.plot(x_line, p(x_line), color=COLORS["danger"], linestyle="--", linewidth=2,
                label=f"Trend: {z[0]:.2f} MB/chunk")

    ax.set_xlabel("Leaked Chunks")
    ax.set_ylabel("Heap Allocation (MB)")
    ax.set_title("Memory Leak Correlation: Chunks vs Heap Size", fontsize=14, fontweight="bold")
    ax.legend(loc="upper left", framealpha=0.9)

    _ensure_dir(output_dir)
    path = os.path.join(output_dir, "leak_correlation.png")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# ── 5. Resource Pressure Dashboard ────────────────────────────────────────

def plot_resource_dashboard(db_path: str, output_dir: str) -> str:
    """Multi-panel dashboard: CPU, Memory, Latency, Error Rate."""
    conn = sqlite3.connect(db_path)

    mem_rows = conn.execute(
        "SELECT timestamp, value/1048576.0 FROM metrics WHERE metric_name='container_memory_bytes' ORDER BY timestamp"
    ).fetchall()
    cpu_rows = conn.execute(
        "SELECT timestamp, value*100 FROM metrics WHERE metric_name='container_cpu_seconds' ORDER BY timestamp"
    ).fetchall()
    p99_rows = conn.execute(
        "SELECT timestamp, value*1000 FROM metrics WHERE metric_name='p99_latency' ORDER BY timestamp"
    ).fetchall()
    err_rows = conn.execute(
        "SELECT timestamp, value*100 FROM metrics WHERE metric_name='http_error_rate_5m' ORDER BY timestamp"
    ).fetchall()
    conn.close()

    fig, axes = plt.subplots(2, 2, figsize=(16, 10))
    fig.suptitle("Resource Pressure Dashboard", fontsize=16, fontweight="bold", y=0.98)

    panels = [
        (axes[0, 0], mem_rows, "Memory (MB)", COLORS["primary"], 100, "Alert: 100 MB"),
        (axes[0, 1], cpu_rows, "CPU Usage (%)", COLORS["secondary"], 50, "Alert: 50%"),
        (axes[1, 0], p99_rows, "P99 Latency (ms)", COLORS["warning"], 500, "SLO: 500ms"),
        (axes[1, 1], err_rows, "Error Rate (%)", COLORS["danger"], 10, "Alert: 10%"),
    ]

    for ax, rows, ylabel, color, threshold, threshold_label in panels:
        if rows:
            timestamps = _parse_timestamps(rows)
            values = [r[1] for r in rows]
            ax.plot(timestamps, values, color=color, linewidth=1.5)
            ax.fill_between(timestamps, values, alpha=0.1, color=color)
            ax.axhline(y=threshold, color=COLORS["danger"], linestyle="--", linewidth=1, alpha=0.7)
            ax.text(timestamps[-1], threshold, f" {threshold_label}", va="bottom",
                    fontsize=9, color=COLORS["danger"])
            ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        ax.set_ylabel(ylabel, fontsize=10)

    _ensure_dir(output_dir)
    path = os.path.join(output_dir, "resource_dashboard.png")
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


# ── Convenience: generate all ──────────────────────────────────────────────

def generate_all(db_path: str, output_dir: str = "output/figures") -> list[str]:
    """Run all visualisation functions and return list of saved paths."""
    funcs = [
        plot_memory_timeline,
        plot_latency_percentiles,
        plot_error_rate,
        plot_leak_correlation,
        plot_resource_dashboard,
    ]
    paths = []
    for fn in funcs:
        try:
            p = fn(db_path, output_dir)
            if p:
                paths.append(p)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Visualisation '%s' failed: %s", fn.__name__, exc)
    return paths
