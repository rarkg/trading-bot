"""
Deep analysis of V7 trades — understand what distinguishes winners from losers.
This will guide V10 signal quality improvements.
"""

import sys
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine
from strategies.squeeze_only_v7 import SqueezeOnlyV7


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def analyze_trade_features(trades, data):
    """Extract features for each trade for winner/loser analysis."""
    features = []
    closes = data["close"].astype(float)
    highs = data["high"].astype(float)
    lows = data["low"].astype(float)
    volumes = data["volume"].astype(float)

    # Precompute some useful indicators
    atr = (highs - lows).rolling(14).mean()
    vol_avg = volumes.rolling(20).mean()
    sma20 = closes.rolling(20).mean()
    std20 = closes.rolling(20).std()
    bb_width = std20 / sma20 * 100
    rsi_delta = closes.diff()
    gain = rsi_delta.where(rsi_delta > 0, 0).rolling(14).mean()
    loss = (-rsi_delta.where(rsi_delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))

    for trade in trades:
        try:
            idx = data.index.get_loc(trade.entry_time)
        except (KeyError, Exception):
            continue

        if idx < 50 or idx >= len(data):
            continue

        price = trade.entry_price
        atri = float(atr.iloc[idx]) if not pd.isna(atr.iloc[idx]) else 1
        vol_ratio_val = float(volumes.iloc[idx] / vol_avg.iloc[idx]) if not pd.isna(vol_avg.iloc[idx]) else 1
        rsiv = float(rsi.iloc[idx]) if not pd.isna(rsi.iloc[idx]) else 50
        bbw = float(bb_width.iloc[idx]) if not pd.isna(bb_width.iloc[idx]) else 2

        # How many hours before we had a squeeze
        # Time of day
        hour_of_day = trade.entry_time.hour

        # Duration in hours
        if trade.exit_time:
            duration = (trade.exit_time - trade.entry_time).total_seconds() / 3600
        else:
            duration = 0

        # Extract score from signal name
        score = 0
        if "(s" in trade.signal:
            try:
                score = int(trade.signal.split("(s")[1].rstrip(")"))
            except Exception:
                pass

        features.append({
            "direction": trade.direction,
            "score": score,
            "pnl_pct": trade.pnl_pct,
            "pnl_usd": trade.pnl_usd,
            "winner": 1 if trade.pnl_usd > 0 else 0,
            "exit_reason": trade.exit_reason,
            "vol_ratio": vol_ratio_val,
            "rsi_entry": rsiv,
            "bb_width": bbw,
            "atr_pct": atri / price * 100,
            "hour": hour_of_day,
            "duration_h": duration,
            "year": trade.entry_time.year,
        })

    return pd.DataFrame(features)


