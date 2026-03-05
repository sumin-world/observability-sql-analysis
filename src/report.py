"""Generate a Markdown analysis report from SQL query results.

Combines analytical queries, statistics, and chart references into a
single Markdown document suitable for review or inclusion in a pull
request / incident debrief.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from textwrap import dedent

from .analyzer import QueryResult, run_analysis


def _format_table(result: QueryResult, max_rows: int = 20) -> str:
    """Render a QueryResult as a Markdown table."""
    if result.is_empty:
        return "_No data available._\n"

    lines = []
    # Header
    lines.append("| " + " | ".join(result.columns) + " |")
    lines.append("| " + " | ".join("---" for _ in result.columns) + " |")

    # Rows (truncate if large)
    display_rows = result.rows[:max_rows]
    for row in display_rows:
        cells = []
        for cell in row:
            if cell is None:
                cells.append("")
            elif isinstance(cell, float):
                cells.append(f"{cell:,.4f}" if abs(cell) < 1 else f"{cell:,.2f}")
            else:
                cells.append(str(cell))
        lines.append("| " + " | ".join(cells) + " |")

    if len(result.rows) > max_rows:
        lines.append(f"\n_Showing {max_rows} of {len(result.rows)} rows._")

    return "\n".join(lines) + "\n"


def generate_report(
    db_path: str,
    figure_dir: str = "output/figures",
    output_path: str = "output/report.md",
) -> str:
    """Generate a full Markdown analysis report.

    Returns the report content as a string and writes it to *output_path*.
    """
    results = run_analysis(db_path)

    # Database summary
    conn = sqlite3.connect(db_path)
    total_rows = conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    metric_count = conn.execute("SELECT COUNT(DISTINCT metric_name) FROM metrics").fetchone()[0]
    time_range = conn.execute(
        "SELECT MIN(timestamp), MAX(timestamp) FROM metrics"
    ).fetchone()
    conn.close()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    sections = []

    # Title
    sections.append(dedent(f"""\
    # Observability SQL Analysis Report

    **Generated:** {now}
    **Database:** `{db_path}`
    **Total rows:** {total_rows:,} | **Metrics:** {metric_count} | **Time range:** {time_range[0] or 'N/A'} → {time_range[1] or 'N/A'}

    ---
    """))

    # Group results by category
    categories = {
        "Memory Analysis": ["memory_growth_rate", "memory_5min_windows", "memory_anomaly_zscore", "leak_correlation"],
        "Latency Analysis": ["latency_percentile_trend", "latency_slo_breach_windows"],
        "Error Rate & Traffic": ["error_rate_trend", "traffic_by_endpoint", "traffic_by_status_code"],
        "Resource Utilisation": ["cpu_memory_correlation"],
        "Data Summary": ["data_summary"],
    }

    result_map = {r.name: r for r in results}

    for category, query_names in categories.items():
        sections.append(f"## {category}\n")

        for name in query_names:
            if name not in result_map:
                continue
            result = result_map[name]
            sections.append(f"### {result.description}\n")
            sections.append(_format_table(result))
            sections.append("")

        # Add figure references
        figure_map = {
            "Memory Analysis": "memory_timeline.png",
            "Latency Analysis": "latency_percentiles.png",
            "Error Rate & Traffic": "error_rate.png",
        }
        if category in figure_map:
            fig_path = f"{figure_dir}/{figure_map[category]}"
            sections.append(f"![{category}]({fig_path})\n")

    # Leak correlation figure
    sections.append("## Correlation Analysis\n")
    sections.append(f"![Leak Correlation]({figure_dir}/leak_correlation.png)\n")
    sections.append(f"![Resource Dashboard]({figure_dir}/resource_dashboard.png)\n")

    report = "\n".join(sections)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(report)

    return report
