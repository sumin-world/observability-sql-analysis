"""Command-line interface for the observability SQL analysis toolkit.

Usage examples:

    # Collect metrics from Prometheus into SQLite
    python -m src.cli collect --prometheus http://localhost:9090

    # Run analysis queries
    python -m src.cli analyze

    # Generate visualisations
    python -m src.cli visualize

    # Generate full report (analyze + visualize + markdown)
    python -m src.cli report

    # Run custom SQL
    python -m src.cli query "SELECT * FROM metrics LIMIT 10"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_DB = "data/metrics.db"
DEFAULT_OUTPUT = "output"


def cmd_collect(args: argparse.Namespace) -> None:
    from .collector import collect_and_store

    result = collect_and_store(
        db_path=args.db,
        prometheus_url=args.prometheus,
        start=args.start or "",
        end=args.end or "",
        step=args.step,
    )
    print(f"Collection complete: {result['queries_run']} queries, {result['rows_inserted']} rows inserted")


def cmd_analyze(args: argparse.Namespace) -> None:
    from .analyzer import run_analysis

    results = run_analysis(args.db, query_names=args.queries)
    for r in results:
        print(f"\n{'='*60}")
        print(f"  {r.name}: {r.description}")
        print(f"{'='*60}")
        if r.is_empty:
            print("  (no data)")
            continue
        # Print header
        print("  " + " | ".join(f"{c:>15}" for c in r.columns))
        print("  " + "-" * (17 * len(r.columns)))
        for row in r.rows[:20]:
            cells = []
            for cell in row:
                if cell is None:
                    cells.append(f"{'':>15}")
                elif isinstance(cell, float):
                    cells.append(f"{cell:>15.4f}")
                else:
                    cells.append(f"{str(cell):>15}")
            print("  " + " | ".join(cells))
        if len(r.rows) > 20:
            print(f"  ... ({len(r.rows) - 20} more rows)")


def cmd_visualize(args: argparse.Namespace) -> None:
    from .visualizer import generate_all

    fig_dir = f"{args.output}/figures"
    paths = generate_all(args.db, fig_dir)
    print(f"Generated {len(paths)} figures:")
    for p in paths:
        print(f"  → {p}")


def cmd_report(args: argparse.Namespace) -> None:
    from .visualizer import generate_all
    from .report import generate_report

    fig_dir = f"{args.output}/figures"
    paths = generate_all(args.db, fig_dir)
    print(f"Generated {len(paths)} figures")

    report_path = f"{args.output}/report.md"
    generate_report(args.db, fig_dir, report_path)
    print(f"Report written to: {report_path}")


def cmd_query(args: argparse.Namespace) -> None:
    from .analyzer import run_custom_query

    result = run_custom_query(args.db, args.sql)
    if args.json:
        print(json.dumps(result.to_dicts(), indent=2, default=str))
    else:
        print(" | ".join(result.columns))
        print("-" * (15 * len(result.columns)))
        for row in result.rows:
            print(" | ".join(str(c) for c in row))


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="observability-sql",
        description="SQL-based observability data analysis toolkit",
    )
    parser.add_argument("--db", default=DEFAULT_DB, help=f"SQLite database path (default: {DEFAULT_DB})")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"Output directory (default: {DEFAULT_OUTPUT})")

    sub = parser.add_subparsers(dest="command", required=True)

    # collect
    p_collect = sub.add_parser("collect", help="Collect metrics from Prometheus into SQLite")
    p_collect.add_argument("--prometheus", default="http://localhost:9090", help="Prometheus base URL")
    p_collect.add_argument("--start", default="", help="Start time (ISO-8601 or UNIX)")
    p_collect.add_argument("--end", default="", help="End time (ISO-8601 or UNIX)")
    p_collect.add_argument("--step", default="15s", help="Query step (default: 15s)")

    # analyze
    p_analyze = sub.add_parser("analyze", help="Run analytical SQL queries")
    p_analyze.add_argument("--queries", nargs="*", help="Specific query names to run")

    # visualize
    sub.add_parser("visualize", help="Generate matplotlib figures")

    # report
    sub.add_parser("report", help="Generate full Markdown report with figures")

    # query
    p_query = sub.add_parser("query", help="Run a custom SQL query")
    p_query.add_argument("sql", help="SQL query string")
    p_query.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    commands = {
        "collect": cmd_collect,
        "analyze": cmd_analyze,
        "visualize": cmd_visualize,
        "report": cmd_report,
        "query": cmd_query,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
