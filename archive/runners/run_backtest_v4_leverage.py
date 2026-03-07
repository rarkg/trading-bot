"""V4 — Test with leverage + more aggressive parameters to hit 5%/mo."""

import sys
import pandas as pd
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.portfolio_v3 import PortfolioV3


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def main():
    assets = {
        "SOL": load_data("data/SOL_USD_daily.csv"),
        "ETH": load_data("data/ETH_USD_daily.csv"),
        "BTC": load_data("data/BTC_USD_daily.csv"),
    }
    
    print("=" * 75)
    print("  V4 — LEVERAGE TEST (target: 5%/month = 60%/yr)")
    print("=" * 75)
    
    # Test different leverage levels
    for leverage in [1, 2, 3, 5]:
        print(f"\n{'#' * 75}")
        print(f"  LEVERAGE: {leverage}x")
        print(f"{'#' * 75}")
        
        total_pnl = 0
        allocs = {"BTC": 0, "ETH": 300, "SOL": 700}
        
        for asset, data in assets.items():
            capital = allocs[asset]
            if capital == 0:
                continue
            
            # Leverage simulated via larger max_risk_pct and position sizing
            engine = BacktestEngine(
                initial_capital=capital, 
                fee_pct=0.10,
                max_risk_pct=5.0 * leverage,  # More risk per trade
            )
            strategy = PortfolioV3()
            result = engine.run(data, strategy, f"{asset} {leverage}x")
            
            # Scale P&L by leverage (simplified)
            leveraged_pnl = result.total_pnl_usd * leverage
            leveraged_dd = result.max_drawdown_pct * leverage
            
            years = (data.index[-1] - data.index[0]).days / 365.25
            months = years * 12
            
            leveraged_return = leveraged_pnl / capital * 100
            monthly = leveraged_return / months if months > 0 else 0
            annual = leveraged_return / years if years > 0 else 0
            
            print(f"  {asset}: ${capital} → ${capital + leveraged_pnl:,.2f} "
                  f"({leveraged_return:+.1f}%) | {monthly:+.2f}%/mo | {annual:+.1f}%/yr "
                  f"| DD {leveraged_dd:.1f}% | {result.total_trades} trades")
            
            total_pnl += leveraged_pnl
        
        total_return = total_pnl / 1000 * 100
        years = 4.17
        months = years * 12
        monthly_total = total_return / months
        annual_total = total_return / years
        
        print(f"\n  PORTFOLIO: $1,000 → ${1000 + total_pnl:,.2f}")
        print(f"  Return: {total_return:+.1f}% total | {monthly_total:+.2f}%/mo | {annual_total:+.1f}%/yr")
        if monthly_total >= 5:
            print(f"  🎯 MEETS 5%/MONTH TARGET")
        else:
            print(f"  ⚠️  Need {5 - monthly_total:.2f}%/mo more")
    
    # Also test: what if we long-only (no shorts)?
    print(f"\n{'#' * 75}")
    print(f"  LONG-ONLY TEST (Kraken US spot = no shorting)")
    print(f"{'#' * 75}")
    
    # Count short vs long trades
    for asset_name in ["ETH", "SOL"]:
        data = assets[asset_name]
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.10, max_risk_pct=5.0)
        strategy = PortfolioV3()
        result = engine.run(data, strategy, f"{asset_name}")
        
        longs = [t for t in result.trades if t.direction == "LONG"]
        shorts = [t for t in result.trades if t.direction == "SHORT"]
        long_pnl = sum(t.pnl_usd for t in longs)
        short_pnl = sum(t.pnl_usd for t in shorts)
        
        print(f"  {asset_name}: {len(longs)} longs (${long_pnl:+,.2f}) | "
              f"{len(shorts)} shorts (${short_pnl:+,.2f})")
        if short_pnl < 0:
            print(f"    → Shorts LOSE money. Long-only would be BETTER.")
        else:
            print(f"    → Shorts profitable. Would lose ${short_pnl:,.2f} going long-only.")


if __name__ == "__main__":
    main()
