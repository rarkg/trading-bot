"""
Filter Sweep — Find the optimal combination that:
1. Keeps enough trades (>50/yr)
2. Fixes 2024 specifically
3. Maintains 5%/mo with ≤35% DD

Tests V10 with individual filters added one at a time.
"""

import sys
import time
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine
from strategies.squeeze_v10 import SqueezeV10
from strategies.squeeze_v11 import SqueezeV11


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def mo(result, years):
    return result.total_pnl_pct / (years * 12)


def per_year_pnl(result):
    year_pnl = {}
    for yr in range(2022, 2027):
        trades = [t for t in result.trades if t.entry_time.year == yr]
        year_pnl[yr] = (len(trades), sum(t.pnl_usd for t in trades))
    return year_pnl


def run_and_show(data, cls, kwargs, lev, years, name):
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    k = dict(kwargs)
    k["fixed_leverage"] = lev
    strat = cls(**k)
    result = engine.run(data, strat, name)
    monthly = mo(result, years)
    hit = monthly >= 5.0 and result.max_drawdown_pct <= 35.0
    s = "✅" if hit else ("🟡" if monthly >= 3 else "❌")
    year_pnl = per_year_pnl(result)

    yr_str = " | ".join(f"{yr}:${p[1]:+,.0f}" for yr, p in year_pnl.items() if p[0] > 0)

    print(f"  {s} {name:35s}: {result.total_trades:3d}t {result.win_rate:.0f}%w "
          f"({monthly:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}% PF:{result.profit_factor:.2f}")
    print(f"    Years: {yr_str}")
    return result, monthly


class V10_RushOnly(SqueezeV10):
    """V10 with only momentum rush guard added."""
    def __init__(self, rush_pct=18.0, **kwargs):
        super().__init__(**kwargs)
        self.rush_pct = rush_pct
        self._roc_168 = None

    def _precompute(self, data):
        super()._precompute(data)
        closes = data["close"].astype(float)
        self._ind["roc_168"] = closes.pct_change(168) * 100

    def generate_signal(self, data, i):
        sig = super().generate_signal(data, i)
        if sig is None:
            return None
        if i >= len(self._ind):
            return None
        roc = float(self._ind["roc_168"].iloc[i]) if not pd.isna(self._ind["roc_168"].iloc[i]) else 0
        if sig["action"] == "LONG" and roc > self.rush_pct:
            return None
        if sig["action"] == "SHORT" and roc < -self.rush_pct:
            return None
        return sig


class V10_RangeOnly(SqueezeV10):
    """V10 with only range position filter added."""
    def __init__(self, max_range_pct=0.75, min_range_pct=0.25, **kwargs):
        super().__init__(**kwargs)
        self.max_range_pct = max_range_pct
        self.min_range_pct = min_range_pct

    def _precompute(self, data):
        super()._precompute(data)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        closes = data["close"].astype(float)
        range_high = highs.rolling(50).max()
        range_low = lows.rolling(50).min()
        self._ind["range_pct"] = (closes - range_low) / (range_high - range_low).replace(0, 1)

    def generate_signal(self, data, i):
        sig = super().generate_signal(data, i)
        if sig is None:
            return None
        if i >= len(self._ind):
            return None
        rp = float(self._ind["range_pct"].iloc[i]) if not pd.isna(self._ind["range_pct"].iloc[i]) else 0.5
        if sig["action"] == "LONG" and rp > self.max_range_pct:
            return None
        if sig["action"] == "SHORT" and rp < self.min_range_pct:
            return None
        return sig


class V10_ATROnly(SqueezeV10):
    """V10 with tighter ATR filter."""
    def __init__(self, max_atr=2.5, **kwargs):
        super().__init__(**kwargs)
        self.max_atr_custom = max_atr

    def generate_signal(self, data, i):
        if self._ind is None:
            self._precompute(data)
        if i >= len(self._ind):
            return None
        atr_pct = float(self._ind["atr_pct"].iloc[i])
        if atr_pct > self.max_atr_custom:
            return None
        return super().generate_signal(data, i)


class V10_Combined(SqueezeV10):
    """V10 + Rush + Range filters combined (no duration requirement)."""
    def __init__(self, rush_pct=18.0, max_range=0.75, min_range=0.25, **kwargs):
        super().__init__(**kwargs)
        self.rush_pct = rush_pct
        self.max_range = max_range
        self.min_range = min_range

    def _precompute(self, data):
        super()._precompute(data)
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        self._ind["roc_168"] = closes.pct_change(168) * 100
        range_high = highs.rolling(50).max()
        range_low = lows.rolling(50).min()
        self._ind["range_pct"] = (closes - range_low) / (range_high - range_low).replace(0, 1)

    def generate_signal(self, data, i):
        sig = super().generate_signal(data, i)
        if sig is None:
            return None
        if i >= len(self._ind):
            return None
        roc = float(self._ind["roc_168"].iloc[i]) if not pd.isna(self._ind["roc_168"].iloc[i]) else 0
        rp = float(self._ind["range_pct"].iloc[i]) if not pd.isna(self._ind["range_pct"].iloc[i]) else 0.5

        if sig["action"] == "LONG" and roc > self.rush_pct:
            return None
        if sig["action"] == "SHORT" and roc < -self.rush_pct:
            return None
        if sig["action"] == "LONG" and rp > self.max_range:
            return None
        if sig["action"] == "SHORT" and rp < self.min_range:
            return None
        return sig


def main():
    t_start = time.time()
    sol = load_data("SOL")
    years = (sol.index[-1] - sol.index[0]).days / 365.25

    print("=" * 90)
    print("  Filter Sweep — Finding optimal V10 filter combination")
    print("=" * 90)

    print("\n[1] Individual filter impact (all at 3x leverage)")
    print("  (comparing effect of each filter alone vs V10 baseline)")
    print()

    configs = [
        ("V10 baseline",         SqueezeV10, {}),
        ("V10 + rush18",         V10_RushOnly, {"rush_pct": 18.0}),
        ("V10 + rush25",         V10_RushOnly, {"rush_pct": 25.0}),
        ("V10 + rush30",         V10_RushOnly, {"rush_pct": 30.0}),
        ("V10 + range75",        V10_RangeOnly, {"max_range_pct": 0.75}),
        ("V10 + range80",        V10_RangeOnly, {"max_range_pct": 0.80}),
        ("V10 + atr2.5",         V10_ATROnly, {"max_atr": 2.5}),
        ("V10 + atr2.0",         V10_ATROnly, {"max_atr": 2.0}),
        ("V10+rush18+range75",   V10_Combined, {"rush_pct": 18, "max_range": 0.75}),
        ("V10+rush25+range80",   V10_Combined, {"rush_pct": 25, "max_range": 0.80}),
        ("V10+rush18+range80",   V10_Combined, {"rush_pct": 18, "max_range": 0.80}),
    ]

    best_combo = None
    best_mo_val = -999

    for name, cls, kwargs in configs:
        result, monthly = run_and_show(sol, cls, kwargs, 3.0, years, name)
        if monthly >= 5.0 and result.max_drawdown_pct <= 35.0 and monthly > best_mo_val:
            best_mo_val = monthly
            best_combo = (name, cls, kwargs)

    # Also test at 5x
    print("\n[2] Best configs at 5x leverage")
    for name, cls, kwargs in configs:
        result, monthly = run_and_show(sol, cls, kwargs, 5.0, years, f"{name} 5x")

    print(f"\n  Best combo: {best_combo[0] if best_combo else 'None'}")
    print(f"\n  Runtime: {time.time()-t_start:.1f}s")


if __name__ == "__main__":
    main()
