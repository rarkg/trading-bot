#!/usr/bin/env python3
"""
Compound Multi-Asset Backtest — 10% equity per open position.

Each open position = 10% of current total portfolio equity.
All 10 assets share a single equity pool. Compounding enabled.

Usage:
    python3 run_compound_backtest.py
    STRATEGY_VERSION=v2.5 python3 run_compound_backtest.py
    STRATEGY_VERSION=v2.4 python3 run_compound_backtest.py
"""

import os
import sys
import time
import numpy as np
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest.engine import Trade

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
STRATEGY_VERSION = os.environ.get("STRATEGY_VERSION", "v2.5").lower()
ASSETS = ["BTC", "ETH", "SOL", "LINK", "ADA", "AVAX", "DOGE", "XRP", "ICP", "SHIB"]
INITIAL_CAPITAL = 5_000.0              # total portfolio starting equity
SLOT_INITIAL = INITIAL_CAPITAL / 10   # $500 per slot at start
SLOT_MAX_MULT = 10                     # compound up to 10x initial slot ($5k max)
SLOT_CAP = SLOT_INITIAL * SLOT_MAX_MULT  # hard cap per slot = $5,000
FEE_PCT = 0.05 / 100                   # 0.05% taker per side
SLIPPAGE_PCT = 0.10 / 100             # 0.10% slippage on market orders (entry + stop exits)
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ---------------------------------------------------------------------------
# Strategy params
# ---------------------------------------------------------------------------
V24_PARAMS = dict(
    min_score=2,
    stop_atr=2.0,
    target_atr=3.0,
    use_trailing_stop=True,
    trail_activation_atr=1.5,
    trail_distance_atr=0.5,
    adx_max=40,
    pattern_set="all",
    use_rsi=True, use_adx=True, use_bb=True, use_volume=True,
    base_leverage=2.0, cooldown=12, time_exit_bars=144,
)

V25_PARAMS = dict(
    min_score=1,
    stop_atr=2.0,
    target_atr=4.0,
    use_trailing_stop=True,
    trail_activation_atr=1.5,
    trail_distance_atr=0.3,
    adx_max=50,
    pattern_set="top5",
    use_mtf=True, mtf_require="both",
    use_rsi=True, use_stoch_rsi=True, use_williams_r=True,
    use_macd=True, use_cci=True, use_ema_alignment=True,
    use_adx=True, use_bb=True, use_atr_percentile=True,
    use_keltner=True, use_volume=True, use_mfi=True,
    use_obv_slope=True, use_range_position=True, use_hh_ll=True,
    base_leverage=2.0, cooldown=12, time_exit_bars=144,
)


def _get_strategy_class_and_params():
    ver = STRATEGY_VERSION.replace("v", "").replace(".", "_")
    if ver == "2_5":
        from strategies.candle_v2_5 import CandleV2_5
        return CandleV2_5, V25_PARAMS, "V2.5"
    else:
        from strategies.candle_v2_4 import CandleV2_4
        return CandleV2_4, V24_PARAMS, "V2.4"


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_data(asset):
    path = os.path.join(DATA_DIR, f"{asset}_USD_hourly.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df.columns = [c.lower().strip() for c in df.columns]
    for col in ["timestamp", "date", "datetime", "time"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col])
            df = df.set_index(col).sort_index()
            break
    df = df[["open", "high", "low", "close", "volume"]].dropna()
    return df


