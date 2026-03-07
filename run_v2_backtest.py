#!/usr/bin/env python3
"""V2.4 Backtest Runner — uses BacktestEngine + CsvDataProvider + CandleV2_4.

Runs all 10 assets, writes results to SQLite (data/results.db),
prints full summary with per-asset breakdown.

Usage:
    python3 run_v2_backtest.py
    MODE=backtest STRATEGY_VERSION=v2.4 python3 run_v2_backtest.py
"""

import os
import sys
import time
import sqlite3
import json
from datetime import datetime

import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.engine import BacktestEngine, BacktestResult, print_result

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STRATEGY_VERSION = os.environ.get("STRATEGY_VERSION", "v2.4").lower()
ASSETS = ["BTC", "ETH", "SOL", "LINK", "ADA", "AVAX", "DOGE", "XRP", "ICP", "SHIB"]
INITIAL_CAPITAL = 5_000.0
CAPITAL_PER_ASSET = INITIAL_CAPITAL / len(ASSETS)  # $500
FEE_PCT = 0.05  # 0.05% taker per side (engine doubles it for round-trip)
MAX_RISK_PCT = 2.0  # 2% of capital per trade
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DATA_DIR, "results.db")

# V2.4 strategy params
V24_PARAMS = dict(
    min_score=2,
    stop_atr=2.0,
    target_atr=3.0,
    use_trailing_stop=True,
    trail_activation_atr=1.5,
    trail_distance_atr=0.5,
    adx_max=40,
    pattern_set="all",
    use_rsi=True,
    use_adx=True,
    use_bb=True,
    use_volume=True,
    base_leverage=2.0,
    cooldown=12,
    time_exit_bars=144,
)

# V2.5 strategy params — all 15 indicators ON, MTF both, tighter trail
V25_PARAMS = dict(
    min_score=1,
    stop_atr=2.0,
    target_atr=4.0,
    use_trailing_stop=True,
    trail_activation_atr=1.5,
    trail_distance_atr=0.3,
    adx_max=50,
    pattern_set="top5",
    use_mtf=True,
    mtf_require="both",
    use_rsi=True, use_stoch_rsi=True, use_williams_r=True,
    use_macd=True, use_cci=True, use_ema_alignment=True,
    use_adx=True, use_bb=True, use_atr_percentile=True,
    use_keltner=True, use_volume=True, use_mfi=True,
    use_obv_slope=True, use_range_position=True, use_hh_ll=True,
    base_leverage=2.0,
    cooldown=12,
    time_exit_bars=144,
)

def _get_strategy():
    """Return (strategy_class, params_dict, label) based on STRATEGY_VERSION."""
    ver = STRATEGY_VERSION.replace("v", "").replace(".", "_")
    if ver == "2_5":
        from strategies.candle_v2_5 import CandleV2_5
        return CandleV2_5, V25_PARAMS, "V2.5"
    else:
        from strategies.candle_v2_4 import CandleV2_4
        return CandleV2_4, V24_PARAMS, "V2.4"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data(asset, start=None, end=None):
    csv_path = os.path.join(DATA_DIR, f"{asset}_USD_hourly.csv")
    if not os.path.exists(csv_path):
        print(f"  WARNING: {csv_path} not found, skipping {asset}")
        return None

    df = pd.read_csv(csv_path)
    df.columns = [c.lower().strip() for c in df.columns]

    ts_col = None
    for candidate in ["timestamp", "date", "datetime", "time"]:
        if candidate in df.columns:
            ts_col = candidate
            break
    if ts_col is None:
        print(f"  WARNING: No timestamp column in {csv_path}")
        return None

    df[ts_col] = pd.to_datetime(df[ts_col], utc=True)
    df = df.set_index(ts_col).sort_index()

    # Filter date range
    if start:
        df = df[df.index >= pd.Timestamp(start, tz="UTC")]
    if end:
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]

    return df


