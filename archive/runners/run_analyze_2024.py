"""
Analyze V10 2024 failure — understand what makes 2024 squeezes different.
"""

import sys
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine
from strategies.squeeze_v10 import SqueezeV10


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def main():
    sol = load_data("SOL")
    btc = load_data("BTC")
    years = (sol.index[-1] - sol.index[0]).days / 365.25

    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strat = SqueezeV10(fixed_leverage=3.0)
    result = engine.run(sol, strat, "V10 SOL 3x")

    # Get all 2024 trades
    trades_2024 = [t for t in result.trades if t.entry_time.year == 2024]
    trades_2023 = [t for t in result.trades if t.entry_time.year == 2023]
    trades_2025 = [t for t in result.trades if t.entry_time.year == 2025]

    print("="*70)
    print("V10 Trade Analysis: 2023 vs 2024 vs 2025")
    print("="*70)

    for label, trades in [("2023", trades_2023), ("2024", trades_2024), ("2025", trades_2025)]:
        longs = [t for t in trades if t.direction == "LONG"]
        shorts = [t for t in trades if t.direction == "SHORT"]
        wins = [t for t in trades if t.pnl_usd > 0]

        print(f"\n{label}: {len(trades)} total, {len(wins)} wins ({len(wins)/len(trades)*100:.0f}%)")
        print(f"  Longs: {len(longs)}, PnL=${sum(t.pnl_usd for t in longs):+,.0f}, "
              f"WR={sum(1 for t in longs if t.pnl_usd>0)/max(len(longs),1)*100:.0f}%")
        print(f"  Shorts: {len(shorts)}, PnL=${sum(t.pnl_usd for t in shorts):+,.0f}, "
              f"WR={sum(1 for t in shorts if t.pnl_usd>0)/max(len(shorts),1)*100:.0f}%")

        # Exit reason breakdown
        from collections import Counter
        exit_reasons = Counter(t.exit_reason for t in trades)
        print(f"  Exit reasons: {dict(exit_reasons)}")

    # Deep dive on 2024 longs vs 2023 longs
    print("\n" + "="*70)
    print("2024 LONGS vs 2023 LONGS comparison:")

    for label, trades in [("2023 longs", [t for t in trades_2023 if t.direction=="LONG"]),
                           ("2024 longs", [t for t in trades_2024 if t.direction=="LONG"])]:
        if not trades:
            continue
        durations = []
        for t in trades:
            if t.exit_time:
                d = (t.exit_time - t.entry_time).total_seconds() / 3600
                durations.append(d)
        wins = [t for t in trades if t.pnl_usd > 0]
        losses = [t for t in trades if t.pnl_usd <= 0]
        print(f"\n  {label}: {len(trades)} trades")
        print(f"    Win rate: {len(wins)/len(trades)*100:.0f}%")
        print(f"    Avg win: {sum(t.pnl_pct for t in wins)/max(len(wins),1):.2f}%")
        print(f"    Avg loss: {sum(t.pnl_pct for t in losses)/max(len(losses),1):.2f}%")
        print(f"    Avg duration: {sum(durations)/len(durations):.1f}h" if durations else "")
        print(f"    Total PnL: ${sum(t.pnl_usd for t in trades):+,.0f}")

    # What was SOL doing in 2024 vs 2023?
    print("\n" + "="*70)
    print("SOL price context:")
    for yr in [2022, 2023, 2024, 2025]:
        yr_data = sol[sol.index.year == yr]["close"]
        if len(yr_data) > 0:
            start_p = yr_data.iloc[0]
            end_p = yr_data.iloc[-1]
            max_p = yr_data.max()
            min_p = yr_data.min()
            print(f"  {yr}: Start=${start_p:.0f} End=${end_p:.0f} "
                  f"Max=${max_p:.0f} Min=${min_p:.0f} "
                  f"Change={((end_p-start_p)/start_p*100):+.0f}%")

    # Monthly breakdown of 2024
    print("\n2024 monthly P&L:")
    for mo in range(1, 13):
        mo_trades = [t for t in trades_2024 if t.entry_time.month == mo]
        if mo_trades:
            mo_pnl = sum(t.pnl_usd for t in mo_trades)
            mo_wins = sum(1 for t in mo_trades if t.pnl_usd > 0)
            print(f"  2024-{mo:02d}: {len(mo_trades)}t {mo_wins}w ${mo_pnl:+,.0f}")

    # Score distribution: 2024 winners vs losers
    print("\n2024 Score distribution:")
    from collections import Counter
    win_scores = []
    loss_scores = []
    for t in trades_2024:
        if "(s" in t.signal:
            try:
                s = int(t.signal.split("(s")[1].rstrip(")"))
                if t.pnl_usd > 0:
                    win_scores.append(s)
                else:
                    loss_scores.append(s)
            except:
                pass
    if win_scores:
        print(f"  Winners avg score: {sum(win_scores)/len(win_scores):.0f}")
    if loss_scores:
        print(f"  Losers avg score: {sum(loss_scores)/len(loss_scores):.0f}")

    # Check: what's the RSI like at entry for 2024 vs 2023
    # Precompute RSI on full data
    closes = sol["close"].astype(float)
    delta = closes.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss_ser = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss_ser.replace(0, 1e-10)
    rsi = 100 - (100 / (1 + rs))

    # BTC 24h return at entry times
    btc_closes = btc["close"].astype(float)
    btc_roc24 = btc_closes.pct_change(24) * 100

    print("\n2024 losing longs entry context (RSI and BTC ROC at entry):")
    losing_longs_2024 = [t for t in trades_2024 if t.direction == "LONG" and t.pnl_usd < 0]
    rsi_vals = []
    btc_vals = []
    for t in losing_longs_2024[:15]:  # First 15
        try:
            idx = sol.index.get_loc(t.entry_time)
            rsi_v = float(rsi.iloc[idx])
            btc_idx = btc.index.get_indexer([t.entry_time], method="nearest")[0]
            btc_v = float(btc_roc24.iloc[btc_idx]) if btc_idx >= 0 else 0
            rsi_vals.append(rsi_v)
            btc_vals.append(btc_v)
            print(f"  {t.entry_time.date()} RSI:{rsi_v:.0f} BTC24h:{btc_v:+.1f}% "
                  f"PnL:{t.pnl_pct:+.1f}% exit:{t.exit_reason}")
        except:
            pass


if __name__ == "__main__":
    main()
