"""Run V2 backtests — smarter strategies."""

import sys
import pandas as pd
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.momentum_v2 import MomentumV2
from strategies.dca_v2 import DCAv2
from strategies.regime_switcher import RegimeSwitcher


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def main():
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.10, max_risk_pct=3.0)
    
    strategies = [
        ("Momentum V2", MomentumV2()),
        ("DCA V2", DCAv2()),
        ("Regime Switcher (AI)", RegimeSwitcher()),
    ]
    
    datasets = {
        "BTC 4yr": "data/BTC_USD_daily.csv",
        "ETH 4yr": "data/ETH_USD_daily.csv",
        "SOL 4yr": "data/SOL_USD_daily.csv",
    }
    
    print("=" * 75)
    print("  CRYPTO BOT V2 — BACKTEST RESULTS")
    print("  Capital: $1,000 | Fees: 0.10% | Max Risk: 3%/trade")
    print("  Target: 20% annual minimum")
    print("=" * 75)
    
    all_results = []
    
    for data_name, filepath in datasets.items():
        print(f"\n{'#' * 75}")
        print(f"  {data_name}")
        print(f"{'#' * 75}")
        
        data = load_data(filepath)
        years = (data.index[-1] - data.index[0]).days / 365.25
        
        for strat_name, strategy in strategies:
            # Reset strategy state
            if hasattr(strategy, '_trailing_stop'):
                strategy._trailing_stop = None
                strategy._best_price = None
            if hasattr(strategy, 'momentum'):
                strategy.momentum._trailing_stop = None
                strategy.momentum._best_price = None
            
            result = engine.run(data, strategy, f"{strat_name} | {data_name}")
            annual_return = result.total_pnl_pct / years if years > 0 else 0
            
            print_result(result)
            print(f"  Annualized: {annual_return:+.1f}%/yr over {years:.1f} years")
            if annual_return >= 20:
                print(f"  🎯 MEETS 20% TARGET")
            else:
                print(f"  ⚠️  Below target ({20 - annual_return:.1f}% short)")
            
            # Out-of-sample
            if hasattr(strategy, '_trailing_stop'):
                strategy._trailing_stop = None
                strategy._best_price = None
            if hasattr(strategy, 'momentum'):
                strategy.momentum._trailing_stop = None
                strategy.momentum._best_price = None
            
            train_r, test_r = engine.run_split(data, strategy, f"{strat_name} | {data_name}")
            test_years = years * 0.4
            test_annual = test_r.total_pnl_pct / test_years if test_years > 0 else 0
            
            print(f"  Out-of-sample: {test_r.total_trades} trades, "
                  f"${test_r.total_pnl_usd:+,.2f} ({test_annual:+.1f}%/yr)")
            if test_r.total_pnl_usd > 0:
                print(f"  ✅ Passes out-of-sample")
            else:
                print(f"  ❌ Fails out-of-sample")
            
            # Stress test: random periods
            if hasattr(strategy, '_trailing_stop'):
                strategy._trailing_stop = None
                strategy._best_price = None
            if hasattr(strategy, 'momentum'):
                strategy.momentum._trailing_stop = None
                strategy.momentum._best_price = None
                
            randoms = engine.run_random_periods(data, strategy, strat_name, 
                                                 num_periods=15, period_days=60)
            profitable = sum(1 for r in randoms if r.total_pnl_usd > 0)
            avg_pnl = sum(r.total_pnl_usd for r in randoms) / len(randoms)
            print(f"  Stress test (15x 60-day random): {profitable}/15 profitable, "
                  f"avg ${avg_pnl:+,.2f}")
            print()
            
            all_results.append((result, annual_return))
    
    # Summary
    print("\n" + "=" * 75)
    print("  SUMMARY — Does it hit 20%/year?")
    print("=" * 75)
    print(f"{'Strategy':<30} {'Asset':<8} {'Trades':<7} {'Win%':<7} {'Total':<11} {'Annual':<10} {'MaxDD':<7} {'Target'}")
    print("-" * 95)
    for result, annual in all_results:
        parts = result.strategy_name.split(" | ")
        sname = parts[0] if len(parts) > 0 else result.strategy_name
        dname = parts[1] if len(parts) > 1 else ""
        target = "✅" if annual >= 20 else "❌"
        print(f"{sname:<30} {dname:<8} {result.total_trades:<7} {result.win_rate:<7.1f} "
              f"${result.total_pnl_usd:<10,.2f} {annual:<10.1f} {result.max_drawdown_pct:<7.1f} {target}")


if __name__ == "__main__":
    main()
