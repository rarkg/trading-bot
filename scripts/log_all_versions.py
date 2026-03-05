"""
Run all strategies (V7, V10, V12) x all assets (BTC, ETH, SOL, LINK) x leverages (2x, 3x, 5x)
and log results to data/results.db — comprehensive knowledge base.

Populates: backtest_runs, trades, monthly_breakdown, yearly_breakdown, indicator_analysis, research_notes
"""

import sys
import os
import json
import sqlite3
import math
import pandas as pd
import numpy as np
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestEngine
from strategies.squeeze_only_v7 import SqueezeOnlyV7
from strategies.squeeze_v10 import SqueezeV10
from strategies.squeeze_v12 import SqueezeV12


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "results.db")

STRATEGY_PARAMS = {
    "V7": {
        "squeeze_threshold": 0.65, "bb_period": 20, "bb_std": 2,
        "ema_periods": [8, 21, 55], "daily_ema_spans": [192, 504, 1320],
        "atr_period": 14, "rsi_period": 14, "vol_avg_period": 20,
        "min_body_ratio": 0.4, "min_vol_ratio": 1.2,
        "rsi_long_max": 75, "rsi_short_min": 25,
        "min_confidence": 50, "cooldown_bars": 8,
        "trail_initial_atr": 2.5, "trail_tightening": 0.08,
        "target_atr_mult": 10,
    },
    "V10": {
        "squeeze_threshold": 0.65, "bb_period": 20, "bb_std": 2,
        "ema_periods": [8, 21, 55], "daily_ema_spans": [192, 504, 1320],
        "ema_200d_span": 4800,
        "atr_period": 14, "rsi_period": 14, "vol_avg_period": 20,
        "min_body_ratio": 0.4,
        "rsi_long_range": [45, 68], "rsi_short_range": [32, 55],
        "atr_pct_max": 3.5,
        "vol_good_zones": "<=1.35 or >=2.3", "score_cap": 80,
        "bear_market_filter": True, "btc_crash_guard_pct": -10,
        "min_confidence": 50, "cooldown_bars": 8,
        "trail_initial_atr": 2.5, "trail_tightening": 0.08,
        "time_exit_bars": 10, "time_exit_min_r": 0.5,
        "target_atr_mult": 12,
    },
    "V12": {
        "squeeze_threshold": 0.65, "bb_period": 20, "bb_std": 2,
        "ema_periods": [8, 21, 55], "daily_ema_spans": [192, 504, 1320],
        "ema_200d_span": 4800,
        "atr_period": 14, "rsi_period": 14, "vol_avg_period": 20,
        "min_body_ratio": 0.4,
        "rsi_long_range": [45, 68], "rsi_short_range": [32, 55],
        "atr_pct_max": 3.5,
        "vol_good_zones": "<=1.35 or >=2.3", "score_cap": 80,
        "bear_market_filter": True,
        "range_period": 50, "range_long_max": 0.75, "range_short_min": 0.25,
        "min_confidence": 50, "cooldown_bars": 8,
        "trail_initial_atr": 2.5, "trail_tightening": 0.08,
        "time_exit_bars": 10, "time_exit_min_r": 0.5,
        "target_atr_mult": 12,
    },
}

FEE_PCT = 0.045
INITIAL_CAPITAL = 1000
MAX_RISK_PCT = 5.0


