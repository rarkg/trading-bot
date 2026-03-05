"""
Query the results knowledge base (data/results.db).

Usage:
    python scripts/query_results.py                          # all runs, sorted by monthly return
    python scripts/query_results.py --asset SOL              # filter by asset
    python scripts/query_results.py --version V12            # filter by version
    python scripts/query_results.py --best                   # best result per asset

    python scripts/query_results.py --trades --asset BTC --direction LONG --exit-reason TRAIL
    python scripts/query_results.py --monthly --asset SOL --version V12
    python scripts/query_results.py --yearly --asset ETH
    python scripts/query_results.py --indicators --asset BTC --indicator rsi
    python scripts/query_results.py --compare V10 V12 --asset SOL
    python scripts/query_results.py --regime --version V10
    python scripts/query_results.py --research
    python scripts/query_results.py --search "Kelly"
    python scripts/query_results.py --add-note --topic "RSI" --finding "RSI>60 loses on BTC" --confidence high
"""

import sys
import os
import json
import sqlite3
import argparse

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "results.db")


def get_conn():
    if not os.path.exists(DB_PATH):
        print(f"Database not found: {DB_PATH}")
        print("Run scripts/log_all_versions.py first.")
        sys.exit(1)
    return sqlite3.connect(DB_PATH)


def fmt_val(val, fmt):
    if val is None:
        return "-"
    try:
        return format(val, fmt)
    except (TypeError, ValueError):
        return str(val)


def print_table(rows, col_defs):
    if not rows:
        print("No results found.")
        return
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


# --- Query modes ---

RUNS_COLS = [
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
    ("L/S",      None,                 7,  "s"),
    ("LW%",      "long_win_rate",      5,  ".1f"),
    ("SW%",      "short_win_rate",     5,  ".1f"),
    ("BstMo",    "best_month_pct",     6,  "+.1f"),
    ("WstMo",    "worst_month_pct",    6,  "+.1f"),
    ("Profit%",  "profitable_months_pct", 7, ".0f"),
]


