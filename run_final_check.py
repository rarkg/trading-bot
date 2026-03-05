"""Final check: V3 strategy with Hyperliquid fees at various leverage levels."""

import sys
import pandas as pd
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.portfolio_v3 import PortfolioV3


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def run_leveraged(leverage, allocs, fee=0.045):
    assets = {
        "BTC": load_data("data/BTC_USD_daily.csv"),
        "ETH": load_data("data/ETH_USD_daily.csv"),
        "SOL": load_data("data/SOL_USD_daily.csv"),
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
        
        longs = [t for t in result.trades if t.direction == "LONG"]
        shorts = [t for t in result.trades if t.direction == "SHORT"]
        
        details.append(f"{asset}: ${capital}→${capital+lev_pnl:,.0f} "
                       f"({result.total_trades}t, {result.win_rate:.0f}%w, DD{lev_dd:.0f}%) "
                       f"[L:{len(longs)} S:{len(shorts)}]")
    
    years = 4.17
    months = years * 12
    total_return = total_pnl / 1000 * 100
    monthly = total_return / months
    annual = total_return / years
    hit = monthly >= 5.0
    
    return monthly, annual, worst_dd, total_trades, details, hit


def main():
    print("=" * 80)
    print("  FINAL VERIFICATION — PortfolioV3 + Hyperliquid fees (0.045%)")
    print("  Target: 5%/month")
    print("=" * 80)
    
    # Best allocation from earlier: ETH 30% / SOL 70%
    alloc = {"ETH": 300, "SOL": 700}
    
    for lev in [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        mo, ann, dd, trades, details, hit = run_leveraged(lev, alloc)
        marker = "🎯" if hit else "❌"
        safe = "SAFE" if dd < 30 else ("RISKY" if dd < 50 else "DANGEROUS")
        
        print(f"\n  {marker} {lev}x leverage | {mo:+.2f}%/mo | {ann:+.1f}%/yr | DD {dd:.0f}% [{safe}] | {trades} trades")
        for d in details:
            print(f"     {d}")
    
    # Out-of-sample for the sweet spot
    print(f"\n{'=' * 80}")
    print(f"  OUT-OF-SAMPLE VALIDATION (train 60% / test 40%)")
    print(f"{'=' * 80}")
    
    assets_data = {
        "ETH": load_data("data/ETH_USD_daily.csv"),
        "SOL": load_data("data/SOL_USD_daily.csv"),
    }
    
    for lev in [2.0, 2.5, 3.0]:
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
            
            print(f"    {asset_name}: test ${test_pnl:+,.0f} ({test_monthly:+.2f}%/mo) "
                  f"| {test_r.total_trades} trades, {test_r.win_rate:.0f}% win "
                  f"{'✅' if test_pnl > 0 else '❌'}")
        
        total_test_return = total_test_pnl / 1000 * 100
        test_monthly_total = total_test_return / (4.17 * 0.4 * 12)
        print(f"    PORTFOLIO OOS: ${total_test_pnl:+,.0f} ({test_monthly_total:+.2f}%/mo) "
              f"{'🎯' if test_monthly_total >= 5 else '❌'}")

    # Stress test: worst periods
    print(f"\n{'=' * 80}")
    print(f"  STRESS TEST — Specific bad periods")
    print(f"{'=' * 80}")
    
    sol_data = assets_data["SOL"]
    eth_data = assets_data["ETH"]
    
    periods = [
        ("2022 crash (Jan-Jun)", "2022-01-01", "2022-07-01"),
        ("FTX collapse (Oct-Dec 2022)", "2022-10-01", "2022-12-31"),
        ("2023 recovery (Jan-Jun)", "2023-01-01", "2023-07-01"),
        ("2023 sideways (Jul-Dec)", "2023-07-01", "2023-12-31"),
        ("2024 bull (Jan-Jun)", "2024-01-01", "2024-07-01"),
        ("2025 (Jan-Jun)", "2025-01-01", "2025-07-01"),
    ]
    
    lev = 2.5  # Middle ground
    for label, start, end in periods:
        total_period_pnl = 0
        for asset_name, data in assets_data.items():
            cap = alloc[asset_name]
            mask = (data.index >= start) & (data.index < end)
            period_data = data[mask]
            if len(period_data) < 30:
                continue
            engine = BacktestEngine(initial_capital=cap, fee_pct=0.045, max_risk_pct=5.0*lev)
            strategy = PortfolioV3()
            result = engine.run(period_data, strategy, asset_name)
            total_period_pnl += result.total_pnl_usd * lev
        
        period_return = total_period_pnl / 1000 * 100
        print(f"  {label}: ${total_period_pnl:+,.0f} ({period_return:+.1f}%) "
              f"{'✅' if total_period_pnl > 0 else '❌'}")


if __name__ == "__main__":
    main()
