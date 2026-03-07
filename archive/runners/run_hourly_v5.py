"""Hourly backtest with SmartSizingV5 — dynamic leverage based on confidence."""

import sys
import time
import pandas as pd
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.smart_sizing_v5 import SmartSizingV5


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def main():
    print("=" * 80)
    print("  HOURLY V5 — Smart Sizing (dynamic leverage per signal)")
    print("  36,577 hourly candles | Hyperliquid 0.045% fees")
    print("  Target: 5%/month on out-of-sample")
    print("=" * 80)
    
    alloc = {"ETH": 300, "SOL": 700}
    assets = {
        "ETH": load_data("data/ETH_USD_hourly.csv"),
        "SOL": load_data("data/SOL_USD_hourly.csv"),
    }
    
    # Full period test
    total_pnl = 0
    total_trades = 0
    
    for asset_name, data in assets.items():
        cap = alloc[asset_name]
        engine = BacktestEngine(initial_capital=cap, fee_pct=0.045, max_risk_pct=3.0)
        strategy = SmartSizingV5()
        
        t0 = time.time()
        result = engine.run(data, strategy, f"{asset_name}")
        elapsed = time.time() - t0
        
        years = (data.index[-1] - data.index[0]).days / 365.25
        months = years * 12
        annual = result.total_pnl_pct / years if years > 0 else 0
        monthly = result.total_pnl_pct / months if months > 0 else 0
        
        print_result(result)
        print(f"  Computed in {elapsed:.1f}s | {annual:+.1f}%/yr | {monthly:+.2f}%/mo")
        
        # Signal breakdown
        longs = [t for t in result.trades if t.direction == "LONG"]
        shorts = [t for t in result.trades if t.direction == "SHORT"]
        long_pnl = sum(t.pnl_usd for t in longs)
        short_pnl = sum(t.pnl_usd for t in shorts)
        print(f"  Longs: {len(longs)} (${long_pnl:+,.2f}) | Shorts: {len(shorts)} (${short_pnl:+,.2f})")
        
        # Signal type breakdown
        from collections import Counter
        sig_types = Counter()
        sig_pnl = {}
        for t in result.trades:
            # Extract signal name before (s
            sig_name = t.signal.split("(")[0] if "(" in t.signal else t.signal
            sig_types[sig_name] += 1
            sig_pnl[sig_name] = sig_pnl.get(sig_name, 0) + t.pnl_usd
        
        print(f"  Signal breakdown:")
        for sig, count in sig_types.most_common():
            pnl = sig_pnl.get(sig, 0)
            print(f"    {sig}: {count} trades, ${pnl:+,.2f}")
        print()
        
        total_pnl += result.total_pnl_usd
        total_trades += result.total_trades
    
    # Portfolio summary
    years = 4.17
    months = years * 12
    total_return = total_pnl / 1000 * 100
    monthly_total = total_return / months
    annual_total = total_return / years
    
    print(f"{'=' * 80}")
    print(f"  PORTFOLIO: $1,000 → ${1000 + total_pnl:,.2f}")
    print(f"  Return: {total_return:+.1f}% | {monthly_total:+.2f}%/mo | {annual_total:+.1f}%/yr")
    print(f"  Trades: {total_trades}")
    if monthly_total >= 5:
        print(f"  🎯 MEETS 5%/MONTH TARGET")
    else:
        print(f"  ⚠️  {5 - monthly_total:.2f}%/mo short")
    
    # Out-of-sample
    print(f"\n{'=' * 80}")
    print(f"  OUT-OF-SAMPLE (60/40 split)")
    print(f"{'=' * 80}")
    
    total_oos_pnl = 0
    for asset_name, data in assets.items():
        cap = alloc[asset_name]
        engine = BacktestEngine(initial_capital=cap, fee_pct=0.045, max_risk_pct=3.0)
        strategy = SmartSizingV5()
        train_r, test_r = engine.run_split(data, strategy, asset_name)
        
        test_years = 4.17 * 0.4
        test_months = test_years * 12
        test_monthly = (test_r.total_pnl_usd / cap * 100) / test_months if test_months > 0 else 0
        total_oos_pnl += test_r.total_pnl_usd
        
        print(f"  {asset_name} OOS: ${test_r.total_pnl_usd:+,.2f} ({test_monthly:+.2f}%/mo) "
              f"| {test_r.total_trades} trades, {test_r.win_rate:.0f}% win "
              f"{'✅' if test_r.total_pnl_usd > 0 else '❌'}")
    
    oos_return = total_oos_pnl / 1000 * 100
    oos_monthly = oos_return / (4.17 * 0.4 * 12)
    print(f"\n  PORTFOLIO OOS: ${total_oos_pnl:+,.2f} ({oos_monthly:+.2f}%/mo) "
          f"{'🎯' if oos_monthly >= 5 else '❌'}")


if __name__ == "__main__":
    main()
