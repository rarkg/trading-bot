"""V7 — SOL squeeze-only with dynamic leverage. The focused approach."""

import sys
import time
import pandas as pd
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.squeeze_only_v7 import SqueezeOnlyV7


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def main():
    sol = load_data("data/SOL_USD_hourly.csv")
    
    print("=" * 80)
    print("  V7 — SOL Squeeze Only + Dynamic Leverage")
    print(f"  {len(sol)} hourly candles | Full $1K on SOL | Hyperliquid 0.045%")
    print("  Only trades Bollinger squeeze breakouts (the proven signal)")
    print("  Target: 5%/month")
    print("=" * 80)
    
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strategy = SqueezeOnlyV7()
    
    t0 = time.time()
    result = engine.run(sol, strategy, "SOL V7")
    elapsed = time.time() - t0
    
    years = (sol.index[-1] - sol.index[0]).days / 365.25
    months = years * 12
    monthly = result.total_pnl_pct / months
    annual = result.total_pnl_pct / years
    
    print_result(result)
    print(f"  {elapsed:.1f}s | {monthly:+.2f}%/mo | {annual:+.1f}%/yr")
    print(f"  Trades/month: {result.total_trades / months:.1f}")
    
    # Per-year breakdown
    print(f"\n  Per-year breakdown:")
    for yr in [2022, 2023, 2024, 2025, 2026]:
        yr_trades = [t for t in result.trades 
                     if t.entry_time.year == yr]
        if yr_trades:
            yr_pnl = sum(t.pnl_usd for t in yr_trades)
            yr_wins = sum(1 for t in yr_trades if t.pnl_usd > 0)
            print(f"    {yr}: {len(yr_trades)} trades, {yr_wins} wins, ${yr_pnl:+,.2f}")
    
    # Signal breakdown
    longs = [t for t in result.trades if t.direction == "LONG"]
    shorts = [t for t in result.trades if t.direction == "SHORT"]
    print(f"\n  Longs: {len(longs)} (${sum(t.pnl_usd for t in longs):+,.2f})")
    print(f"  Shorts: {len(shorts)} (${sum(t.pnl_usd for t in shorts):+,.2f})")
    
    # Leverage distribution
    print(f"\n  Score distribution:")
    from collections import Counter
    scores = Counter()
    for t in result.trades:
        # Extract score from signal name
        if "(s" in t.signal:
            s = int(t.signal.split("(s")[1].rstrip(")"))
            bucket = f"{(s//10)*10}-{(s//10)*10+9}"
            scores[bucket] += 1
    for bucket in sorted(scores.keys()):
        print(f"    Score {bucket}: {scores[bucket]} trades")
    
    # Out-of-sample
    print(f"\n{'=' * 80}")
    print(f"  OUT-OF-SAMPLE (60/40)")
    engine2 = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strategy2 = SqueezeOnlyV7()
    train_r, test_r = engine2.run_split(sol, strategy2, "SOL")
    
    test_years = years * 0.4
    test_months = test_years * 12
    test_monthly = test_r.total_pnl_pct / test_months if test_months > 0 else 0
    
    print(f"  Train: {train_r.total_trades}t, {train_r.win_rate:.0f}%w, ${train_r.total_pnl_usd:+,.2f}")
    print(f"  Test:  {test_r.total_trades}t, {test_r.win_rate:.0f}%w, ${test_r.total_pnl_usd:+,.2f} ({test_monthly:+.2f}%/mo)")
    print(f"  {'🎯 OOS MEETS TARGET' if test_monthly >= 5 else f'❌ OOS: {test_monthly:.2f}%/mo'}")
    
    # Also test on ETH and BTC for comparison
    print(f"\n  Cross-asset validation:")
    for sym in ["ETH", "BTC"]:
        data = load_data(f"data/{sym}_USD_hourly.csv")
        engine3 = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat3 = SqueezeOnlyV7()
        r = engine3.run(data, strat3, sym)
        m = r.total_pnl_pct / months
        print(f"    {sym}: {r.total_trades}t, {r.win_rate:.0f}%w, ${r.total_pnl_usd:+,.2f} ({m:+.2f}%/mo)")


if __name__ == "__main__":
    main()
