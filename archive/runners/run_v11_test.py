"""
V11 Test — Squeeze with Duration + Momentum Rush Filter.
Target: Fix 2024 problem while keeping 5%/mo with ≤35% DD.
"""

import sys
import time
import os
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.squeeze_v10 import SqueezeV10
from strategies.squeeze_v11 import SqueezeV11


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def mo(result, years):
    return result.total_pnl_pct / (years * 12)


def run(data, cls, kwargs, years, lev=None, name=""):
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    k = dict(kwargs)
    if lev is not None:
        k["fixed_leverage"] = lev
    strat = cls(**k)
    result = engine.run(data, strat, name or f"{cls.__name__} {lev}x")
    monthly = mo(result, years)
    hit = monthly >= 5.0 and result.max_drawdown_pct <= 35.0
    stretch = monthly >= 10.0 and result.max_drawdown_pct <= 35.0
    status = "🏆" if stretch else ("✅" if hit else ("🟡" if monthly >= 3 else "❌"))
    return result, monthly, status


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    assets = {sym: load_data(sym) for sym in ["BTC", "ETH", "SOL"]}
    sol, eth, btc = assets["SOL"], assets["ETH"], assets["BTC"]
    years = (sol.index[-1] - sol.index[0]).days / 365.25

    print("=" * 90)
    print("  V11 Test — Squeeze Duration + Momentum Rush Filter")
    print(f"  {years:.1f} years | Target: 5%/mo ≤35% DD | Stretch: 10%/mo ≤35% DD")
    print("=" * 90)

    # =====================================================
    # 1. V11 parameter sweep
    # =====================================================
    print("\n[1] V11 Parameter Sweep on SOL")
    print(f"\n  {'Config':40s} {'3x':>22s} {'5x':>22s}")
    print(f"  {'-'*40} {'-'*22} {'-'*22}")

    configs = [
        ("V10 baseline",     SqueezeV10, {}),
        ("V11 sq20 rush18",  SqueezeV11, {"min_squeeze_bars": 20, "momentum_rush_pct": 18}),
        ("V11 sq15 rush18",  SqueezeV11, {"min_squeeze_bars": 15, "momentum_rush_pct": 18}),
        ("V11 sq20 rush25",  SqueezeV11, {"min_squeeze_bars": 20, "momentum_rush_pct": 25}),
        ("V11 sq10 rush18",  SqueezeV11, {"min_squeeze_bars": 10, "momentum_rush_pct": 18}),
        ("V11 sq20 rush15",  SqueezeV11, {"min_squeeze_bars": 20, "momentum_rush_pct": 15}),
        ("V11 sq30 rush18",  SqueezeV11, {"min_squeeze_bars": 30, "momentum_rush_pct": 18}),
    ]

    for name, cls, kwargs in configs:
        row = f"  {name:40s}"
        for lev in [3.0, 5.0]:
            result, monthly, status = run(sol, cls, kwargs, years, lev)
            row += f"  {status}{result.total_trades}t {result.win_rate:.0f}%w {monthly:+.1f}%/mo DD:{result.max_drawdown_pct:.0f}%"
        print(row)

    # =====================================================
    # 2. Best V11 config — full leverage sweep
    # =====================================================
    print("\n[2] V11 (sq20, rush18) Full Leverage Sweep on SOL")
    best_result = None
    best_mo = -999
    for lev in [2.0, 2.5, 3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]:
        result, monthly, status = run(sol, SqueezeV11,
                                       {"min_squeeze_bars": 20, "momentum_rush_pct": 18},
                                       years, lev, f"V11 SOL {lev}x")
        print(f"  {status} {lev}x: {result.total_trades}t {result.win_rate:.0f}%w "
              f"${result.total_pnl_usd:+,.0f} ({monthly:+.2f}%/mo) "
              f"DD:{result.max_drawdown_pct:.1f}% PF:{result.profit_factor:.2f}")
        if monthly >= 5.0 and result.max_drawdown_pct <= 35.0:
            if monthly > best_mo:
                best_mo = monthly
                best_result = result

    # =====================================================
    # 3. Regime tests
    # =====================================================
    print("\n[3] V11 Regime Tests (3x leverage)")
    regimes = [
        ("Full Period",    "2022-01-01", "2026-03-05"),
        ("2022 Crash",     "2022-01-01", "2022-12-31"),
        ("2023 Recovery",  "2023-01-01", "2023-12-31"),
        ("2024 Bull Run",  "2024-01-01", "2024-12-31"),
        ("2025 Present",   "2025-01-01", "2026-03-05"),
    ]
    for regime, start, end in regimes:
        chunk = sol[(sol.index >= start) & (sol.index <= end)]
        if len(chunk) < 300:
            continue
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat = SqueezeV11(fixed_leverage=3.0, min_squeeze_bars=20, momentum_rush_pct=18)
        result = engine.run(chunk, strat, regime)
        chunk_years = len(chunk) / (24 * 365.25)
        m = mo(result, chunk_years) if chunk_years > 0.05 else 0
        s = "✅" if result.total_pnl_usd >= 0 else "❌"
        print(f"  {s} {regime:22s}: {result.total_trades:3d}t ${result.total_pnl_usd:+,.0f} "
              f"({m:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}%")

    # =====================================================
    # 4. V11 on BTC and ETH
    # =====================================================
    print("\n[4] V11 on all 3 assets (3x and 5x)")
    for sym in ["BTC", "ETH", "SOL"]:
        data = assets[sym]
        yr = (data.index[-1] - data.index[0]).days / 365.25
        print(f"\n  {sym}:")
        for lev in [3.0, 5.0, 7.0]:
            result, monthly, status = run(data, SqueezeV11,
                                           {"min_squeeze_bars": 20, "momentum_rush_pct": 18},
                                           yr, lev, f"V11 {sym} {lev}x")
            print(f"    {status} {lev}x: {result.total_trades}t {result.win_rate:.0f}%w "
                  f"({monthly:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}% "
                  f"PF:{result.profit_factor:.2f}")

    # =====================================================
    # 5. OOS Validation
    # =====================================================
    print("\n[5] OOS Validation (60/40 train/test)")
    for sym in ["SOL", "BTC"]:
        data = assets[sym]
        yr = (data.index[-1] - data.index[0]).days / 365.25
        split = int(len(data) * 0.6)
        train = data.iloc[:split]
        test = data.iloc[split:]

        # Train
        engine_tr = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat_tr = SqueezeV11(fixed_leverage=3.0, min_squeeze_bars=20, momentum_rush_pct=18)
        r_tr = engine_tr.run(train, strat_tr, f"{sym} Train")
        m_tr = mo(r_tr, yr * 0.6)

        # Test (fresh instance)
        engine_te = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat_te = SqueezeV11(fixed_leverage=3.0, min_squeeze_bars=20, momentum_rush_pct=18)
        r_te = engine_te.run(test, strat_te, f"{sym} Test")
        m_te = mo(r_te, yr * 0.4)

        pass_te = m_te >= 5.0 and r_te.max_drawdown_pct <= 35.0
        print(f"  {sym} Train: {r_tr.total_trades}t {r_tr.win_rate:.0f}%w "
              f"({m_tr:+.2f}%/mo) DD:{r_tr.max_drawdown_pct:.1f}%")
        print(f"  {sym} Test:  {r_te.total_trades}t {r_te.win_rate:.0f}%w "
              f"({m_te:+.2f}%/mo) DD:{r_te.max_drawdown_pct:.1f}% "
              f"{'✅ OOS PASS' if pass_te else ('🟡 near' if m_te > 3 else '❌')}")
        print()

    # =====================================================
    # 6. Deep dive — per year for best V11 config
    # =====================================================
    print("\n[6] V11 Deep Dive (SOL, 3x) — Per Year")
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strat = SqueezeV11(fixed_leverage=3.0, min_squeeze_bars=20, momentum_rush_pct=18)
    result = engine.run(sol, strat, "V11 SOL 3x")
    monthly = mo(result, years)

    print_result(result)
    print(f"  Monthly: {monthly:+.2f}%/mo | Annual: {result.total_pnl_pct/years:+.1f}%/yr")

    for yr in [2022, 2023, 2024, 2025, 2026]:
        yr_trades = [t for t in result.trades if t.entry_time.year == yr]
        if yr_trades:
            yr_pnl = sum(t.pnl_usd for t in yr_trades)
            yr_wins = sum(1 for t in yr_trades if t.pnl_usd > 0)
            yr_longs = [t for t in yr_trades if t.direction == "LONG"]
            yr_shorts = [t for t in yr_trades if t.direction == "SHORT"]
            print(f"    {yr}: {len(yr_trades)}t {yr_wins}w ${yr_pnl:+,.0f} "
                  f"(L:{len(yr_longs)} ${sum(t.pnl_usd for t in yr_longs):+,.0f} | "
                  f"S:{len(yr_shorts)} ${sum(t.pnl_usd for t in yr_shorts):+,.0f})")

    elapsed = time.time() - t_start
    print(f"\n  Runtime: {elapsed:.1f}s")

    # Log
    log = f"""
## V11 — Squeeze Duration + Momentum Rush Filter

### Key Results (SOL, 3x leverage)
- Monthly: {monthly:+.2f}%/mo
- Max DD: {result.max_drawdown_pct:.1f}%
- Trades: {result.total_trades}
- Win rate: {result.win_rate:.1f}%
- Profit factor: {result.profit_factor:.2f}

### V11 New Filters
1. Squeeze must persist for 20 bars minimum (durable consolidation)
2. Momentum rush guard: skip longs if price up >18% in 7 days
3. Price position: don't buy in top 25% of 50-bar range
4. ATR cap: 2.8% max (tighter than V10's 3.5%)

Runtime: {elapsed:.1f}s
"""
    with open("results/iteration_log.md", "a") as f:
        f.write(log)


if __name__ == "__main__":
    main()
