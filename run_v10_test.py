"""
V10 Test — Optimized Squeeze with data-driven filters + Hidden Divergence.
Tests all new strategies on all 3 assets.
"""

import sys
import time
import os
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.squeeze_only_v7 import SqueezeOnlyV7
from strategies.squeeze_v10 import SqueezeV10
from strategies.hidden_divergence import HiddenDivergence


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def run_leverage_sweep(data, strategy_cls, kwargs, years, name, levs=None):
    """Sweep leverage and find best config."""
    if levs is None:
        levs = [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0]
    results = []
    for lev in levs:
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        k = dict(kwargs)
        k["fixed_leverage"] = lev
        strat = strategy_cls(**k)
        result = engine.run(data, strat, f"{name} {lev}x")
        mo = result.total_pnl_pct / (years * 12)
        hit = mo >= 5.0 and result.max_drawdown_pct <= 35.0
        status = "✅" if hit else ("🟡" if mo >= 3.0 else "❌")
        print(f"    {status} {lev}x: {result.total_trades}t {result.win_rate:.0f}%w "
              f"${result.total_pnl_usd:+,.0f} ({mo:+.2f}%/mo) "
              f"DD:{result.max_drawdown_pct:.1f}% PF:{result.profit_factor:.2f}")
        results.append((lev, result, mo, hit))
    return results


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    assets = {sym: load_data(sym) for sym in ["BTC", "ETH", "SOL"]}
    btc = assets["BTC"]
    sol = assets["SOL"]
    eth = assets["ETH"]
    years = (sol.index[-1] - sol.index[0]).days / 365.25

    print("=" * 90)
    print("  V10 Test — Optimized Filters + Hidden Divergence")
    print(f"  {len(sol)} candles | {years:.1f} years | 2022-2026")
    print("=" * 90)

    # =====================================================
    # 1. V10 on SOL with BTC crash guard
    # =====================================================
    print("\n[1] V10 Optimized Squeeze on SOL (with BTC crash guard)")
    strat = SqueezeV10()
    strat.set_btc_data(btc)
    run_leverage_sweep(sol, SqueezeV10, {}, years, "SOL V10")

    # =====================================================
    # 2. V10 without crash guard (see how much it helps)
    # =====================================================
    print("\n[2] V10 without BTC crash guard (baseline)")
    run_leverage_sweep(sol, SqueezeV10, {}, years, "SOL V10-nc",
                       levs=[5.0, 7.0, 10.0])

    # =====================================================
    # 3. V10 on all 3 assets
    # =====================================================
    print("\n[3] V10 on BTC and ETH")
    for sym, data in [("BTC", btc), ("ETH", eth)]:
        print(f"\n  --- {sym} ---")
        run_leverage_sweep(data, SqueezeV10, {}, years, f"{sym} V10",
                           levs=[5.0, 7.0, 10.0])

    # =====================================================
    # 4. Hidden Divergence on all 3 assets
    # =====================================================
    print("\n[4] Hidden RSI Divergence on all 3 assets")
    for sym, data in [("BTC", btc), ("ETH", eth), ("SOL", sol)]:
        print(f"\n  --- {sym} ---")
        run_leverage_sweep(data, HiddenDivergence, {}, years, f"{sym} HDIV",
                           levs=[3.0, 5.0, 7.0, 10.0])

    # =====================================================
    # 5. V7 vs V10 direct comparison at 5x and 7x
    # =====================================================
    print("\n[5] V7 vs V10 Direct Comparison (SOL)")
    print(f"\n  {'Strategy':30s} {'5x':>25s} {'7x':>25s}")
    print(f"  {'-'*30} {'-'*25} {'-'*25}")

    for name, cls, kwargs in [
        ("V7 baseline", SqueezeOnlyV7, {}),
        ("V10 optimized", SqueezeV10, {}),
        ("V10 + BTC guard", SqueezeV10, {"btc_data": btc}),
    ]:
        row = f"  {name:30s}"
        for lev in [5.0, 7.0]:
            engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)

            class FixedLev:
                def __init__(self):
                    if "btc_data" in kwargs and kwargs["btc_data"] is not None:
                        self._inner = cls(fixed_leverage=lev)
                        self._inner.set_btc_data(kwargs["btc_data"])
                    else:
                        self._inner = cls(fixed_leverage=lev) if hasattr(cls, "fixed_leverage") else cls()
                def _wrap_gen(self, data, i):
                    sig = self._inner.generate_signal(data, i)
                    if sig:
                        sig["leverage"] = lev
                    return sig
                def generate_signal(self, data, i):
                    return self._wrap_gen(data, i)
                def check_exit(self, data, i, trade):
                    return self._inner.check_exit(data, i, trade)

            wrapped = FixedLev()
            result = engine.run(sol, wrapped, f"{name} {lev}x")
            mo = result.total_pnl_pct / (years * 12)
            row += f"  {result.total_trades}t {result.win_rate:.0f}%w {mo:+.2f}%/mo DD:{result.max_drawdown_pct:.0f}%"
        print(row)

    # =====================================================
    # 6. Deep dive on best V10 config
    # =====================================================
    print("\n[6] V10 SOL Deep Dive at 7x")
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strat = SqueezeV10(fixed_leverage=7.0)
    strat.set_btc_data(btc)
    result = engine.run(sol, strat, "SOL V10 7x")
    mo = result.total_pnl_pct / (years * 12)
    print(f"  {result.total_trades}t {result.win_rate:.0f}%w ${result.total_pnl_usd:+,.0f} "
          f"({mo:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}%")

    print(f"\n  Per-year:")
    for yr in [2022, 2023, 2024, 2025, 2026]:
        yr_trades = [t for t in result.trades if t.entry_time.year == yr]
        if yr_trades:
            yr_pnl = sum(t.pnl_usd for t in yr_trades)
            yr_wins = sum(1 for t in yr_trades if t.pnl_usd > 0)
            yr_shorts = [t for t in yr_trades if t.direction == "SHORT"]
            yr_longs = [t for t in yr_trades if t.direction == "LONG"]
            print(f"    {yr}: {len(yr_trades)}t {yr_wins}w ${yr_pnl:+,.0f} "
                  f"(L:{len(yr_longs)} S:{len(yr_shorts)})")

    # OOS validation
    print(f"\n[7] OOS Validation (60/40 split)")
    for sym, data in [("SOL", sol), ("BTC", btc)]:
        split_idx = int(len(data) * 0.6)
        train = data.iloc[:split_idx]
        test = data.iloc[split_idx:]
        test_years = years * 0.4

        engine_t = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat_t = SqueezeV10(fixed_leverage=7.0)
        result_t = engine_t.run(test, strat_t, f"{sym} Test")
        mo_t = result_t.total_pnl_pct / (test_years * 12)
        print(f"  {sym} TEST: {result_t.total_trades}t {result_t.win_rate:.0f}%w "
              f"${result_t.total_pnl_usd:+,.0f} ({mo_t:+.2f}%/mo) "
              f"DD:{result_t.max_drawdown_pct:.1f}% "
              f"{'✅ PASS' if mo_t >= 5 and result_t.max_drawdown_pct <= 35 else '❌'}")

    elapsed = time.time() - t_start
    print(f"\n  Total: {elapsed:.1f}s")

    # Log
    log = f"""
## V10 Optimized + Hidden Divergence Tests

### V10 Key Changes vs V7
- RSI gate: 45-68 for longs (was 0-75)
- Volume filter: skip 1.35-2.3x zone
- Score cap: 80 max
- No longs in bear market (below 200d EMA)
- BTC crash guard: no longs if BTC -10%+ in 24h

### Runtime: {elapsed:.1f}s
"""
    with open("results/iteration_log.md", "a") as f:
        f.write(log)


if __name__ == "__main__":
    main()
