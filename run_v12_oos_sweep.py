"""
V12 OOS sweep across leverage levels + random period stress test.
Check if OOS improves at higher leverage (2024 turns positive at 7x+).
"""

import sys
import time
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine
from strategies.squeeze_v12 import SqueezeV12


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def mo(result, years):
    return result.total_pnl_pct / (years * 12)


def main():
    sol = load_data("SOL")
    years = (sol.index[-1] - sol.index[0]).days / 365.25
    split = int(len(sol) * 0.6)
    train = sol.iloc[:split]
    test = sol.iloc[split:]
    test_years = years * 0.4

    print("=" * 80)
    print("  V12 OOS Sweep — SOL at multiple leverage levels")
    print(f"  Train: {train.index[0].date()} to {train.index[-1].date()}")
    print(f"  Test:  {test.index[0].date()} to {test.index[-1].date()}")
    print("=" * 80)

    print(f"\n  {'Lev':5s} {'Train':>28s} {'Test':>28s}")
    print(f"  {'-'*5} {'-'*28} {'-'*28}")

    for lev in [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]:
        # Fresh train instance
        engine_tr = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        r_tr = engine_tr.run(train, SqueezeV12(fixed_leverage=lev), f"Train {lev}x")
        m_tr = mo(r_tr, years * 0.6)

        # Fresh test instance
        engine_te = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        r_te = engine_te.run(test, SqueezeV12(fixed_leverage=lev), f"Test {lev}x")
        m_te = mo(r_te, test_years)

        s = "✅" if m_te >= 5 and r_te.max_drawdown_pct <= 35 else ("🟡" if m_te >= 3 else "❌")
        print(f"  {lev:4.0f}x  "
              f"{r_tr.total_trades:3d}t {r_tr.win_rate:.0f}%w {m_tr:+.2f}%/mo DD:{r_tr.max_drawdown_pct:.0f}%  "
              f"  {s}{r_te.total_trades:3d}t {r_te.win_rate:.0f}%w {m_te:+.2f}%/mo DD:{r_te.max_drawdown_pct:.0f}%")

    # Random period sampling (30 random 6-month periods)
    print(f"\n  Random 6-month Period Tests (30 samples, 5x leverage):")
    import random
    random.seed(42)
    period_bars = 180 * 24  # 6 months in hours
    positive = 0
    good = 0
    total_mo = []
    for _ in range(30):
        start = random.randint(1500, len(sol) - period_bars - 100)
        chunk = sol.iloc[start:start + period_bars]
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        r = engine.run(chunk, SqueezeV12(fixed_leverage=5.0), "random")
        m = r.total_pnl_pct / (180 / 30)  # approximate months
        if r.total_pnl_usd > 0:
            positive += 1
        if m >= 5 and r.max_drawdown_pct <= 35:
            good += 1
        total_mo.append(m)

    print(f"  Profitable: {positive}/30 ({positive/30*100:.0f}%)")
    print(f"  Meets 5%/mo+35%DD: {good}/30 ({good/30*100:.0f}%)")
    print(f"  Avg monthly: {sum(total_mo)/len(total_mo):+.2f}%")
    print(f"  Median monthly: {sorted(total_mo)[15]:+.2f}%")


if __name__ == "__main__":
    main()