def query_runs(args):
    conn = get_conn()
    conditions = []
    params = []
    if args.asset:
        conditions.append("asset = ?")
        params.append(args.asset.upper())
    if args.version:
        conditions.append("strategy_version = ?")
        params.append(args.version.upper())
    if getattr(args, 'leverage', None):
        conditions.append("leverage = ?")
        params.append(float(args.leverage))

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    if args.best:
        query = f"""
            SELECT id, strategy_version, asset, leverage, monthly_return_pct,
                   max_drawdown_pct, total_pnl_pct, total_trades, win_rate,
                   profit_factor, sharpe_ratio, long_trades, short_trades,
                   long_win_rate, short_win_rate, best_month_pct, worst_month_pct,
                   profitable_months_pct
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
        query = f"""
            SELECT id, strategy_version, asset, leverage, monthly_return_pct,
                   max_drawdown_pct, total_pnl_pct, total_trades, win_rate,
                   profit_factor, sharpe_ratio, long_trades, short_trades,
                   long_win_rate, short_win_rate, best_month_pct, worst_month_pct,
                   profitable_months_pct
            FROM backtest_runs {where} ORDER BY monthly_return_pct DESC
        """

    rows = conn.execute(query, params).fetchall()
    conn.close()

    # Post-process: merge long_trades/short_trades into L/S column
    processed = []
    for r in rows:
        r = list(r)
        lt, st = r[11], r[12]
        ls_str = f"{lt or 0}/{st or 0}"
        r[11] = ls_str
        del r[12]
        processed.append(r)

    print_table(processed, RUNS_COLS)


def query_trades(args):
    conn = get_conn()
    conditions = ["1=1"]
    params = []

    if args.asset:
        conditions.append("t.asset = ?")
        params.append(args.asset.upper())
    if args.version:
        conditions.append("r.strategy_version = ?")
        params.append(args.version.upper())
    if args.direction:
        conditions.append("t.direction = ?")
        params.append(args.direction.upper())
    if args.exit_reason:
        conditions.append("t.exit_reason = ?")
        params.append(args.exit_reason.upper())
    if getattr(args, 'leverage', None):
        conditions.append("r.leverage = ?")
        params.append(float(args.leverage))

    where = " AND ".join(conditions)
    limit = args.limit if hasattr(args, 'limit') and args.limit else 50

    query = f"""
        SELECT t.id, r.strategy_version, t.asset, t.direction, t.signal,
               t.confidence_score, t.leverage, t.entry_time, t.exit_time,
               t.entry_price, t.exit_price, t.exit_reason,
               t.pnl_pct, t.pnl_usd, t.duration_hours,
               t.rsi_at_entry, t.range_position_at_entry, t.market_regime
        FROM trades t JOIN backtest_runs r ON t.run_id = r.id
        WHERE {where}
        ORDER BY t.pnl_pct DESC
        LIMIT ?
    """
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    cols = [
        ("ID",    None, 5, "d"),
        ("Ver",   None, 4, "s"),
        ("Asset", None, 5, "s"),
        ("Dir",   None, 5, "s"),
        ("Signal",None, 14,"s"),
        ("Score", None, 5, "d"),
        ("Lev",   None, 4, ".1f"),
        ("Entry",      None, 19,"s"),
        ("Exit",       None, 19,"s"),
        ("EntryP",     None, 10,".2f"),
        ("ExitP",      None, 10,".2f"),
        ("Reason",     None, 10,"s"),
        ("PnL%",       None, 7, "+.2f"),
        ("PnL$",       None, 8, "+.2f"),
        ("Hours",      None, 6, ".1f"),
        ("RSI",        None, 5, ".1f"),
        ("RngPos",     None, 6, ".2f"),
        ("Regime",     None, 8, "s"),
    ]
    print_table(rows, cols)


def query_monthly(args):
    conn = get_conn()
    conditions = ["1=1"]
    params = []
    if args.asset:
        conditions.append("r.asset = ?")
        params.append(args.asset.upper())
    if args.version:
        conditions.append("r.strategy_version = ?")
        params.append(args.version.upper())
    if getattr(args, 'leverage', None):
        conditions.append("r.leverage = ?")
        params.append(float(args.leverage))

    where = " AND ".join(conditions)
    query = f"""
        SELECT r.strategy_version, r.asset, r.leverage,
               m.year, m.month, m.trades, m.wins, m.pnl_usd, m.pnl_pct, m.max_drawdown_pct
        FROM monthly_breakdown m JOIN backtest_runs r ON m.run_id = r.id
        WHERE {where}
        ORDER BY r.strategy_version, r.asset, r.leverage, m.year, m.month
    """
    rows = conn.execute(query, params).fetchall()
    conn.close()

    cols = [
        ("Ver",   None, 4, "s"),
        ("Asset", None, 5, "s"),
        ("Lev",   None, 4, ".1f"),
        ("Year",  None, 4, "d"),
        ("Mon",   None, 3, "d"),
        ("Trades",None, 6, "d"),
        ("Wins",  None, 4, "d"),
        ("PnL$",  None, 9, "+.2f"),
        ("PnL%",  None, 8, "+.2f"),
        ("DD%",   None, 6, ".2f"),
    ]
    print_table(rows, cols)


def query_yearly(args):
    conn = get_conn()
    conditions = ["1=1"]
    params = []
    if args.asset:
        conditions.append("r.asset = ?")
        params.append(args.asset.upper())
    if args.version:
        conditions.append("r.strategy_version = ?")
        params.append(args.version.upper())
    if getattr(args, 'leverage', None):
        conditions.append("r.leverage = ?")
        params.append(float(args.leverage))

    where = " AND ".join(conditions)
    query = f"""
        SELECT r.strategy_version, r.asset, r.leverage,
               y.year, y.trades, y.wins, y.losses, y.pnl_usd, y.pnl_pct,
               y.monthly_avg_pct, y.max_drawdown_pct, y.win_rate
        FROM yearly_breakdown y JOIN backtest_runs r ON y.run_id = r.id
        WHERE {where}
        ORDER BY r.strategy_version, r.asset, r.leverage, y.year
    """
    rows = conn.execute(query, params).fetchall()
    conn.close()

    cols = [
        ("Ver",    None, 4, "s"),
        ("Asset",  None, 5, "s"),
        ("Lev",    None, 4, ".1f"),
        ("Year",   None, 4, "d"),
        ("Trades", None, 6, "d"),
        ("Wins",   None, 4, "d"),
        ("Loss",   None, 4, "d"),
        ("PnL$",   None, 10,"+.2f"),
        ("PnL%",   None, 9, "+.2f"),
        ("Avg%/mo",None, 7, "+.2f"),
        ("DD%",    None, 6, ".1f"),
        ("Win%",   None, 5, ".1f"),
    ]
    print_table(rows, cols)


def query_indicators(args):
    conn = get_conn()
    conditions = ["1=1"]
    params = []
    if args.asset:
        conditions.append("r.asset = ?")
        params.append(args.asset.upper())
    if args.version:
        conditions.append("r.strategy_version = ?")
        params.append(args.version.upper())
    if args.indicator:
        conditions.append("ia.indicator = ?")
        params.append(args.indicator.lower())
    if getattr(args, 'leverage', None):
        conditions.append("r.leverage = ?")
        params.append(float(args.leverage))

    where = " AND ".join(conditions)

    # Aggregate across matching runs
    query = f"""
        SELECT ia.indicator, ia.bucket,
               SUM(ia.trade_count) as total_trades,
               ROUND(SUM(ia.win_rate * ia.trade_count) / SUM(ia.trade_count), 1) as avg_win_rate,
               ROUND(SUM(ia.avg_pnl_pct * ia.trade_count) / SUM(ia.trade_count), 2) as avg_pnl
        FROM indicator_analysis ia JOIN backtest_runs r ON ia.run_id = r.id
        WHERE {where}
        GROUP BY ia.indicator, ia.bucket
        ORDER BY ia.indicator, ia.bucket
    """
    rows = conn.execute(query, params).fetchall()
    conn.close()

    cols = [
        ("Indicator", None, 15, "s"),
        ("Bucket",    None, 12, "s"),
        ("Trades",    None, 7,  "d"),
        ("Win%",      None, 6,  ".1f"),
        ("AvgPnL%",   None, 8,  "+.2f"),
    ]
    print_table(rows, cols)


def query_compare(args):
    versions = args.compare
    if len(versions) < 2:
        print("Need at least 2 versions to compare. E.g.: --compare V10 V12")
        return

    conn = get_conn()
    conditions = [f"strategy_version IN ({','.join('?' * len(versions))})"]
    params = list(v.upper() for v in versions)
    if args.asset:
        conditions.append("asset = ?")
        params.append(args.asset.upper())
    if getattr(args, 'leverage', None):
        conditions.append("leverage = ?")
        params.append(float(args.leverage))

    where = " AND ".join(conditions)
    query = f"""
        SELECT strategy_version, asset, leverage,
               monthly_return_pct, max_drawdown_pct, total_pnl_pct,
               total_trades, win_rate, profit_factor, sharpe_ratio,
               long_win_rate, short_win_rate,
               best_month_pct, worst_month_pct, profitable_months_pct
        FROM backtest_runs WHERE {where}
        ORDER BY asset, leverage, strategy_version
    """
    rows = conn.execute(query, params).fetchall()
    conn.close()

    cols = [
        ("Ver",    None, 4, "s"),
        ("Asset",  None, 5, "s"),
        ("Lev",    None, 4, ".1f"),
        ("%/mo",   None, 7, "+.2f"),
        ("DD%",    None, 6, ".1f"),
        ("PnL%",   None, 8, "+.1f"),
        ("Trades", None, 6, "d"),
        ("Win%",   None, 5, ".1f"),
        ("PF",     None, 5, ".2f"),
        ("Sharpe", None, 6, ".2f"),
        ("LW%",    None, 5, ".1f"),
        ("SW%",    None, 5, ".1f"),
        ("BstMo",  None, 6, "+.1f"),
        ("WstMo",  None, 6, "+.1f"),
        ("Pr%Mo",  None, 5, ".0f"),
    ]
    print_table(rows, cols)


def query_regime(args):
    conn = get_conn()
    conditions = ["1=1"]
    params = []
    if args.version:
        conditions.append("strategy_version = ?")
        params.append(args.version.upper())
    if args.asset:
        conditions.append("asset = ?")
        params.append(args.asset.upper())
    if getattr(args, 'leverage', None):
        conditions.append("leverage = ?")
        params.append(float(args.leverage))

    where = " AND ".join(conditions)
    query = f"""
        SELECT id, strategy_version, asset, leverage, regime_breakdown
        FROM backtest_runs WHERE {where}
        ORDER BY strategy_version, asset, leverage
    """
    rows = conn.execute(query, params).fetchall()
    conn.close()

    print(f"\n{'Ver':<5} {'Asset':<6} {'Lev':<5} {'Regime Breakdown'}")
    print("-" * 60)
    for row in rows:
        rid, ver, asset, lev, regime_json = row
        regime = json.loads(regime_json) if regime_json else {}
        parts = [f"{k}: {v}%" for k, v in sorted(regime.items())]
        print(f"{ver:<5} {asset:<6} {lev:<5.1f} {', '.join(parts)}")
    print(f"\n  {len(rows)} row(s)")


def query_research(args):
    conn = get_conn()
    query = "SELECT id, created_at, topic, finding, confidence, actionable, applied_in_version FROM research_notes ORDER BY created_at DESC"
    rows = conn.execute(query).fetchall()
    conn.close()

    if not rows:
        print("No research notes found. Use --add-note to add one.")
        return

    cols = [
        ("ID",      None, 4, "d"),
        ("Date",    None, 19,"s"),
        ("Topic",   None, 20,"s"),
        ("Finding", None, 50,"s"),
        ("Conf",    None, 6, "s"),
        ("Act",     None, 3, "d"),
        ("Applied", None, 8, "s"),
    ]
    print_table(rows, cols)


def query_search(args):
    keyword = args.search
    conn = get_conn()
    query = """
        SELECT id, created_at, topic, finding, confidence, actionable, applied_in_version
        FROM research_notes
        WHERE topic LIKE ? OR finding LIKE ? OR evidence LIKE ?
        ORDER BY created_at DESC
    """
    pattern = f"%{keyword}%"
    rows = conn.execute(query, (pattern, pattern, pattern)).fetchall()
    conn.close()

    if not rows:
        print(f"No research notes matching '{keyword}'.")
        return

    cols = [
        ("ID",      None, 4, "d"),
        ("Date",    None, 19,"s"),
        ("Topic",   None, 20,"s"),
        ("Finding", None, 50,"s"),
        ("Conf",    None, 6, "s"),
        ("Act",     None, 3, "d"),
        ("Applied", None, 8, "s"),
    ]
    print_table(rows, cols)


def add_note(args):
    if not args.topic or not args.finding:
        print("--add-note requires --topic and --finding")
        return
    conn = get_conn()
    confidence = args.confidence or "medium"
    evidence = args.evidence or None
    actionable = 1 if args.actionable else 0
    applied = args.applied or None

    conn.execute("""
        INSERT INTO research_notes (topic, finding, confidence, evidence, actionable, applied_in_version)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (args.topic, args.finding, confidence, evidence, actionable, applied))
    conn.commit()
    conn.close()
    print(f"Research note added: [{confidence}] {args.topic} — {args.finding}")