def init_db(conn):
    conn.executescript("""
        DROP TABLE IF EXISTS indicator_analysis;
        DROP TABLE IF EXISTS monthly_breakdown;
        DROP TABLE IF EXISTS yearly_breakdown;
        DROP TABLE IF EXISTS trades;
        DROP TABLE IF EXISTS backtest_runs;
        DROP TABLE IF EXISTS research_notes;

        CREATE TABLE backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_version TEXT NOT NULL,
            asset TEXT NOT NULL,
            leverage REAL NOT NULL,
            monthly_return_pct REAL,
            max_drawdown_pct REAL,
            total_pnl_pct REAL,
            total_trades INTEGER,
            win_rate REAL,
            profit_factor REAL,
            sharpe_ratio REAL,
            avg_win_pct REAL,
            avg_loss_pct REAL,
            period_start TEXT,
            period_end TEXT,
            notes TEXT,
            strategy_params TEXT,
            avg_trade_duration_hours REAL,
            long_trades INTEGER,
            short_trades INTEGER,
            long_win_rate REAL,
            short_win_rate REAL,
            best_month_pct REAL,
            worst_month_pct REAL,
            profitable_months_pct REAL,
            max_consecutive_wins INTEGER,
            max_consecutive_losses INTEGER,
            fee_pct REAL,
            initial_capital REAL,
            regime_breakdown TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES backtest_runs(id),
            asset TEXT NOT NULL,
            entry_time TEXT,
            exit_time TEXT,
            entry_price REAL,
            exit_price REAL,
            direction TEXT,
            signal TEXT,
            confidence_score INTEGER,
            leverage REAL,
            size_usd REAL,
            stop_price REAL,
            target_price REAL,
            exit_reason TEXT,
            pnl_pct REAL,
            pnl_usd REAL,
            duration_hours REAL,
            rsi_at_entry REAL,
            atr_at_entry REAL,
            atr_pct_at_entry REAL,
            vol_ratio_at_entry REAL,
            bb_width_at_entry REAL,
            ema_trend_at_entry TEXT,
            range_position_at_entry REAL,
            adx_at_entry REAL,
            market_regime TEXT
        );

        CREATE TABLE yearly_breakdown (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES backtest_runs(id),
            year INTEGER NOT NULL,
            trades INTEGER,
            wins INTEGER,
            losses INTEGER,
            pnl_usd REAL,
            pnl_pct REAL,
            monthly_avg_pct REAL,
            max_drawdown_pct REAL,
            win_rate REAL
        );

        CREATE TABLE monthly_breakdown (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES backtest_runs(id),
            year INTEGER NOT NULL,
            month INTEGER NOT NULL,
            trades INTEGER,
            wins INTEGER,
            pnl_usd REAL,
            pnl_pct REAL,
            max_drawdown_pct REAL
        );

        CREATE TABLE indicator_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES backtest_runs(id),
            indicator TEXT NOT NULL,
            bucket TEXT NOT NULL,
            trade_count INTEGER,
            win_rate REAL,
            avg_pnl_pct REAL
        );

        CREATE TABLE research_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            topic TEXT NOT NULL,
            finding TEXT NOT NULL,
            confidence TEXT DEFAULT 'medium',
            evidence TEXT,
            actionable BOOLEAN DEFAULT 0,
            applied_in_version TEXT
        );

        CREATE INDEX idx_trades_run ON trades(run_id);
        CREATE INDEX idx_trades_asset ON trades(asset);
        CREATE INDEX idx_trades_direction ON trades(direction);
        CREATE INDEX idx_trades_exit_reason ON trades(exit_reason);
        CREATE INDEX idx_yearly_run ON yearly_breakdown(run_id);
        CREATE INDEX idx_monthly_run ON monthly_breakdown(run_id);
        CREATE INDEX idx_indicator_run ON indicator_analysis(run_id);
        CREATE INDEX idx_research_topic ON research_notes(topic);
    """)
    conn.commit()


def load_data(symbol):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fp = os.path.join(base, "data", f"{symbol}_USD_hourly.csv")
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


class WrappedV7:
    def __init__(self, leverage):
        self._inner = SqueezeOnlyV7()
        self._lev = leverage

    def generate_signal(self, data, i):
        s = self._inner.generate_signal(data, i)
        if s:
            s["leverage"] = self._lev
        return s

    def check_exit(self, data, i, trade):
        return self._inner.check_exit(data, i, trade)


def compute_monthly_data(trades_list):
    """Group trades by year-month and compute stats."""
    monthly = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl_usd": 0.0, "pnl_pct_parts": []})
    for t in trades_list:
        if t.exit_time is None:
            continue
        ym = (t.exit_time.year, t.exit_time.month)
        monthly[ym]["trades"] += 1
        if t.pnl_usd > 0:
            monthly[ym]["wins"] += 1
        monthly[ym]["pnl_usd"] += t.pnl_usd
        monthly[ym]["pnl_pct_parts"].append(t.pnl_pct)
    return monthly


def compute_max_consecutive(trades_list):
    """Compute max consecutive wins and losses."""
    max_wins = max_losses = cur_wins = cur_losses = 0
    for t in trades_list:
        if t.pnl_usd > 0:
            cur_wins += 1
            cur_losses = 0
            max_wins = max(max_wins, cur_wins)
        else:
            cur_losses += 1
            cur_wins = 0
            max_losses = max(max_losses, cur_losses)
    return max_wins, max_losses


