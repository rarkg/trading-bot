"""
Query results from data/results.db.

Usage:
    python scripts/query_results.py                # all results, sorted by monthly return desc
    python scripts/query_results.py --asset SOL    # filter by asset
    python scripts/query_results.py --version V12  # filter by version
    python scripts/query_results.py --best         # best result per asset
"""

import sys
import os
import sqlite3
import argparse

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "results.db")

COLS = [
    ("ID",       "id",                 4,  "d"),
    ("Version",  "strategy_version",   8,  "s"),
    ("Asset",    "asset",              5,  "s"),
    ("Lev",      "leverage",           5,  ".1f"),
    ("%/mo",     "monthly_return_pct", 7,  "+.2f"),
    ("DD%",      "max_drawdown_pct",   7,  ".1f"),
    ("PnL%",     "total_pnl_pct",      8,  "+.1f"),
    ("Trades",   "total_trades",       7,  "d"),
    ("Win%",     "win_rate",           6,  ".1f"),
    ("PF",       "profit_factor",      6,  ".2f"),
    ("Sharpe",   "sharpe_ratio",       7,  ".2f"),
    ("AvgW%",    "avg_win_pct",        7,  "+.2f"),
    ("AvgL%",    "avg_loss_pct",       7,  "+.2f"),
    ("Period",   "period_start",       12, "s"),
]


def fmt_val(val, fmt):
    if val is None:
        return "-"
    try:
        return format(val, fmt)
    except (TypeError, ValueError):
        return str(val)


def print_table(rows, col_defs):
    headers = [h for h, _, _, _ in col_defs]
    widths = [max(w, len(h)) for h, _, w, _ in col_defs]

    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    header_row = "|" + "|".join(f" {h:{w}s} " for h, (_, _, w, _) in zip(headers, col_defs)) + "|"

    print(sep)
    print(header_row)
    print(sep)

    for row in rows:
        cells = []
        for (header, col, width, fmt), val in zip(col_defs, row):
            s = fmt_val(val, fmt)
            cells.append(f" {s:>{width}s} ")
        print("|" + "|".join(cells) + "|")

    print(sep)
    print(f"  {len(rows)} row(s)")


def main():
    parser = argparse.ArgumentParser(description="Query backtest results")
    parser.add_argument("--asset", help="Filter by asset (BTC, ETH, SOL, LINK)")
    parser.add_argument("--version", help="Filter by strategy version (V7, V10, V12)")
    parser.add_argument("--best", action="store_true", help="Best result per asset")
    args = parser.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        print("Run scripts/log_all_versions.py first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)

    col_names = ", ".join(c for _, c, _, _ in COLS)

    if args.best:
        query = f"""
            SELECT {col_names}
            FROM backtest_runs
            WHERE id IN (
                SELECT id FROM backtest_runs b1
                WHERE monthly_return_pct = (
                    SELECT MAX(monthly_return_pct) FROM backtest_runs b2
                    WHERE b2.asset = b1.asset
                )
            )
            ORDER BY monthly_return_pct DESC
        """
        params = []
    else:
        conditions = []
        params = []
        if args.asset:
            conditions.append("asset = ?")
            params.append(args.asset.upper())
        if args.version:
            conditions.append("strategy_version = ?")
            params.append(args.version.upper())

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        query = f"SELECT {col_names} FROM backtest_runs {where} ORDER BY monthly_return_pct DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        print("No results found.")
        return

    print_table(rows, COLS)


if __name__ == "__main__":
    main()