def main():
    parser = argparse.ArgumentParser(description="Query backtest results knowledge base")

    # Filters (shared)
    parser.add_argument("--asset", help="Filter by asset (BTC, ETH, SOL, LINK)")
    parser.add_argument("--version", help="Filter by strategy version (V7, V10, V12)")
    parser.add_argument("--leverage", help="Filter by leverage (2, 3, 5)")

    # Run query modes
    parser.add_argument("--best", action="store_true", help="Best result per asset")

    # Trade queries
    parser.add_argument("--trades", action="store_true", help="Show individual trades")
    parser.add_argument("--direction", help="Filter trades by direction (LONG/SHORT)")
    parser.add_argument("--exit-reason", help="Filter trades by exit reason (STOP/TARGET/TRAIL/TIME_EXIT/TREND_FLIP/END_OF_DATA)")
    parser.add_argument("--limit", type=int, default=50, help="Max rows for trade queries (default 50)")

    # Breakdown queries
    parser.add_argument("--monthly", action="store_true", help="Monthly P&L breakdown")
    parser.add_argument("--yearly", action="store_true", help="Yearly breakdown")

    # Indicator analysis
    parser.add_argument("--indicators", action="store_true", help="Indicator bucket analysis")
    parser.add_argument("--indicator", help="Specific indicator (rsi, vol_ratio, range_position, atr_pct, bb_width, adx)")

    # Comparison
    parser.add_argument("--compare", nargs="+", help="Compare versions side-by-side (e.g. --compare V10 V12)")

    # Regime
    parser.add_argument("--regime", action="store_true", help="Show regime breakdown")

    # Research notes
    parser.add_argument("--research", action="store_true", help="Show all research notes")
    parser.add_argument("--search", help="Search research notes by keyword")
    parser.add_argument("--add-note", action="store_true", help="Add a research note")
    parser.add_argument("--topic", help="Note topic")
    parser.add_argument("--finding", help="Note finding text")
    parser.add_argument("--confidence", help="Note confidence (high/medium/low)")
    parser.add_argument("--evidence", help="Note evidence (JSON)")
    parser.add_argument("--actionable", action="store_true", help="Mark note as actionable")
    parser.add_argument("--applied", help="Version this was applied in")

    args = parser.parse_args()

    if args.add_note:
        add_note(args)
    elif args.trades:
        query_trades(args)
    elif args.monthly:
        query_monthly(args)
    elif args.yearly:
        query_yearly(args)
    elif args.indicators:
        query_indicators(args)
    elif args.compare:
        query_compare(args)
    elif args.regime:
        query_regime(args)
    elif args.research:
        query_research(args)
    elif args.search:
        query_search(args)
    else:
        query_runs(args)


if __name__ == "__main__":
    main()
