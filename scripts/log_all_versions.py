"""
Run all strategies (V7, V10, V12) x all assets (BTC, ETH, SOL, LINK) x leverages (2x, 3x, 5x)
and log results to data/results.db.
"""

import sys
import os
import sqlite3
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestEngine
from strategies.squeeze_only_v7 import SqueezeOnlyV7
from strategies.squeeze_v10 import SqueezeV10
from strategies.squeeze_v12 import SqueezeV12


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "results.db")


def init_db(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_version TEXT,
            asset TEXT,
            leverage REAL,
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
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def load_data(symbol):
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fp = os.path.join(base, "data", f"{symbol}_USD_hourly.csv")
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


class WrappedV7:
    """Wraps SqueezeOnlyV7 with a fixed leverage override."""
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


def log_result(conn, version, asset, leverage, result, period_years, notes=""):
    monthly = result.total_pnl_pct / (period_years * 12) if period_years > 0 else 0
    period_parts = result.period.split(" to ")
    period_start = period_parts[0] if len(period_parts) == 2 else ""
    period_end = period_parts[1] if len(period_parts) == 2 else ""

    conn.execute("""
        INSERT INTO backtest_runs
            (strategy_version, asset, leverage, monthly_return_pct, max_drawdown_pct,
             total_pnl_pct, total_trades, win_rate, profit_factor, sharpe_ratio,
             avg_win_pct, avg_loss_pct, period_start, period_end, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        version, asset, leverage,
        round(monthly, 4),
        result.max_drawdown_pct,
        result.total_pnl_pct,
        result.total_trades,
        round(result.win_rate, 2),
        result.profit_factor,
        result.sharpe_ratio,
        round(result.avg_win_pct, 4),
        round(result.avg_loss_pct, 4),
        period_start,
        period_end,
        notes,
    ))
    conn.commit()


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
            continue

        period_years = (data.index[-1] - data.index[0]).days / 365.25

        for version, make_strat in strategies:
            for lev in leverages:
                done += 1
                label = f"{version} {asset} {lev}x"
                print(f"  [{done}/{total}] {label}...", end=" ", flush=True)

                engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
                strat = make_strat(lev)
                result = engine.run(data, strat, label)

                monthly = result.total_pnl_pct / (period_years * 12) if period_years > 0 else 0
                log_result(conn, version, asset, lev, result, period_years)

                status = "+" if result.total_pnl_pct > 0 else "-"
                print(f"{status} {result.total_trades}t {result.win_rate:.0f}%w "
                      f"{monthly:+.2f}%/mo DD:{result.max_drawdown_pct:.1f}%")

    conn.close()
    print(f"\nDone. Results saved to {DB_PATH}")


if __name__ == "__main__":
    main()
