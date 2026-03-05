"""
V8 Comprehensive Test:
1. V8 squeeze on each asset at various leverage levels
2. Multi-asset portfolio (BTC+ETH+SOL simultaneously)
3. Best single asset vs portfolio comparison
4. Out-of-sample validation
5. Regime stress tests
"""

import sys
import time
import os
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from backtest.multi_engine import MultiAssetEngine
from strategies.squeeze_v8 import SqueezeV8
from strategies.squeeze_only_v7 import SqueezeOnlyV7


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def monthly(result, years):
    return result.total_pnl_pct / (years * 12)


def run_leverage_sweep(data, years, symbol="SOL"):
    """Find best leverage for V8 on a given asset."""
    print(f"\n  {symbol} V8 Leverage Sweep:")
    best = None
    for lev in [2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]:
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat = SqueezeV8(fixed_leverage=lev)
        result = engine.run(data, strat, f"{symbol} V8 {lev}x")
        mo = monthly(result, years)
        hit_target = mo >= 5.0 and result.max_drawdown_pct <= 35.0
        status = "✅" if hit_target else ("🟡" if mo >= 3 else "❌")
        print(f"    {status} {lev}x: {result.total_trades}t "
              f"{result.win_rate:.0f}%w ${result.total_pnl_usd:+,.0f} "
              f"({mo:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}% "
              f"PF:{result.profit_factor:.2f}")
        if hit_target and (best is None or mo > monthly(best, years)):
            best = result
    return best


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    assets = {sym: load_data(sym) for sym in ["BTC", "ETH", "SOL"]}
    years_map = {sym: (df.index[-1] - df.index[0]).days / 365.25
                 for sym, df in assets.items()}

    print("=" * 90)
    print("  V8 Comprehensive Test — Improved Squeeze + Multi-Asset Portfolio")
    print("=" * 90)

    # =========================================================
    # SECTION 1: V8 vs V7 comparison at same leverage
    # =========================================================
    print("\n[1] V8 vs V7 baseline comparison (SOL, fixed 5x leverage)")
    for name, cls, kwargs in [("V7 baseline", SqueezeOnlyV7, {}),
                               ("V8 improved", SqueezeV8, {"min_score": 55})]:
        # Wrap in fixed-leverage
        class FixedLevWrapper:
            def __init__(self, base_cls, base_kwargs, lev):
                self._inner = base_cls(**base_kwargs)
                self._lev = lev
            def _precompute(self, data):
                self._inner._precompute(data)
            def generate_signal(self, data, i):
                sig = self._inner.generate_signal(data, i)
                if sig:
                    sig["leverage"] = self._lev
                return sig
            def check_exit(self, data, i, trade):
                return self._inner.check_exit(data, i, trade)

        for lev in [5.0]:
            engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
            sol = assets["SOL"]
            wrapped = FixedLevWrapper(cls, kwargs, lev)
            result = engine.run(sol, wrapped, f"{name} {lev}x")
            mo = monthly(result, years_map["SOL"])
            print(f"  {name:20s} {lev}x: {result.total_trades}t "
                  f"{result.win_rate:.0f}%w ${result.total_pnl_usd:+,.0f} "
                  f"({mo:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}%")

    # =========================================================
    # SECTION 2: V8 Leverage sweep on all 3 assets
    # =========================================================
    print("\n[2] V8 Leverage Sweep (dynamic leverage, min_score=55)")
    sweep_results = {}
    for sym in ["BTC", "ETH", "SOL"]:
        best = run_leverage_sweep(assets[sym], years_map[sym], sym)
        sweep_results[sym] = best

    # =========================================================
    # SECTION 3: Multi-asset portfolio
    # =========================================================
    print("\n[3] Multi-Asset Portfolio (BTC+ETH+SOL simultaneously)")
    for lev in [4.0, 5.0, 6.0]:
        engine = MultiAssetEngine(
            initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0,
            max_open_positions=3, capital_per_slot=0.33
        )
        strats = {sym: SqueezeV8(fixed_leverage=lev) for sym in ["BTC", "ETH", "SOL"]}
        result = engine.run(assets, strats, f"Portfolio V8 {lev}x")
        yrs = years_map["SOL"]
        mo = monthly(result, yrs)
        hit = "✅" if mo >= 5 and result.max_drawdown_pct <= 35 else ("🟡" if mo >= 3 else "❌")
        print(f"  {hit} Portfolio {lev}x: {result.total_trades}t "
              f"{result.win_rate:.0f}%w ${result.total_pnl_usd:+,.0f} "
              f"({mo:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}% "
              f"PF:{result.profit_factor:.2f}")

        if hit == "✅":
            print(f"\n  *** PORTFOLIO MEETS TARGET ***")
            # Show per-asset breakdown
            for sym in ["BTC", "ETH", "SOL"]:
                sym_trades = [t for t in result.trades if t.signal.startswith(sym)]
                sym_pnl = sum(t.pnl_usd for t in sym_trades)
                sym_wins = sum(1 for t in sym_trades if t.pnl_usd > 0)
                print(f"    {sym}: {len(sym_trades)}t, {sym_wins}w, ${sym_pnl:+,.2f}")

    # =========================================================
    # SECTION 4: SOL-only deep dive at best leverage
    # =========================================================
    print("\n[4] SOL V8 Deep Dive — Best Config")
    best_sol_lev = 7.0  # Start with high leverage, find sweet spot
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strat = SqueezeV8(fixed_leverage=best_sol_lev)
    result = engine.run(assets["SOL"], strat, f"SOL V8 {best_sol_lev}x")
    mo = monthly(result, years_map["SOL"])

    print(f"  SOL V8 {best_sol_lev}x: {mo:+.2f}%/mo, DD:{result.max_drawdown_pct:.1f}%")
    print(f"\n  Per-year breakdown:")
    for yr in [2022, 2023, 2024, 2025, 2026]:
        yr_trades = [t for t in result.trades if t.entry_time.year == yr]
        if yr_trades:
            yr_pnl = sum(t.pnl_usd for t in yr_trades)
            yr_wins = sum(1 for t in yr_trades if t.pnl_usd > 0)
            print(f"    {yr}: {len(yr_trades)}t {yr_wins}w ${yr_pnl:+,.0f}")

    # =========================================================
    # SECTION 5: Out-of-sample validation
    # =========================================================
    print("\n[5] Out-of-Sample Validation (60% train / 40% test)")
    for sym in ["BTC", "SOL"]:
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat = SqueezeV8(fixed_leverage=5.0)
        train_r, test_r = engine.run_split(assets[sym], strat, sym)
        test_years = years_map[sym] * 0.4
        test_mo = monthly(test_r, test_years)
        train_mo = monthly(train_r, years_map[sym] * 0.6)
        print(f"  {sym}: Train {train_mo:+.2f}%/mo | Test {test_mo:+.2f}%/mo "
              f"DD:{test_r.max_drawdown_pct:.1f}% "
              f"{'✅ OOS PASS' if test_mo >= 5 and test_r.max_drawdown_pct <= 35 else '❌'}")

    # =========================================================
    # SECTION 6: Regime stress tests
    # =========================================================
    print("\n[6] Regime Stress Tests")
    regimes = {
        "2022-Crash":    ("2022-01-01", "2022-11-30"),
        "FTX-Collapse":  ("2022-10-01", "2022-12-31"),
        "2023-Recovery": ("2023-01-01", "2023-12-31"),
        "2024-Bull":     ("2024-01-01", "2024-12-31"),
        "2025-Present":  ("2025-01-01", "2026-03-05"),
    }
    sol = assets["SOL"]
    for regime_name, (start, end) in regimes.items():
        chunk = sol[(sol.index >= start) & (sol.index <= end)]
        if len(chunk) < 200:
            continue
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat = SqueezeV8(fixed_leverage=5.0)
        result = engine.run(chunk, strat, regime_name)
        chunk_months = len(chunk) / (24 * 30)
        mo = result.total_pnl_pct / chunk_months if chunk_months > 0 else 0
        status = "✅" if result.total_pnl_usd > 0 else "❌"
        print(f"  {status} {regime_name:20s}: {result.total_trades}t "
              f"${result.total_pnl_usd:+,.0f} ({mo:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}%")

    elapsed = time.time() - t_start
    print(f"\n  Total runtime: {elapsed:.1f}s")

    # =========================================================
    # Log results
    # =========================================================
    log_entry = "\n## V8 Comprehensive Test\n\nRun completed. See console output above.\n"
    with open("results/iteration_log.md", "a") as f:
        f.write(log_entry)

    print(f"\n  Logged to results/iteration_log.md")


if __name__ == "__main__":
    main()
