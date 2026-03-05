"""Run backtests on all strategies against BTC and ETH data."""

import sys
import pandas as pd
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.momentum import MomentumStrategy
from strategies.dca_timing import DCATiming
from strategies.mean_reversion import MeanReversion


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    # Normalize column names
    df.columns = [c.lower() for c in df.columns]
    return df


def main():
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.10)
    
    strategies = [
        ("Momentum/Breakout", MomentumStrategy()),
        ("AI-Timed DCA", DCATiming()),
        ("Mean Reversion", MeanReversion()),
    ]
    
    datasets = {
        "BTC Daily (4yr)": "data/BTC_USD_daily.csv",
        "ETH Daily (4yr)": "data/ETH_USD_daily.csv",
    }
    
    print("=" * 70)
    print("  CRYPTO TRADING BOT — BACKTEST RESULTS")
    print("  Capital: $1,000 | Fees: 0.10% per trade (Kraken)")
    print("=" * 70)
    
    all_results = []
    
    for data_name, filepath in datasets.items():
        print(f"\n{'#' * 70}")
        print(f"  DATASET: {data_name}")
        print(f"{'#' * 70}")
        
        data = load_data(filepath)
        
        for strat_name, strategy in strategies:
            # Full period test
            result = engine.run(data, strategy, f"{strat_name} | {data_name}")
            print_result(result)
            all_results.append(result)
            
            # Train/test split (60/40)
            train_r, test_r = engine.run_split(data, strategy, f"{strat_name} | {data_name}")
            print(f"  Split results:")
            print(f"    TRAIN: {train_r.total_trades} trades, {train_r.win_rate:.0f}% win, ${train_r.total_pnl_usd:+,.2f}")
            print(f"    TEST:  {test_r.total_trades} trades, {test_r.win_rate:.0f}% win, ${test_r.total_pnl_usd:+,.2f}")
            
            if test_r.total_pnl_usd > 0:
                print(f"    ✅ Out-of-sample PROFITABLE")
            else:
                print(f"    ❌ Out-of-sample NOT profitable")
            print()
            
            # Random period stress test
            random_results = engine.run_random_periods(data, strategy, strat_name, 
                                                       num_periods=10, period_days=30)
            profitable_periods = sum(1 for r in random_results if r.total_pnl_usd > 0)
            avg_pnl = sum(r.total_pnl_usd for r in random_results) / len(random_results)
            print(f"  Random 30-day periods (10 samples):")
            print(f"    Profitable: {profitable_periods}/10 ({profitable_periods*10}%)")
            print(f"    Avg P&L: ${avg_pnl:+,.2f}")
            print()
    
    # Summary table
    print("\n" + "=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"{'Strategy':<35} {'Trades':<8} {'Win%':<8} {'P&L':<12} {'MaxDD':<8} {'Sharpe':<8}")
    print("-" * 70)
    for r in all_results:
        print(f"{r.strategy_name:<35} {r.total_trades:<8} {r.win_rate:<8.1f} ${r.total_pnl_usd:<11,.2f} {r.max_drawdown_pct:<8.1f} {r.sharpe_ratio:<8.2f}")


if __name__ == "__main__":
    main()
