#!/usr/bin/env python3

import sys
sys.path.insert(0, ".")

from strategies.candle_v2_3 import CandleV2_3
from live.adaptive_sizer import AdaptiveSizer
from live.regime import RegimeDetector
import pandas as pd

print("Testing basic V2.5 components...")

# Test strategy
strat = CandleV2_3(min_score=2, use_trailing_stop=True)
print("Strategy created")

# Test adaptive sizer
sizer = AdaptiveSizer(enabled=True)
print("Adaptive sizer created")

# Test regime detector
regime = RegimeDetector(enabled=True)
print("Regime detector created")

# Load some data
df = pd.read_csv("data/BTC_USD_hourly.csv", index_col=0, parse_dates=True)
df.columns = [c.lower() for c in df.columns]
print(f"Data loaded: {len(df)} rows")

# Test signal generation
sig = strat.generate_signal(df, 500)
print(f"Signal generated: {sig}")

print("Test completed successfully!")