def compute_regime_breakdown(trades_list):
    """Compute what % of trades happened in each market regime."""
    counts = defaultdict(int)
    for t in trades_list:
        regime = t.market_regime or "unknown"
        counts[regime] += 1
    total = len(trades_list) or 1
    return {k: round(v / total * 100, 1) for k, v in counts.items()}


def bucket_value(val, indicator):
    """Assign a value to a bucket for indicator analysis."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return None
    if indicator == "rsi":
        for lo in range(0, 100, 10):
            if val < lo + 10:
                return f"{lo}-{lo+10}"
        return "90-100"
    elif indicator == "vol_ratio":
        if val < 1.0:
            return "0-1.0"
        elif val < 1.5:
            return "1.0-1.5"
        elif val < 2.0:
            return "1.5-2.0"
        elif val < 2.5:
            return "2.0-2.5"
        elif val < 3.0:
            return "2.5-3.0"
        else:
            return "3.0+"
    elif indicator == "range_position":
        for lo_pct in range(0, 100, 25):
            lo = lo_pct / 100
            hi = (lo_pct + 25) / 100
            if val < hi:
                return f"{lo:.2f}-{hi:.2f}"
        return "0.75-1.00"
    elif indicator == "atr_pct":
        if val < 1.0:
            return "0-1%"
        elif val < 2.0:
            return "1-2%"
        elif val < 3.0:
            return "2-3%"
        elif val < 4.0:
            return "3-4%"
        else:
            return "4%+"
    elif indicator == "bb_width":
        if val < 1.0:
            return "0-1"
        elif val < 2.0:
            return "1-2"
        elif val < 3.0:
            return "2-3"
        elif val < 4.0:
            return "3-4"
        else:
            return "4+"
    elif indicator == "adx":
        if val < 15:
            return "0-15"
        elif val < 25:
            return "15-25"
        elif val < 35:
            return "25-35"
        elif val < 50:
            return "35-50"
        else:
            return "50+"
    return str(round(val, 1))


def log_run(conn, version, asset, leverage, result, period_years, trades_list):
    """Insert backtest_runs row and return run_id."""
    monthly_data = compute_monthly_data(trades_list)
    monthly_pnls = [sum(m["pnl_pct_parts"]) for m in monthly_data.values()] if monthly_data else []
    best_month = max(monthly_pnls) if monthly_pnls else 0
    worst_month = min(monthly_pnls) if monthly_pnls else 0
    profitable_months = sum(1 for p in monthly_pnls if p > 0)
    profitable_months_pct = (profitable_months / len(monthly_pnls) * 100) if monthly_pnls else 0

    long_trades = [t for t in trades_list if t.direction == "LONG"]
    short_trades = [t for t in trades_list if t.direction == "SHORT"]
    long_wins = sum(1 for t in long_trades if t.pnl_usd > 0)
    short_wins = sum(1 for t in short_trades if t.pnl_usd > 0)

    max_wins, max_losses = compute_max_consecutive(trades_list)
    regime_breakdown = compute_regime_breakdown(trades_list)

    monthly_ret = result.total_pnl_pct / (period_years * 12) if period_years > 0 else 0
    period_parts = result.period.split(" to ")
    period_start = period_parts[0] if len(period_parts) == 2 else ""
    period_end = period_parts[1] if len(period_parts) == 2 else ""

    cursor = conn.execute("""
        INSERT INTO backtest_runs
            (strategy_version, asset, leverage, monthly_return_pct, max_drawdown_pct,
             total_pnl_pct, total_trades, win_rate, profit_factor, sharpe_ratio,
             avg_win_pct, avg_loss_pct, period_start, period_end, notes,
             strategy_params, avg_trade_duration_hours,
             long_trades, short_trades, long_win_rate, short_win_rate,
             best_month_pct, worst_month_pct, profitable_months_pct,
             max_consecutive_wins, max_consecutive_losses,
             fee_pct, initial_capital, regime_breakdown)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        version, asset, leverage,
        round(monthly_ret, 4), result.max_drawdown_pct, result.total_pnl_pct,
        result.total_trades, round(result.win_rate, 2), result.profit_factor,
        result.sharpe_ratio, round(result.avg_win_pct, 4), round(result.avg_loss_pct, 4),
        period_start, period_end, "",
        json.dumps(STRATEGY_PARAMS.get(version, {})),
        result.avg_trade_duration_hours,
        len(long_trades), len(short_trades),
        round(long_wins / len(long_trades) * 100, 2) if long_trades else 0,
        round(short_wins / len(short_trades) * 100, 2) if short_trades else 0,
        round(best_month, 2), round(worst_month, 2), round(profitable_months_pct, 1),
        max_wins, max_losses,
        FEE_PCT, INITIAL_CAPITAL,
        json.dumps(regime_breakdown),
    ))
    return cursor.lastrowid