# ---------------------------------------------------------------------------
# Precompute indicators per asset (parallel)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Main synchronized compound backtest loop
# ---------------------------------------------------------------------------
def run_compound_backtest(datasets, strategy_class, params):
    # Instantiate one strategy per asset (strategies are stateful — track cooldowns, trailing stops)
    strategies = {asset: strategy_class(**params) for asset in datasets}

    # Build union timestamp index
    all_times = sorted(set().union(*(set(df.index) for df in datasets.values())))

    # Portfolio state
    equity = INITIAL_CAPITAL
    peak_equity = equity
    max_dd = 0.0
    equity_curve = [equity]

    # Per-asset state
    open_trades: Dict[str, Trade] = {}
    all_trades: List[Trade] = []

    assets = list(datasets.keys())

    for ts in all_times:
        for asset in assets:
            df = datasets[asset]
            if ts not in df.index:
                continue
            idx = df.index.get_loc(ts)
            if isinstance(idx, slice):
                idx = idx.start
            if idx < 200:
                continue

            row = df.iloc[idx]
            price = float(row["close"])
            high_val = float(row["high"])
            low_val = float(row["low"])
            strat = strategies[asset]

            # === Exit check ===
            if asset in open_trades:
                trade = open_trades[asset]
                exit_reason = None
                exit_price = None

                if trade.direction == "LONG":
                    if low_val <= trade.stop_price:
                        exit_reason = "STOP"
                        # Market stop: fill BELOW stop (slippage hurts)
                        exit_price = trade.stop_price * (1 - SLIPPAGE_PCT)
                    elif high_val >= trade.target_price:
                        exit_reason = "TARGET"
                        # Limit order: no slippage
                        exit_price = trade.target_price
                else:
                    if high_val >= trade.stop_price:
                        exit_reason = "STOP"
                        # Market stop SHORT: fill ABOVE stop (slippage hurts)
                        exit_price = trade.stop_price * (1 + SLIPPAGE_PCT)
                    elif low_val <= trade.target_price:
                        exit_reason = "TARGET"
                        exit_price = trade.target_price

                if not exit_reason:
                    sig = strat.check_exit(df, idx, trade)
                    if sig and not isinstance(sig, dict):
                        exit_reason = sig
                        # Trailing stop / time exit: market order with slippage
                        if trade.direction == "LONG":
                            exit_price = price * (1 - SLIPPAGE_PCT)
                        else:
                            exit_price = price * (1 + SLIPPAGE_PCT)

                if exit_reason:
                    trade.exit_time = ts
                    trade.exit_price = exit_price
                    trade.exit_reason = exit_reason
                    if trade.direction == "LONG":
                        raw = (exit_price - trade.entry_price) / trade.entry_price
                    else:
                        raw = (trade.entry_price - exit_price) / trade.entry_price
                    pnl_pct = raw - FEE_PCT * 2
                    trade.pnl_pct = round(pnl_pct * 100, 2)
                    trade.pnl_usd = round(trade.size_usd * pnl_pct, 2)
                    equity += trade.pnl_usd
                    all_trades.append(trade)
                    del open_trades[asset]

            # === Entry check ===
            if asset not in open_trades:
                sig = strat.generate_signal(df, idx)
                if sig and sig.get("action") in ("LONG", "SHORT"):
                    direction = sig["action"]
                    stop = sig["stop"]
                    target = sig["target"]
                    leverage = sig.get("leverage", 1.0)

                    # Compound sizing: equity / n_assets, capped at SLOT_CAP (10x initial)
                    position_size = min(equity / len(assets), SLOT_CAP)

                    trade = Trade(
                        entry_time=ts,
                        # Entry slippage: market order fills slightly worse
                        entry_price=price * (1 + SLIPPAGE_PCT) if direction == "LONG" else price * (1 - SLIPPAGE_PCT),
                        direction=direction,
                        signal=sig.get("signal", ""),
                        stop_price=stop,
                        target_price=target,
                        size_usd=round(position_size, 2),
                        leverage=leverage,
                        atr_at_entry=sig.get("atr_at_entry"),
                    )
                    open_trades[asset] = trade

        # Track portfolio drawdown
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        max_dd = max(max_dd, dd)
        equity_curve.append(equity)

    # Close remaining open trades at last price
    for asset, trade in open_trades.items():
        df = datasets[asset]
        last_price = float(df.iloc[-1]["close"])
        trade.exit_time = df.index[-1]
        trade.exit_price = last_price
        trade.exit_reason = "END_OF_DATA"
        if trade.direction == "LONG":
            raw = (last_price - trade.entry_price) / trade.entry_price
        else:
            raw = (trade.entry_price - last_price) / trade.entry_price
        pnl_pct = raw - FEE_PCT * 2
        trade.pnl_usd = round(trade.size_usd * pnl_pct, 2)
        equity += trade.pnl_usd
        all_trades.append(trade)

    return all_trades, equity, max_dd, equity_curve


