"""Hourly V6 — Selective signals only, multi-timeframe, dynamic leverage."""

import sys
import time
import pandas as pd
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.selective_v6 import SelectiveV6


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def main():
    print("=" * 80)
    print("  HOURLY V6 — Selective (3 signals only) + Multi-Timeframe")
    print("  36,577 hourly candles | Hyperliquid 0.045% fees")
    print("  Dynamic leverage: 1.5x-3.5x based on confidence score")
    print("  Target: 5%/month")
    print("=" * 80)
    
    alloc = {"ETH": 300, "SOL": 700}
    assets = {
        "ETH": load_data("data/ETH_USD_hourly.csv"),
        "SOL": load_data("data/SOL_USD_hourly.csv"),
    }
    
    total_pnl = 0
    
    for asset_name, data in assets.items():
        cap = alloc[asset_name]
        engine = BacktestEngine(initial_capital=cap, fee_pct=0.045, max_risk_pct=5.0)
        strategy = SelectiveV6()
        
        t0 = time.time()
        result = engine.run(data, strategy, asset_name)
        elapsed = time.time() - t0
        
        years = (data.index[-1] - data.index[0]).days / 365.25
        months = years * 12
        monthly = result.total_pnl_pct / months if months > 0 else 0
        annual = result.total_pnl_pct / years if years > 0 else 0
        
        print_result(result)
        print(f"  {elapsed:.1f}s | {monthly:+.2f}%/mo | {annual:+.1f}%/yr")
        
        longs = [t for t in result.trades if t.direction == "LONG"]
        shorts = [t for t in result.trades if t.direction == "SHORT"]
        print(f"  Longs: {len(longs)} (${sum(t.pnl_usd for t in longs):+,.2f}) | "
              f"Shorts: {len(shorts)} (${sum(t.pnl_usd for t in shorts):+,.2f})")
        
        from collections import Counter
        sigs = Counter()
        sig_pnl = {}
        for t in result.trades:
            s = t.signal.split("(")[0]
            sigs[s] += 1
            sig_pnl[s] = sig_pnl.get(s, 0) + t.pnl_usd
        for s, c in sigs.most_common():
            print(f"    {s}: {c} trades, ${sig_pnl[s]:+,.2f}")
        print()
        
        total_pnl += result.total_pnl_usd
    
    years = 4.17
    months = years * 12
    total_return = total_pnl / 1000 * 100
    monthly_total = total_return / months
    
    print(f"{'=' * 80}")
    print(f"  PORTFOLIO: $1,000 → ${1000+total_pnl:,.2f} ({monthly_total:+.2f}%/mo | {total_return/years:+.1f}%/yr)")
    print(f"  {'🎯 MEETS TARGET' if monthly_total >= 5 else f'⚠️ {5-monthly_total:.2f}%/mo short'}")
    
    # Out-of-sample
    print(f"\n  OUT-OF-SAMPLE:")
    total_oos = 0
    for asset_name, data in assets.items():
        cap = alloc[asset_name]
        engine = BacktestEngine(initial_capital=cap, fee_pct=0.045, max_risk_pct=5.0)
        strategy = SelectiveV6()
        _, test_r = engine.run_split(data, strategy, asset_name)
        test_mo = (test_r.total_pnl_usd / cap * 100) / (4.17 * 0.4 * 12) if test_r.total_trades > 0 else 0
        total_oos += test_r.total_pnl_usd
        print(f"    {asset_name}: ${test_r.total_pnl_usd:+,.2f} ({test_mo:+.2f}%/mo) "
              f"| {test_r.total_trades}t {test_r.win_rate:.0f}%w {'✅' if test_r.total_pnl_usd > 0 else '❌'}")
    
    oos_mo = (total_oos / 1000 * 100) / (4.17 * 0.4 * 12)
    print(f"    TOTAL OOS: ${total_oos:+,.2f} ({oos_mo:+.2f}%/mo) {'🎯' if oos_mo >= 5 else '❌'}")


if __name__ == "__main__":
    main()