def log_trades(conn, run_id, asset, trades_list):
    """Insert all individual trades."""
    rows = []
    for t in trades_list:
        dur = None
        if t.exit_time and t.entry_time:
            dur = round((t.exit_time - t.entry_time).total_seconds() / 3600, 2)
        rows.append((
            run_id, asset,
            str(t.entry_time) if t.entry_time else None,
            str(t.exit_time) if t.exit_time else None,
            t.entry_price, t.exit_price, t.direction, t.signal,
            t.confidence_score, t.leverage, t.size_usd,
            t.stop_price, t.target_price, t.exit_reason,
            t.pnl_pct, t.pnl_usd, dur,
            t.rsi_at_entry, t.atr_at_entry, t.atr_pct_at_entry,
            t.vol_ratio_at_entry, t.bb_width_at_entry, t.ema_trend_at_entry,
            t.range_position_at_entry, t.adx_at_entry, t.market_regime,
        ))
    conn.executemany("""
        INSERT INTO trades
            (run_id, asset, entry_time, exit_time, entry_price, exit_price,
             direction, signal, confidence_score, leverage, size_usd,
             stop_price, target_price, exit_reason, pnl_pct, pnl_usd, duration_hours,
             rsi_at_entry, atr_at_entry, atr_pct_at_entry, vol_ratio_at_entry,
             bb_width_at_entry, ema_trend_at_entry, range_position_at_entry,
             adx_at_entry, market_regime)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, rows)


def log_monthly_breakdown(conn, run_id, trades_list):
    """Compute and insert monthly breakdown."""
    monthly = compute_monthly_data(trades_list)
    rows = []
    # For max_drawdown per month, we need running capital
    # Simplified: use sum of pnl_pct as proxy (not exact but good enough)
    for (year, month), m in sorted(monthly.items()):
        pnl_pct = sum(m["pnl_pct_parts"])
        # Approximate max drawdown within month: worst single trade
        worst = min(m["pnl_pct_parts"]) if m["pnl_pct_parts"] else 0
        rows.append((run_id, year, month, m["trades"], m["wins"],
                      round(m["pnl_usd"], 2), round(pnl_pct, 2),
                      round(abs(worst), 2)))
    conn.executemany("""
        INSERT INTO monthly_breakdown (run_id, year, month, trades, wins, pnl_usd, pnl_pct, max_drawdown_pct)
        VALUES (?,?,?,?,?,?,?,?)
    """, rows)


def log_yearly_breakdown(conn, run_id, trades_list):
    """Compute and insert yearly breakdown."""
    yearly = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0,
                                   "pnl_usd": 0.0, "pnl_pcts": [], "monthly_pcts": defaultdict(float)})
    for t in trades_list:
        if t.exit_time is None:
            continue
        y = t.exit_time.year
        yearly[y]["trades"] += 1
        if t.pnl_usd > 0:
            yearly[y]["wins"] += 1
        else:
            yearly[y]["losses"] += 1
        yearly[y]["pnl_usd"] += t.pnl_usd
        yearly[y]["pnl_pcts"].append(t.pnl_pct)
        yearly[y]["monthly_pcts"][t.exit_time.month] += t.pnl_pct

    rows = []
    for year in sorted(yearly):
        d = yearly[year]
        total_pnl_pct = sum(d["pnl_pcts"])
        months_with_data = len(d["monthly_pcts"])
        monthly_avg = total_pnl_pct / months_with_data if months_with_data else 0
        # Max drawdown: running sum of trade pnls
        running = 0
        peak = 0
        max_dd = 0
        for p in d["pnl_pcts"]:
            running += p
            peak = max(peak, running)
            dd = peak - running
            max_dd = max(max_dd, dd)
        wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
        rows.append((run_id, year, d["trades"], d["wins"], d["losses"],
                      round(d["pnl_usd"], 2), round(total_pnl_pct, 2),
                      round(monthly_avg, 2), round(max_dd, 2), round(wr, 1)))
    conn.executemany("""
        INSERT INTO yearly_breakdown
            (run_id, year, trades, wins, losses, pnl_usd, pnl_pct, monthly_avg_pct, max_drawdown_pct, win_rate)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, rows)


