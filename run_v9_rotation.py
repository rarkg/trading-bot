"""
V9 Multi-Asset Momentum Rotation + Volatility Targeting.
Tests new strategy paradigms that might break the 5%/mo barrier.
"""

import sys
import time
import os
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, BacktestResult, Trade
from strategies.squeeze_only_v7 import SqueezeOnlyV7
from strategies.voltarget_squeeze import VolTargetSqueeze
from strategies.momentum_rotation import MomentumRotation


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def monthly_pct(pnl_pct, years):
    return pnl_pct / (years * 12)


def run_rotation_backtest(asset_data, rotation_strat, name="Rotation",
                          initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0):
    """
    Run the multi-asset rotation strategy.
    At each bar, find the best signal across all assets and execute it.
    One position at a time.
    """
    fee = fee_pct / 100
    capital = initial_capital
    peak_capital = capital
    max_drawdown = 0
    trades = []
    equity_curve = [capital]
    open_trade = None
    open_asset = None
    last_exit_bar = -12
    entry_bar = -1
    trailing_stop = None
    best_price = None

    # Align all assets to common index
    common_index = None
    for df in asset_data.values():
        if common_index is None:
            common_index = df.index
        else:
            common_index = common_index.intersection(df.index)

    aligned = {sym: df.loc[common_index] for sym, df in asset_data.items()}
    n = len(common_index)

    for i in range(1400, n):
        # --- EXIT ---
        if open_trade and open_asset:
            data = aligned[open_asset]
            row = data.iloc[i]
            price = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])
            atr_col = rotation_strat._asset_ind[open_asset].iloc[i]["atr"]
            atr = float(atr_col) if not pd.isna(atr_col) else 0

            exit_reason = None
            exit_price = None

            if open_trade.direction == "LONG":
                if low <= open_trade.stop_price:
                    exit_reason = "STOP"
                    exit_price = open_trade.stop_price
                elif high >= open_trade.target_price:
                    exit_reason = "TARGET"
                    exit_price = open_trade.target_price
            elif open_trade.direction == "SHORT":
                if high >= open_trade.stop_price:
                    exit_reason = "STOP"
                    exit_price = open_trade.stop_price
                elif low <= open_trade.target_price:
                    exit_reason = "TARGET"
                    exit_price = open_trade.target_price

            # Time exit: cut if no progress in 12 bars
            bars_held = i - entry_bar
            if not exit_reason and bars_held == 12 and atr > 0:
                if open_trade.direction == "LONG":
                    progress = (price - open_trade.entry_price) / atr
                    if progress < 0.5:
                        exit_reason = "TIME_EXIT"
                        exit_price = price
                elif open_trade.direction == "SHORT":
                    progress = (open_trade.entry_price - price) / atr
                    if progress < 0.5:
                        exit_reason = "TIME_EXIT"
                        exit_price = price

            # Trailing stop
            if not exit_reason and atr > 0:
                if open_trade.direction == "LONG":
                    if price > (best_price or price):
                        best_price = price
                        pnl_r = (price - open_trade.entry_price) / atr
                        trail = max(1.0, 2.5 - pnl_r * 0.08)
                        new_trail = price - (atr * trail)
                        if new_trail > (trailing_stop or 0):
                            trailing_stop = new_trail
                            open_trade.stop_price = new_trail
                    if trailing_stop and price < trailing_stop:
                        exit_reason = "TRAIL"
                        exit_price = price

                elif open_trade.direction == "SHORT":
                    if price < (best_price or price):
                        best_price = price
                        pnl_r = (open_trade.entry_price - price) / atr
                        trail = max(1.0, 2.5 - pnl_r * 0.08)
                        new_trail = price + (atr * trail)
                        if new_trail < (trailing_stop or float('inf')):
                            trailing_stop = new_trail
                            open_trade.stop_price = new_trail
                    if trailing_stop and price > trailing_stop:
                        exit_reason = "TRAIL"
                        exit_price = price

            # Strategy momentum exit
            if not exit_reason:
                sig = rotation_strat.check_exit_for_asset(open_asset, i, open_trade)
                if sig:
                    exit_reason = sig
                    exit_price = price

            if exit_reason:
                open_trade.exit_time = common_index[i]
                open_trade.exit_price = exit_price
                open_trade.exit_reason = exit_reason

                if open_trade.direction == "LONG":
                    raw_pnl_pct = (exit_price - open_trade.entry_price) / open_trade.entry_price
                else:
                    raw_pnl_pct = (open_trade.entry_price - exit_price) / open_trade.entry_price

                pnl_pct = raw_pnl_pct - (fee * 2)
                pnl_usd = open_trade.size_usd * pnl_pct
                open_trade.pnl_pct = round(pnl_pct * 100, 2)
                open_trade.pnl_usd = round(pnl_usd, 2)

                capital += pnl_usd
                trades.append(open_trade)
                open_trade = None
                open_asset = None
                trailing_stop = None
                best_price = None
                last_exit_bar = i

        # --- ENTRY ---
        if not open_trade and i - last_exit_bar >= 8:
            signals = rotation_strat.get_signals(aligned, i)

            if signals:
                # Sort by score, take best
                signals.sort(key=lambda s: s["score"], reverse=True)
                best_sig = signals[0]
                sym = best_sig["symbol"]
                direction = best_sig["action"]
                price = float(aligned[sym].iloc[i]["close"])
                stop = best_sig["stop"]
                target = best_sig["target"]
                lev = best_sig["leverage"]

                margin = min(capital * 0.4, capital)
                size = margin * lev

                risk_per_unit = abs(price - stop) if stop else price * 0.02
                max_loss = capital * (max_risk_pct / 100)
                loss_per_unit = risk_per_unit * (size / price) if price > 0 else max_loss
                if loss_per_unit > max_loss and risk_per_unit > 0:
                    size = max_loss / (risk_per_unit / price)
                size = min(size, capital * lev)

                open_trade = Trade(
                    entry_time=common_index[i],
                    entry_price=price,
                    direction=direction,
                    signal=best_sig["signal"],
                    stop_price=stop,
                    target_price=target,
                    size_usd=round(size, 2),
                )
                open_asset = sym
                trailing_stop = stop
                best_price = price
                entry_bar = i

        # Equity tracking
        equity_curve.append(capital)
        peak_capital = max(peak_capital, capital)
        dd = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0
        max_drawdown = max(max_drawdown, dd)

    # Close remaining
    if open_trade and open_asset:
        data = aligned[open_asset]
        last_price = float(data.iloc[-1]["close"])
        open_trade.exit_time = common_index[-1]
        open_trade.exit_price = last_price
        open_trade.exit_reason = "END_OF_DATA"
        if open_trade.direction == "LONG":
            pnl_pct = (last_price - open_trade.entry_price) / open_trade.entry_price - fee * 2
        else:
            pnl_pct = (open_trade.entry_price - last_price) / open_trade.entry_price - fee * 2
        open_trade.pnl_pct = round(pnl_pct * 100, 2)
        open_trade.pnl_usd = round(open_trade.size_usd * pnl_pct, 2)
        capital += open_trade.pnl_usd
        trades.append(open_trade)

    result = BacktestResult(
        strategy_name=name,
        period=f"{common_index[0].date()} to {common_index[-1].date()}",
        total_trades=len(trades),
        trades=trades,
        equity_curve=equity_curve,
    )

    if trades:
        winners = [t for t in trades if t.pnl_usd > 0]
        losers = [t for t in trades if t.pnl_usd <= 0]
        result.wins = len(winners)
        result.losses = len(losers)
        result.win_rate = len(winners) / len(trades) * 100
        result.avg_win_pct = np.mean([t.pnl_pct for t in winners]) if winners else 0
        result.avg_loss_pct = np.mean([t.pnl_pct for t in losers]) if losers else 0
        result.total_pnl_usd = round(capital - initial_capital, 2)
        result.total_pnl_pct = round((capital - initial_capital) / initial_capital * 100, 2)
        result.max_drawdown_pct = round(max_drawdown * 100, 2)
        gross_profit = sum(t.pnl_usd for t in winners) if winners else 0
        gross_loss = abs(sum(t.pnl_usd for t in losers)) if losers else 1
        result.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

    return result


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    assets = {sym: load_data(sym) for sym in ["BTC", "ETH", "SOL"]}
    sol = assets["SOL"]
    years = (sol.index[-1] - sol.index[0]).days / 365.25

    print("=" * 90)
    print("  V9 Multi-Asset Momentum Rotation + Volatility Targeting")
    print(f"  Period: {sol.index[0].date()} to {sol.index[-1].date()} ({years:.1f} years)")
    print("=" * 90)

    # =========================================================
    # 1. Volatility-Targeted Squeeze sweep
    # =========================================================
    print("\n[1] Volatility-Targeted Squeeze on SOL (adaptive leverage)")
    print("  (Compares target daily vol levels — lower = smaller positions in crashes)")
    for target_vol in [1.0, 1.5, 2.0, 2.5, 3.0]:
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat = VolTargetSqueeze(target_daily_vol_pct=target_vol, max_leverage=8.0)
        result = engine.run(sol, strat, f"VTS SOL tv={target_vol}")
        mo = monthly_pct(result.total_pnl_pct, years)
        status = "✅" if mo >= 5 and result.max_drawdown_pct <= 35 else ("🟡" if mo >= 3 else "❌")
        print(f"  {status} tv={target_vol}%: {result.total_trades}t "
              f"{result.win_rate:.0f}%w ${result.total_pnl_usd:+,.0f} "
              f"({mo:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}% "
              f"PF:{result.profit_factor:.2f}")

    # =========================================================
    # 2. VolTarget on all 3 assets
    # =========================================================
    print("\n[2] Volatility-Targeted Squeeze on each asset (tv=2.0, max_lev=8x)")
    for sym in ["BTC", "ETH", "SOL"]:
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat = VolTargetSqueeze(target_daily_vol_pct=2.0, max_leverage=8.0)
        result = engine.run(assets[sym], strat, f"VTS {sym}")
        mo = monthly_pct(result.total_pnl_pct, years)
        status = "✅" if mo >= 5 and result.max_drawdown_pct <= 35 else ("🟡" if mo >= 3 else "❌")
        print(f"  {status} {sym}: {result.total_trades}t "
              f"{result.win_rate:.0f}%w ${result.total_pnl_usd:+,.0f} "
              f"({mo:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}% "
              f"PF:{result.profit_factor:.2f}")

    # =========================================================
    # 3. Momentum Rotation Strategy
    # =========================================================
    print("\n[3] Multi-Asset Momentum Rotation (all 3 assets, pick strongest)")
    for threshold in [0.2, 0.5, 1.0]:
        for fast, slow in [(24, 168), (48, 336)]:
            strat = MomentumRotation(fast_period=fast, slow_period=slow,
                                      score_threshold=threshold)
            strat.precompute_all({sym: df for sym, df in assets.items()})
            result = run_rotation_backtest(
                assets, strat, f"Rotation f={fast} s={slow} th={threshold}",
            )
            mo = monthly_pct(result.total_pnl_pct, years)
            status = "✅" if mo >= 5 and result.max_drawdown_pct <= 35 else ("🟡" if mo >= 3 else "❌")
            print(f"  {status} fast={fast}h slow={slow}h th={threshold}: "
                  f"{result.total_trades}t {result.win_rate:.0f}%w "
                  f"${result.total_pnl_usd:+,.0f} ({mo:+.2f}%/mo) "
                  f"DD:{result.max_drawdown_pct:.1f}% PF:{result.profit_factor:.2f}")

    # =========================================================
    # 4. Best configs per-year breakdown
    # =========================================================
    print("\n[4] Best Config Deep Dive — VTS SOL tv=2.5 max_lev=8x")
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strat = VolTargetSqueeze(target_daily_vol_pct=2.5, max_leverage=8.0)
    result = engine.run(sol, strat, "VTS SOL Best")
    mo = monthly_pct(result.total_pnl_pct, years)
    print(f"  Result: {mo:+.2f}%/mo, DD:{result.max_drawdown_pct:.1f}%")
    print(f"\n  Per-year:")
    for yr in [2022, 2023, 2024, 2025, 2026]:
        yr_trades = [t for t in result.trades if t.entry_time.year == yr]
        if yr_trades:
            yr_pnl = sum(t.pnl_usd for t in yr_trades)
            yr_wins = sum(1 for t in yr_trades if t.pnl_usd > 0)
            print(f"    {yr}: {len(yr_trades)}t {yr_wins}w ${yr_pnl:+,.0f}")

    # =========================================================
    # 5. Rotation deep dive (best config)
    # =========================================================
    print("\n[5] Rotation Deep Dive")
    strat = MomentumRotation(fast_period=24, slow_period=168, score_threshold=0.5)
    strat.precompute_all(assets)
    result = run_rotation_backtest(assets, strat, "Rotation Best")
    mo = monthly_pct(result.total_pnl_pct, years)
    print(f"  Result: {mo:+.2f}%/mo, DD:{result.max_drawdown_pct:.1f}%")
    print(f"\n  Per-year:")
    for yr in [2022, 2023, 2024, 2025, 2026]:
        yr_trades = [t for t in result.trades if t.entry_time.year == yr]
        if yr_trades:
            yr_pnl = sum(t.pnl_usd for t in yr_trades)
            yr_wins = sum(1 for t in yr_trades if t.pnl_usd > 0)
            print(f"    {yr}: {len(yr_trades)}t {yr_wins}w ${yr_pnl:+,.0f}")

    # Per-asset breakdown
    print(f"\n  By asset:")
    for sym in ["BTC", "ETH", "SOL"]:
        sym_trades = [t for t in result.trades if sym in t.signal]
        if sym_trades:
            sym_pnl = sum(t.pnl_usd for t in sym_trades)
            sym_wins = sum(1 for t in sym_trades if t.pnl_usd > 0)
            print(f"    {sym}: {len(sym_trades)}t {sym_wins}w ${sym_pnl:+,.0f}")

    elapsed = time.time() - t_start
    print(f"\n  Total: {elapsed:.1f}s")

    # Log
    log = f"""
## V9 Rotation + VolTarget Tests

### Key Results
- VTS SOL best: see console
- Rotation best: see console
- Runtime: {elapsed:.1f}s
"""
    with open("results/iteration_log.md", "a") as f:
        f.write(log)

    print("  Logged to results/iteration_log.md")


if __name__ == "__main__":
    main()