# ---------------------------------------------------------------------------
# SQLite output
# ---------------------------------------------------------------------------
def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time TEXT NOT NULL,
            strategy TEXT NOT NULL,
            params TEXT,
            assets TEXT,
            period TEXT,
            total_pnl_usd REAL,
            total_pnl_pct REAL,
            avg_win_rate REAL,
            max_drawdown_pct REAL,
            sharpe REAL,
            profit_factor REAL,
            total_trades INTEGER,
            fee_pct REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER REFERENCES backtest_runs(id),
            asset TEXT NOT NULL,
            total_trades INTEGER,
            wins INTEGER,
            losses INTEGER,
            win_rate REAL,
            total_pnl_usd REAL,
            total_pnl_pct REAL,
            max_drawdown_pct REAL,
            sharpe REAL,
            profit_factor REAL,
            avg_trade_duration_hours REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER REFERENCES backtest_runs(id),
            asset TEXT NOT NULL,
            entry_time TEXT,
            exit_time TEXT,
            direction TEXT,
            signal TEXT,
            entry_price REAL,
            exit_price REAL,
            stop_price REAL,
            target_price REAL,
            size_usd REAL,
            leverage REAL,
            pnl_usd REAL,
            pnl_pct REAL,
            exit_reason TEXT
        )
    """)
    conn.commit()
    return conn


def save_results(conn, results, params_dict, label="V2.4"):
    """Save all results to SQLite. Returns run_id."""
    # Aggregate stats
    all_trades = sum(r.total_trades for r in results.values())
    all_pnl = sum(r.total_pnl_usd for r in results.values())
    all_pnl_pct = all_pnl / INITIAL_CAPITAL * 100
    win_rates = [r.win_rate for r in results.values() if r.total_trades > 0]
    avg_wr = np.mean(win_rates) if win_rates else 0
    max_dd = max((r.max_drawdown_pct for r in results.values()), default=0)
    sharpes = [r.sharpe_ratio for r in results.values() if r.total_trades > 0]
    avg_sharpe = np.mean(sharpes) if sharpes else 0
    pfs = [r.profit_factor for r in results.values() if r.total_trades > 0]
    avg_pf = np.mean(pfs) if pfs else 0

    periods = []
    for r in results.values():
        if r.period:
            periods.append(r.period)

    cur = conn.execute("""
        INSERT INTO backtest_runs (run_time, strategy, params, assets, period,
            total_pnl_usd, total_pnl_pct, avg_win_rate, max_drawdown_pct,
            sharpe, profit_factor, total_trades, fee_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().isoformat(),
        "candle_" + label.lower().replace(".", "_"),
        json.dumps(params_dict),
        ",".join(results.keys()),
        periods[0] if periods else "",
        round(all_pnl, 2),
        round(all_pnl_pct, 2),
        round(avg_wr, 2),
        round(max_dd, 2),
        round(avg_sharpe, 2),
        round(avg_pf, 2),
        all_trades,
        FEE_PCT,
    ))
    run_id = cur.lastrowid

    for asset, r in results.items():
        conn.execute("""
            INSERT INTO backtest_results (run_id, asset, total_trades, wins, losses,
                win_rate, total_pnl_usd, total_pnl_pct, max_drawdown_pct,
                sharpe, profit_factor, avg_trade_duration_hours)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            run_id, asset, r.total_trades, r.wins, r.losses,
            round(r.win_rate, 2), round(r.total_pnl_usd, 2),
            round(r.total_pnl_pct, 2), round(r.max_drawdown_pct, 2),
            round(r.sharpe_ratio, 2), round(r.profit_factor, 2),
            round(r.avg_trade_duration_hours, 1),
        ))

        for t in r.trades:
            conn.execute("""
                INSERT INTO backtest_trades (run_id, asset, entry_time, exit_time,
                    direction, signal, entry_price, exit_price, stop_price,
                    target_price, size_usd, leverage, pnl_usd, pnl_pct, exit_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id, asset,
                str(t.entry_time) if t.entry_time else None,
                str(t.exit_time) if t.exit_time else None,
                t.direction, t.signal,
                t.entry_price, t.exit_price,
                t.stop_price, t.target_price,
                t.size_usd, t.leverage,
                t.pnl_usd, t.pnl_pct,
                t.exit_reason,
            ))

    conn.commit()
    return run_id


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------
def print_summary(results):
    print("\n" + "=" * 70)
    print("  V2.4 BACKTEST SUMMARY — CandleV2_4 (all 21 patterns)")
    print("  Fee: %.2f%% taker per side | Capital: $%.0f ($%.0f/asset)"
          % (FEE_PCT, INITIAL_CAPITAL, CAPITAL_PER_ASSET))
    print("=" * 70)

    total_pnl = 0
    total_trades = 0
    total_wins = 0
    total_losses = 0
    max_dd = 0
    sharpes = []
    pfs = []

    print("\n  %-6s %6s %5s %6s %10s %8s %7s %7s %6s" % (
        "Asset", "Trades", "WR%", "PF", "PnL $", "PnL %", "MaxDD%", "Sharpe", "AvgH"))
    print("  " + "-" * 65)

    for asset in ASSETS:
        r = results.get(asset)
        if r is None or r.total_trades == 0:
            print("  %-6s %6s" % (asset, "no data"))
            continue

        total_pnl += r.total_pnl_usd
        total_trades += r.total_trades
        total_wins += r.wins
        total_losses += r.losses
        max_dd = max(max_dd, r.max_drawdown_pct)
        if r.sharpe_ratio != 0:
            sharpes.append(r.sharpe_ratio)
        if r.profit_factor != 0:
            pfs.append(r.profit_factor)

        print("  %-6s %6d %5.1f %6.2f %+10.2f %+7.1f%% %6.1f%% %7.2f %5.1fh" % (
            asset, r.total_trades, r.win_rate, r.profit_factor,
            r.total_pnl_usd, r.total_pnl_pct, r.max_drawdown_pct,
            r.sharpe_ratio, r.avg_trade_duration_hours))

    print("  " + "-" * 65)

    overall_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0
    overall_pnl_pct = total_pnl / INITIAL_CAPITAL * 100
    avg_sharpe = np.mean(sharpes) if sharpes else 0
    avg_pf = np.mean(pfs) if pfs else 0

    print("  %-6s %6d %5.1f %6.2f %+10.2f %+7.1f%% %6.1f%% %7.2f" % (
        "TOTAL", total_trades, overall_wr, avg_pf,
        total_pnl, overall_pnl_pct, max_dd, avg_sharpe))

    print(f"\n  Total PnL: ${total_pnl:+,.2f} ({overall_pnl_pct:+.1f}%)")
    print(f"  Win Rate: {overall_wr:.1f}% ({total_wins}/{total_trades})")
    print(f"  Max Drawdown: {max_dd:.1f}%")
    print(f"  Profit Factor: {avg_pf:.2f}")
    print(f"  Sharpe Ratio: {avg_sharpe:.2f}")
    print(f"  Trade Count: {total_trades}")
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    start_date = os.environ.get("BT_START", "2022-01-01")
    end_date = os.environ.get("BT_END", "2026-03-01")

    StrategyCls, params, label = _get_strategy()

    print("=" * 70)
    print("  %s Backtest — %s to %s" % (label, start_date, end_date))
    print("  Params: %s" % params)
    print("=" * 70)

    engine = BacktestEngine(
        initial_capital=CAPITAL_PER_ASSET,
        fee_pct=FEE_PCT,
        max_risk_pct=MAX_RISK_PCT,
    )

    results = {}
    t0 = time.time()

    for asset in ASSETS:
        print(f"\n--- {asset} ---")
        df = load_data(asset, start=start_date, end=end_date)
        if df is None or len(df) < 250:
            print(f"  Skipped: insufficient data ({len(df) if df is not None else 0} bars)")
            continue

        print(f"  Bars: {len(df)} | {df.index[0].date()} to {df.index[-1].date()}")

        strategy = StrategyCls(**params)
        result = engine.run(df, strategy, name=f"{label} {asset}")
        results[asset] = result

        print(f"  Trades: {result.total_trades} | WR: {result.win_rate:.1f}% | "
              f"PnL: ${result.total_pnl_usd:+,.2f} ({result.total_pnl_pct:+.1f}%) | "
              f"DD: {result.max_drawdown_pct:.1f}%")

    elapsed = time.time() - t0
    print(f"\nBacktest completed in {elapsed:.1f}s")

    # Print full summary
    print_summary(results)

    # Write to SQLite
    print(f"\nWriting results to {DB_PATH}...")
    conn = init_db(DB_PATH)
    run_id = save_results(conn, results, params, label=label)
    conn.close()
    print(f"Saved as run_id={run_id}")

    return results


if __name__ == "__main__":
    main()
