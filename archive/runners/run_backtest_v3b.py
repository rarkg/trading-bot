"""V3b — Optimize allocation based on V3 results."""

import sys
import pandas as pd
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, print_result
from strategies.portfolio_v3 import PortfolioV3


def load_data(filepath):
    df = pd.read_csv(filepath, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def test_allocation(allocs, label):
    assets = {
        "BTC": load_data("data/BTC_USD_daily.csv"),
        "ETH": load_data("data/ETH_USD_daily.csv"),
        "SOL": load_data("data/SOL_USD_daily.csv"),
    }
    
    total_pnl = 0
    details = []
    
    for asset, data in assets.items():
        capital = allocs[asset]
        if capital == 0:
            continue
        engine = BacktestEngine(initial_capital=capital, fee_pct=0.10, max_risk_pct=5.0)
        strategy = PortfolioV3()
        result = engine.run(data, strategy, f"{asset}")
        total_pnl += result.total_pnl_usd
        details.append((asset, capital, result))
    
    total_return = total_pnl / 1000 * 100
    annual = total_return / 4.17
    
    print(f"\n  {label}: ", end="")
    for asset, capital, r in details:
        print(f"{asset}(${capital})={r.total_pnl_pct:+.0f}% ", end="")
    print(f"| TOTAL: {total_return:+.1f}% ({annual:+.1f}%/yr) {'🎯' if annual >= 20 else '❌'}")
    
    return annual


print("=" * 75)
print("  ALLOCATION OPTIMIZATION")
print("  Finding the mix that hits 20%/yr")
print("=" * 75)

# Test different allocations
configs = [
    ({"BTC": 400, "ETH": 300, "SOL": 300}, "40/30/30 (baseline)"),
    ({"BTC": 200, "ETH": 300, "SOL": 500}, "20/30/50 (SOL heavy)"),
    ({"BTC": 200, "ETH": 400, "SOL": 400}, "20/40/40 (alt heavy)"),
    ({"BTC": 100, "ETH": 400, "SOL": 500}, "10/40/50 (max alt)"),
    ({"BTC": 0, "ETH": 400, "SOL": 600}, "0/40/60 (no BTC)"),
    ({"BTC": 0, "ETH": 500, "SOL": 500}, "0/50/50 (alts only)"),
    ({"BTC": 0, "ETH": 300, "SOL": 700}, "0/30/70 (SOL max)"),
    ({"BTC": 0, "ETH": 0, "SOL": 1000}, "0/0/100 (SOL only)"),
    ({"BTC": 333, "ETH": 333, "SOL": 334}, "Equal weight"),
    ({"BTC": 500, "ETH": 250, "SOL": 250}, "BTC heavy"),
]

best = None
best_annual = -999

for alloc, label in configs:
    annual = test_allocation(alloc, label)
    if annual > best_annual:
        best_annual = annual
        best = label

print(f"\n  BEST: {best} at {best_annual:+.1f}%/yr")