# ---------------------------------------------------------------------------
# Print results
# ---------------------------------------------------------------------------
def print_results(all_trades, final_equity, max_dd, start_time):
    n = len(all_trades)
    winners = [t for t in all_trades if t.pnl_usd > 0]
    losers  = [t for t in all_trades if t.pnl_usd <= 0]
    wr = len(winners) / n * 100 if n > 0 else 0
    total_pnl = final_equity - INITIAL_CAPITAL
    pnl_pct = total_pnl / INITIAL_CAPITAL * 100
    gp = sum(t.pnl_usd for t in winners)
    gl = abs(sum(t.pnl_usd for t in losers)) or 1
    pf = gp / gl

    pnls = [t.pnl_pct for t in all_trades]
    sharpe = (np.mean(pnls) / np.std(pnls) * np.sqrt(365)) if len(pnls) > 1 and np.std(pnls) > 0 else 0

    # Per-asset breakdown
    per_asset = {}
    for t in all_trades:
        a = t.signal.split("|")[0] if "|" in t.signal else "?"
    # Use entry_time grouping — just count by signal isn't reliable
    # Show overall summary only (no per-asset since equity is shared)

    print()
    print("=" * 70)
    print(f"  {STRATEGY_VERSION.upper()} COMPOUND BACKTEST — 10% equity per position")
    print(f"  Starting capital: ${INITIAL_CAPITAL:,.0f} | 10 assets sharing equity pool")
    print("=" * 70)
    print(f"  Final equity:   ${final_equity:>12,.2f}")
    print(f"  Total PnL:      ${total_pnl:>+12,.2f}  ({pnl_pct:+.1f}%)")
    print(f"  Win rate:       {wr:.1f}%  ({len(winners)}/{n} trades)")
    print(f"  Max drawdown:   {max_dd*100:.1f}%")
    print(f"  Profit factor:  {pf:.2f}")
    print(f"  Sharpe ratio:   {sharpe:.2f}")
    print(f"  Total trades:   {n:,}")
    print(f"  Runtime:        {time.time()-start_time:.1f}s")
    print("=" * 70)

    # Exit reason breakdown
    reasons = {}
    for t in all_trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    print("  Exit reasons:")
    for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"    {r:<20s} {c:>5,}")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    t_start = time.time()
    strategy_class, params, label = _get_strategy_class_and_params()

    print(f"\n  Loading data for {len(ASSETS)} assets...")
    datasets = {}
    for sym in ASSETS:
        df = load_data(sym)
        if df is not None:
            datasets[sym] = df
            print(f"  ✓ {sym}: {len(df):,} candles  ({df.index[0].date()} → {df.index[-1].date()})")
        else:
            print(f"  ✗ {sym}: not found")

    if not datasets:
        print("  No data found. Exiting.")
        return

    print(f"\n  Strategy: {label}")
    print(f"  Position size: equity / n_assets, capped at ${SLOT_CAP:,.0f}/slot (10x initial)")
    print(f"  Slippage: {SLIPPAGE_PCT*100:.2f}% on entries + stop exits | Targets: limit order (no slippage)")
    print(f"  Leverage: {params.get('base_leverage', 1.0)}x")
    print()

    all_trades, final_equity, max_dd, equity_curve = run_compound_backtest(
        datasets, strategy_class, params
    )

    print_results(all_trades, final_equity, max_dd, t_start)


if __name__ == "__main__":
    main()
