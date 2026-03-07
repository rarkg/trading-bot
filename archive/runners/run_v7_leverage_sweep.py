"""Test V7 at different fixed leverage levels to find 5%/mo sweet spot."""

import sys
import pandas as pd
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.squeeze_only_v7 import SqueezeOnlyV7


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


# Monkey-patch the strategy to use fixed leverage
class FixedLevSqueeze(SqueezeOnlyV7):
    def __init__(self, fixed_lev=2.0):
        super().__init__()
        self._fixed_lev = fixed_lev
    
    def generate_signal(self, data, i):
        sig = super().generate_signal(data, i)
        if sig:
            sig["leverage"] = self._fixed_lev
        return sig


class LongOnlySqueeze(SqueezeOnlyV7):
    """Only take long signals — shorts barely break even."""
    def __init__(self, fixed_lev=2.0):
        super().__init__()
        self._fixed_lev = fixed_lev
    
    def generate_signal(self, data, i):
        sig = super().generate_signal(data, i)
        if sig and sig["action"] == "SHORT":
            return None  # Skip shorts
        if sig:
            sig["leverage"] = self._fixed_lev
        return sig


def main():
    sol = load_data("data/SOL_USD_hourly.csv")
    years = (sol.index[-1] - sol.index[0]).days / 365.25
    months = years * 12
    
    print("=" * 80)
    print("  V7 LEVERAGE SWEEP — Finding 5%/month")
    print("=" * 80)
    
    print("\n  LONG+SHORT (all signals):")
    for lev in [1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0]:
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strategy = FixedLevSqueeze(lev)
        result = engine.run(sol, strategy, "SOL")
        mo = result.total_pnl_pct / months
        ann = result.total_pnl_pct / years
        hit = "🎯" if mo >= 5 else "❌"
        print(f"    {hit} {lev:4.1f}x: {result.total_trades}t, {result.win_rate:.0f}%w, "
              f"${result.total_pnl_usd:+,.0f} ({mo:+.2f}%/mo, {ann:+.1f}%/yr), DD {result.max_drawdown_pct:.0f}%")
    
    print("\n  LONG ONLY (skip shorts — they barely make money):")
    for lev in [1.0, 2.0, 3.0, 4.0, 5.0, 7.0, 10.0]:
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strategy = LongOnlySqueeze(lev)
        result = engine.run(sol, strategy, "SOL")
        mo = result.total_pnl_pct / months
        ann = result.total_pnl_pct / years
        hit = "🎯" if mo >= 5 else "❌"
        print(f"    {hit} {lev:4.1f}x: {result.total_trades}t, {result.win_rate:.0f}%w, "
              f"${result.total_pnl_usd:+,.0f} ({mo:+.2f}%/mo, {ann:+.1f}%/yr), DD {result.max_drawdown_pct:.0f}%")
    
    # Best config deep dive
    print(f"\n{'=' * 80}")
    print(f"  DEEP DIVE: Long-only 5x")
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strategy = LongOnlySqueeze(5.0)
    result = engine.run(sol, strategy, "SOL 5x Long")
    print_result(result)
    
    print(f"  Per-year:")
    for yr in [2022, 2023, 2024, 2025, 2026]:
        yr_trades = [t for t in result.trades if t.entry_time.year == yr]
        if yr_trades:
            yr_pnl = sum(t.pnl_usd for t in yr_trades)
            yr_wins = sum(1 for t in yr_trades if t.pnl_usd > 0)
            print(f"    {yr}: {len(yr_trades)}t, {yr_wins}w, ${yr_pnl:+,.0f}")


if __name__ == "__main__":
    main()