def main():
    sol = load_data("SOL")
    years = (sol.index[-1] - sol.index[0]).days / 365.25

    # Run with fixed 5x leverage
    class Fixed5x(SqueezeOnlyV7):
        def generate_signal(self, data, i):
            sig = super().generate_signal(data, i)
            if sig:
                sig["leverage"] = 5.0
            return sig

    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strat = Fixed5x()
    result = engine.run(sol, strat, "SOL V7 5x")
    mo = result.total_pnl_pct / (years * 12)

    print(f"SOL V7 5x: {result.total_trades}t, {result.win_rate:.0f}%w, "
          f"{mo:+.2f}%/mo, DD:{result.max_drawdown_pct:.1f}%")

    # Analyze
    df = analyze_trade_features(result.trades, sol)
    print(f"\nTotal trades analyzed: {len(df)}")

    # ---- Score analysis ----
    print("\n--- Win Rate by Score Bucket ---")
    df["score_bucket"] = pd.cut(df["score"], bins=[0, 50, 60, 70, 80, 90, 100],
                                 labels=["<50", "50-60", "60-70", "70-80", "80-90", "90+"])
    score_analysis = df.groupby("score_bucket").agg(
        count=("winner", "count"),
        win_rate=("winner", "mean"),
        avg_pnl=("pnl_pct", "mean"),
    ).round(2)
    print(score_analysis.to_string())

    # ---- Direction analysis ----
    print("\n--- Winner/Loser by Direction ---")
    dir_analysis = df.groupby("direction").agg(
        count=("winner", "count"),
        win_rate=("winner", "mean"),
        avg_pnl=("pnl_pct", "mean"),
        total_pnl=("pnl_usd", "sum"),
    ).round(2)
    print(dir_analysis.to_string())

    # ---- Exit reason analysis ----
    print("\n--- Exit Reason Distribution ---")
    exit_analysis = df.groupby("exit_reason").agg(
        count=("winner", "count"),
        win_rate=("winner", "mean"),
        avg_pnl=("pnl_pct", "mean"),
        total_pnl=("pnl_usd", "sum"),
    ).round(2)
    print(exit_analysis.to_string())

    # ---- Vol ratio analysis ----
    print("\n--- Win Rate by Volume Ratio (entry) ---")
    df["vol_bucket"] = pd.cut(df["vol_ratio"],
                               bins=[0, 1.3, 1.8, 2.5, 4.0, 100],
                               labels=["1.0-1.3x", "1.3-1.8x", "1.8-2.5x", "2.5-4x", "4x+"])
    vol_analysis = df.groupby("vol_bucket").agg(
        count=("winner", "count"),
        win_rate=("winner", "mean"),
        avg_pnl=("pnl_pct", "mean"),
    ).round(2)
    print(vol_analysis.to_string())

    # ---- RSI at entry ----
    print("\n--- Win Rate by RSI at Entry ---")
    df["rsi_bucket"] = pd.cut(df["rsi_entry"],
                               bins=[0, 30, 40, 50, 60, 70, 100],
                               labels=["<30", "30-40", "40-50", "50-60", "60-70", ">70"])
    rsi_analysis = df.groupby("rsi_bucket").agg(
        count=("winner", "count"),
        win_rate=("winner", "mean"),
        avg_pnl=("pnl_pct", "mean"),
    ).round(2)
    print(rsi_analysis.to_string())

    # ---- ATR% at entry (volatility regime) ----
    print("\n--- Win Rate by ATR% at Entry ---")
    df["atr_bucket"] = pd.cut(df["atr_pct"],
                               bins=[0, 1, 2, 3, 5, 100],
                               labels=["<1%", "1-2%", "2-3%", "3-5%", ">5%"])
    atr_analysis = df.groupby("atr_bucket").agg(
        count=("winner", "count"),
        win_rate=("winner", "mean"),
        avg_pnl=("pnl_pct", "mean"),
    ).round(2)
    print(atr_analysis.to_string())

    # ---- Year analysis ----
    print("\n--- Annual Performance ---")
    yr_analysis = df.groupby("year").agg(
        count=("winner", "count"),
        win_rate=("winner", "mean"),
        avg_pnl=("pnl_pct", "mean"),
        total_pnl=("pnl_usd", "sum"),
    ).round(2)
    print(yr_analysis.to_string())

    # ---- Key insights ----
    print("\n--- KEY INSIGHTS ---")

    # Best score threshold
    for min_score in [50, 60, 70, 80]:
        subset = df[df["score"] >= min_score]
        if len(subset) > 0:
            print(f"  Score >= {min_score}: {len(subset)}t, "
                  f"{subset['winner'].mean()*100:.0f}%w, "
                  f"avg pnl={subset['pnl_pct'].mean():+.2f}%, "
                  f"total=${subset['pnl_usd'].sum():+,.0f}")

    # What if we only trade HIGH vol ratio
    for min_vol in [1.5, 2.0, 2.5]:
        subset = df[df["vol_ratio"] >= min_vol]
        if len(subset) > 0:
            print(f"  Vol >= {min_vol}x: {len(subset)}t, "
                  f"{subset['winner'].mean()*100:.0f}%w, "
                  f"avg pnl={subset['pnl_pct'].mean():+.2f}%, "
                  f"total=${subset['pnl_usd'].sum():+,.0f}")

    # Combination filters
    best_filter = df[(df["score"] >= 70) & (df["vol_ratio"] >= 1.5)]
    if len(best_filter) > 0:
        print(f"\n  Score>=70 AND Vol>=1.5x: {len(best_filter)}t, "
              f"{best_filter['winner'].mean()*100:.0f}%w, "
              f"avg pnl={best_filter['pnl_pct'].mean():+.2f}%, "
              f"total=${best_filter['pnl_usd'].sum():+,.0f}")

    # By ATR regime
    high_vol = df[df["atr_pct"] > 3]
    low_vol = df[df["atr_pct"] <= 3]
    print(f"\n  High vol (ATR>3%): {len(high_vol)}t, {high_vol['winner'].mean()*100:.0f}%w, "
          f"avg={high_vol['pnl_pct'].mean():+.2f}%, total=${high_vol['pnl_usd'].sum():+,.0f}")
    print(f"  Low vol (ATR<=3%): {len(low_vol)}t, {low_vol['winner'].mean()*100:.0f}%w, "
          f"avg={low_vol['pnl_pct'].mean():+.2f}%, total=${low_vol['pnl_usd'].sum():+,.0f}")

    return df


if __name__ == "__main__":
    df = main()
