"""Iterate until we find 5%/month. Test V4 aggressive strategy with leverage."""

import sys
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.aggressive_v4 import AggressiveV4


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def test_config(leverage, fee_pct, allocs, label):
    assets = {
        "BTC": load_data("data/BTC_USD_daily.csv"),
        "ETH": load_data("data/ETH_USD_daily.csv"),
        "SOL": load_data("data/SOL_USD_daily.csv"),
    }
    
    total_pnl = 0
    total_trades = 0
    worst_dd = 0
    details = []
    
    for asset, data in assets.items():
        capital = allocs.get(asset, 0)
        if capital == 0:
            continue
        
        engine = BacktestEngine(
            initial_capital=capital,
            fee_pct=fee_pct,
            max_risk_pct=5.0 * leverage,
        )
        strategy = AggressiveV4()
        result = engine.run(data, strategy, f"{asset}")
        
        # Apply leverage to P&L
        lev_pnl = result.total_pnl_usd * leverage
        lev_dd = result.max_drawdown_pct * leverage
        
        total_pnl += lev_pnl
        total_trades += result.total_trades
        worst_dd = max(worst_dd, lev_dd)
        
        longs = [t for t in result.trades if t.direction == "LONG"]
        shorts = [t for t in result.trades if t.direction == "SHORT"]
        long_pnl = sum(t.pnl_usd for t in longs) * leverage
        short_pnl = sum(t.pnl_usd for t in shorts) * leverage
        
        details.append({
            "asset": asset, "capital": capital,
            "trades": result.total_trades,
            "win_rate": result.win_rate,
            "pnl": lev_pnl,
            "dd": lev_dd,
            "long_pnl": long_pnl,
            "short_pnl": short_pnl,
            "longs": len(longs),
            "shorts": len(shorts),
        })
    
    years = 4.17
    months = years * 12
    total_return = total_pnl / 1000 * 100
    monthly = total_return / months
    annual = total_return / years
    
    hit = monthly >= 5.0
    marker = "🎯" if hit else "❌"
    
    print(f"\n  {marker} {label}")
    print(f"     Leverage: {leverage}x | Fees: {fee_pct}% | Trades: {total_trades}")
    for d in details:
        print(f"     {d['asset']}: ${d['capital']} → ${d['capital']+d['pnl']:,.0f} "
              f"({d['trades']} trades, {d['win_rate']:.0f}% win, DD {d['dd']:.0f}%) "
              f"[L:{d['longs']}=${d['long_pnl']:+,.0f} S:{d['shorts']}=${d['short_pnl']:+,.0f}]")
    print(f"     TOTAL: $1K → ${1000+total_pnl:,.0f} | {monthly:+.2f}%/mo | {annual:+.1f}%/yr | MaxDD {worst_dd:.0f}%")
    
    return monthly, annual, worst_dd, hit


def main():
    print("=" * 80)
    print("  ITERATION RUN — Finding 5%/month")
    print("  Strategy: AggressiveV4 (11 signal types, adaptive trailing stop)")
    print("  Exchange: Hyperliquid (0.045% fee)")
    print("=" * 80)
    
    configs = [
        # (leverage, fee%, allocations, label)
        (1, 0.045, {"ETH": 300, "SOL": 700}, "1x ETH30/SOL70 (baseline)"),
        (1, 0.045, {"ETH": 500, "SOL": 500}, "1x ETH50/SOL50"),
        (1, 0.045, {"BTC": 200, "ETH": 300, "SOL": 500}, "1x BTC20/ETH30/SOL50"),
        (2, 0.045, {"ETH": 300, "SOL": 700}, "2x ETH30/SOL70"),
        (2, 0.045, {"ETH": 500, "SOL": 500}, "2x ETH50/SOL50"),
        (2, 0.045, {"BTC": 200, "ETH": 300, "SOL": 500}, "2x BTC20/ETH30/SOL50"),
        (3, 0.045, {"ETH": 300, "SOL": 700}, "3x ETH30/SOL70"),
        (3, 0.045, {"ETH": 500, "SOL": 500}, "3x ETH50/SOL50"),
        (2, 0.045, {"SOL": 1000}, "2x SOL only"),
        (3, 0.045, {"SOL": 1000}, "3x SOL only"),
    ]
    
    winners = []
    
    for lev, fee, alloc, label in configs:
        monthly, annual, dd, hit = test_config(lev, fee, alloc, label)
        if hit:
            winners.append((label, monthly, annual, dd))
    
    print(f"\n{'=' * 80}")
    if winners:
        print("  ✅ CONFIGURATIONS THAT HIT 5%/MONTH:")
        print(f"  {'Config':<35} {'Monthly':<10} {'Annual':<10} {'MaxDD':<10}")
        print("  " + "-" * 65)
        for label, mo, ann, dd in sorted(winners, key=lambda x: -x[1]):
            safe = "⚠️" if dd > 50 else "✅"
            print(f"  {label:<35} {mo:+.2f}%/mo  {ann:+.1f}%/yr  {dd:.0f}% {safe}")
    else:
        print("  ❌ No configuration hit 5%/month. Need more work.")
    
    # Out-of-sample validation on best config
    if winners:
        print(f"\n  Running out-of-sample on best config...")
        best = winners[0]
        # Use the best leverage/allocation
        # Parse from label is messy, just hardcode top candidates
        for lev, fee, alloc, label in configs:
            if any(label == w[0] for w in winners[:3]):
                assets = {
                    "ETH": load_data("data/ETH_USD_daily.csv"),
                    "SOL": load_data("data/SOL_USD_daily.csv"),
                }
                for asset_name in ["ETH", "SOL"]:
                    if asset_name not in alloc:
                        continue
                    data = assets[asset_name]
                    cap = alloc[asset_name]
                    engine = BacktestEngine(initial_capital=cap, fee_pct=fee, max_risk_pct=5.0*lev)
                    strat = AggressiveV4()
                    train_r, test_r = engine.run_split(data, strat, f"{asset_name}")
                    test_years = 4.17 * 0.4
                    test_pnl = test_r.total_pnl_usd * lev
                    test_annual = (test_pnl / cap * 100) / test_years
                    print(f"    {label} | {asset_name} OOS: ${test_pnl:+,.0f} ({test_annual:+.1f}%/yr) "
                          f"{'✅' if test_pnl > 0 else '❌'}")
                break


if __name__ == "__main__":
    main()
