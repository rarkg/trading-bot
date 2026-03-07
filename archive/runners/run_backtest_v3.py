"""V3 backtest — portfolio approach + aggressive sizing."""

import sys
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.portfolio_v3 import PortfolioV3


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def run_portfolio_backtest():
    """Run strategy across BTC + ETH + SOL, combine P&L."""
    
    assets = {
        "BTC": load_data("data/BTC_USD_daily.csv"),
        "ETH": load_data("data/ETH_USD_daily.csv"),
        "SOL": load_data("data/SOL_USD_daily.csv"),
    }
    
    # Allocation: BTC 40%, ETH 30%, SOL 30%
    allocations = {"BTC": 400, "ETH": 300, "SOL": 300}
    
    print("=" * 75)
    print("  PORTFOLIO V3 — Multi-Asset Backtest")
    print("  Total Capital: $1,000 | BTC 40% | ETH 30% | SOL 30%")
    print("  Fees: 0.10% | Max Risk: 5%/trade | Trailing stops")
    print("=" * 75)
    
    total_pnl = 0
    all_results = []
    
    for asset, data in assets.items():
        capital = allocations[asset]
        engine = BacktestEngine(initial_capital=capital, fee_pct=0.10, max_risk_pct=5.0)
        strategy = PortfolioV3()
        
        result = engine.run(data, strategy, f"Portfolio V3 | {asset}")
        years = (data.index[-1] - data.index[0]).days / 365.25
        annual = result.total_pnl_pct / years if years > 0 else 0
        
        print_result(result)
        print(f"  Capital: ${capital} → ${capital + result.total_pnl_usd:,.2f}")
        print(f"  Annualized: {annual:+.1f}%/yr")
        
        # Out-of-sample
        strategy2 = PortfolioV3()
        train_r, test_r = engine.run_split(data, strategy2, f"V3 | {asset}")
        test_years = years * 0.4
        test_annual = test_r.total_pnl_pct / test_years if test_years > 0 else 0
        print(f"  Out-of-sample: ${test_r.total_pnl_usd:+,.2f} ({test_annual:+.1f}%/yr) "
              f"{'✅' if test_r.total_pnl_usd > 0 else '❌'}")
        
        # Stress test
        strategy3 = PortfolioV3()
        randoms = engine.run_random_periods(data, strategy3, f"V3|{asset}", 
                                             num_periods=20, period_days=60)
        profitable = sum(1 for r in randoms if r.total_pnl_usd > 0)
        print(f"  Stress (20x 60-day): {profitable}/20 profitable")
        print()
        
        total_pnl += result.total_pnl_usd
        all_results.append((asset, result, annual, capital))
    
    # Portfolio summary
    years = 4.17
    total_return_pct = total_pnl / 1000 * 100
    annual_return = total_return_pct / years
    
    print("=" * 75)
    print("  PORTFOLIO SUMMARY")
    print("=" * 75)
    for asset, result, annual, capital in all_results:
        final = capital + result.total_pnl_usd
        print(f"  {asset}: ${capital} → ${final:,.2f} ({result.total_pnl_pct:+.1f}%) "
              f"| {result.total_trades} trades | {result.win_rate:.0f}% win | DD {result.max_drawdown_pct:.1f}%")
    print(f"\n  TOTAL: $1,000 → ${1000 + total_pnl:,.2f}")
    print(f"  Return: {total_return_pct:+.1f}% total | {annual_return:+.1f}%/yr")
    print(f"  {'🎯 MEETS 20% TARGET' if annual_return >= 20 else f'⚠️  {20 - annual_return:.1f}% short of target'}")
    
    # Also run single-asset with full $1000 for comparison
    print(f"\n{'=' * 75}")
    print(f"  COMPARISON: Single-asset $1,000 each")
    print(f"{'=' * 75}")
    for asset, data in assets.items():
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.10, max_risk_pct=5.0)
        strategy = PortfolioV3()
        result = engine.run(data, strategy, f"V3 Full | {asset}")
        years = (data.index[-1] - data.index[0]).days / 365.25
        annual = result.total_pnl_pct / years
        print(f"  {asset}: ${result.total_pnl_usd:+,.2f} ({annual:+.1f}%/yr) "
              f"| {result.total_trades} trades | {result.win_rate:.0f}% win")
    
    # Buy and hold comparison
    print(f"\n{'=' * 75}")
    print(f"  COMPARISON: Buy & Hold (just buy and sit)")
    print(f"{'=' * 75}")
    for asset, data in assets.items():
        first = float(data["close"].iloc[0])
        last = float(data["close"].iloc[-1])
        bnh_return = (last / first - 1) * 100
        years = (data.index[-1] - data.index[0]).days / 365.25
        bnh_annual = bnh_return / years
        print(f"  {asset}: {bnh_return:+.1f}% total ({bnh_annual:+.1f}%/yr) "
              f"| ${first:,.0f} → ${last:,.0f}")


if __name__ == "__main__":
    run_portfolio_backtest()
