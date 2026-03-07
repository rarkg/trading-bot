"""Run V3 strategy on hourly data — should get way more trades and compounding."""

import sys
import pandas as pd
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.portfolio_v3 import PortfolioV3


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def test_hourly(leverage, allocs, fee=0.045):
    assets = {
        "ETH": load_data("data/ETH_USD_hourly.csv"),
        "SOL": load_data("data/SOL_USD_hourly.csv"),
    }
    
    total_pnl = 0
    details = []
    worst_dd = 0
    total_trades = 0
    
    for asset, data in assets.items():
        capital = allocs.get(asset, 0)
        if capital == 0:
            continue
        
        engine = BacktestEngine(initial_capital=capital, fee_pct=fee, max_risk_pct=5.0 * leverage)
        strategy = PortfolioV3()
        result = engine.run(data, strategy, asset)
        
        lev_pnl = result.total_pnl_usd * leverage
        lev_dd = result.max_drawdown_pct * leverage
        total_pnl += lev_pnl
        total_trades += result.total_trades
        worst_dd = max(worst_dd, lev_dd)
        
        years = (data.index[-1] - data.index[0]).days / 365.25
        
        longs = [t for t in result.trades if t.direction == "LONG"]
        shorts = [t for t in result.trades if t.direction == "SHORT"]
        long_pnl = sum(t.pnl_usd for t in longs) * leverage
        short_pnl = sum(t.pnl_usd for t in shorts) * leverage
        
        details.append(f"{asset}: ${capital}→${capital+lev_pnl:,.0f} "
                       f"({result.total_trades}t, {result.win_rate:.0f}%w, DD{lev_dd:.0f}%) "
                       f"[L:{len(longs)}=${long_pnl:+,.0f} S:{len(shorts)}=${short_pnl:+,.0f}]")
    
    years = 4.17
    months = years * 12
    total_return = total_pnl / 1000 * 100
    monthly = total_return / months
    annual = total_return / years
    hit = monthly >= 5.0
    
    return monthly, annual, worst_dd, total_trades, details, hit


def main():
    print("=" * 80)
    print("  HOURLY DATA BACKTEST — 36,577 candles per asset (4 years)")
    print("  Strategy: PortfolioV3 | Exchange: Hyperliquid (0.045%)")
    print("  Target: 5%/month")
    print("=" * 80)
    
    alloc = {"ETH": 300, "SOL": 700}
    
    for lev in [1.0, 1.5, 2.0, 2.5, 3.0]:
        mo, ann, dd, trades, details, hit = test_hourly(lev, alloc)
        marker = "🎯" if hit else "❌"
        safe = "SAFE" if dd < 30 else ("RISKY" if dd < 50 else "DANGEROUS")
        
        print(f"\n  {marker} {lev}x | {mo:+.2f}%/mo | {ann:+.1f}%/yr | DD {dd:.0f}% [{safe}] | {trades} trades")
        for d in details:
            print(f"     {d}")
    
    # Out-of-sample for best configs
    print(f"\n{'=' * 80}")
    print(f"  OUT-OF-SAMPLE (60/40 split)")
    print(f"{'=' * 80}")
    
    assets_data = {
        "ETH": load_data("data/ETH_USD_hourly.csv"),
        "SOL": load_data("data/SOL_USD_hourly.csv"),
    }
    
    for lev in [1.5, 2.0, 2.5]:
        print(f"\n  {lev}x leverage:")
        total_test_pnl = 0
        for asset_name, data in assets_data.items():
            cap = alloc[asset_name]
            engine = BacktestEngine(initial_capital=cap, fee_pct=0.045, max_risk_pct=5.0*lev)
            strategy = PortfolioV3()
            train_r, test_r = engine.run_split(data, strategy, asset_name)
            
            test_pnl = test_r.total_pnl_usd * lev
            test_years = 4.17 * 0.4
            test_months = test_years * 12
            test_monthly = (test_pnl / cap * 100) / test_months
            total_test_pnl += test_pnl
            
            print(f"    {asset_name}: OOS ${test_pnl:+,.0f} ({test_monthly:+.2f}%/mo) "
                  f"| {test_r.total_trades} trades, {test_r.win_rate:.0f}% win "
                  f"{'✅' if test_pnl > 0 else '❌'}")
        
        total_test_return = total_test_pnl / 1000 * 100
        test_monthly_total = total_test_return / (4.17 * 0.4 * 12)
        print(f"    PORTFOLIO OOS: ${total_test_pnl:+,.0f} ({test_monthly_total:+.2f}%/mo) "
              f"{'🎯' if test_monthly_total >= 5 else '❌'}")


if __name__ == "__main__":
    main()