def log_indicator_analysis(conn, run_id, trades_list):
    """Bucket trades by indicator values and compute stats."""
    indicators = {
        "rsi": lambda t: t.rsi_at_entry,
        "vol_ratio": lambda t: t.vol_ratio_at_entry,
        "range_position": lambda t: t.range_position_at_entry,
        "atr_pct": lambda t: t.atr_pct_at_entry,
        "bb_width": lambda t: t.bb_width_at_entry,
        "adx": lambda t: t.adx_at_entry,
    }

    rows = []
    for ind_name, getter in indicators.items():
        buckets = defaultdict(lambda: {"count": 0, "wins": 0, "pnl_sum": 0.0})
        for t in trades_list:
            val = getter(t)
            b = bucket_value(val, ind_name)
            if b is None:
                continue
            buckets[b]["count"] += 1
            if t.pnl_usd > 0:
                buckets[b]["wins"] += 1
            buckets[b]["pnl_sum"] += t.pnl_pct

        for bucket_name, stats in sorted(buckets.items()):
            wr = stats["wins"] / stats["count"] * 100 if stats["count"] else 0
            avg_pnl = stats["pnl_sum"] / stats["count"] if stats["count"] else 0
            rows.append((run_id, ind_name, bucket_name, stats["count"],
                          round(wr, 1), round(avg_pnl, 2)))

    conn.executemany("""
        INSERT INTO indicator_analysis (run_id, indicator, bucket, trade_count, win_rate, avg_pnl_pct)
        VALUES (?,?,?,?,?,?)
    """, rows)


def main():
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    assets = ["BTC", "ETH", "SOL", "LINK"]
    leverages = [2.0, 3.0, 5.0]
    strategies = [
        ("V7",  lambda lev: WrappedV7(lev)),
        ("V10", lambda lev: SqueezeV10(fixed_leverage=lev)),
        ("V12", lambda lev: SqueezeV12(fixed_leverage=lev)),
    ]

    total = len(assets) * len(leverages) * len(strategies)
    done = 0

    for asset in assets:
        print(f"\nLoading {asset}...")
        try:
            data = load_data(asset)
        except FileNotFoundError:
            print(f"  [SKIP] No data file for {asset}")
            done += len(leverages) * len(strategies)
            continue

        period_years = (data.index[-1] - data.index[0]).days / 365.25

        for version, make_strat in strategies:
            for lev in leverages:
                done += 1
                label = f"{version} {asset} {lev}x"
                print(f"  [{done}/{total}] {label}...", end=" ", flush=True)

                engine = BacktestEngine(initial_capital=INITIAL_CAPITAL, fee_pct=FEE_PCT, max_risk_pct=MAX_RISK_PCT)
                strat = make_strat(lev)
                result = engine.run(data, strat, label)
                trades_list = result.trades

                run_id = log_run(conn, version, asset, lev, result, period_years, trades_list)
                log_trades(conn, run_id, asset, trades_list)
                log_monthly_breakdown(conn, run_id, trades_list)
                log_yearly_breakdown(conn, run_id, trades_list)
                log_indicator_analysis(conn, run_id, trades_list)
                conn.commit()

                monthly = result.total_pnl_pct / (period_years * 12) if period_years > 0 else 0
                status = "+" if result.total_pnl_pct > 0 else "-"
                print(f"{status} {result.total_trades}t {result.win_rate:.0f}%w "
                      f"{monthly:+.2f}%/mo DD:{result.max_drawdown_pct:.1f}%")

    conn.close()
    print(f"\nDone. Results saved to {DB_PATH}")


if __name__ == "__main__":
    main()
