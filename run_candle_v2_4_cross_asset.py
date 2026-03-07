"""
Candle V2.4 Cross-Asset Backtest — Trailing stop, score sizing, correlation guard, partial TP.

Tests each V2.4 feature individually vs V2.3 baseline, then all combined.
Uses multi-asset backtest for correlation guard (needs cross-asset position tracking).
"""

import sys
import time
import os
import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Callable

sys.path.insert(0, ".")

from backtest.engine import Trade, BacktestResult
from strategies.candle_v2_3 import CandleV2_3


ASSETS = ["BTC", "ETH", "SOL", "LINK", "ADA", "AVAX", "DOGE", "XRP", "ICP", "SHIB"]

CAPITAL = 400.0
FEE_PCT = 0.15
MAX_RISK_PCT = 2.0

# V2.3 baseline: score=2, R:R 2:3, all 15 indicators, MTF both, cooldown=12, time_exit=144
BASE_PARAMS = dict(
    use_rsi=True, use_stoch_rsi=True, use_williams_r=True, use_macd=True,
    use_cci=True, use_ema_alignment=True, use_adx=True, use_bb=True,
    use_atr_percentile=True, use_keltner=True, use_volume=True, use_mfi=True,
    use_obv_slope=True, use_range_position=True, use_hh_ll=True,
    use_mtf=True, mtf_require="both",
    min_score=2.0, stop_atr=2.0, target_atr=3.0,
    cooldown=12, time_exit_bars=144, base_leverage=2.0,
)


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def run_multi_asset(datasets, strategy_factory, max_same_direction=None):
    # type: (Dict[str, pd.DataFrame], Callable[[], CandleV2_3], Optional[int]) -> Dict[str, dict]
    """Run synchronized multi-asset backtest with optional correlation guard."""

    fee_rate = FEE_PCT / 100
    max_risk = MAX_RISK_PCT / 100
    assets = list(datasets.keys())

    strategies = {a: strategy_factory() for a in assets}
    capitals = {a: CAPITAL for a in assets}
    open_trades = {}  # type: Dict[str, Trade]
    all_trades = {a: [] for a in assets}  # type: Dict[str, List[Trade]]
    peak_caps = {a: CAPITAL for a in assets}
    max_dds = {a: 0.0 for a in assets}

    # Union of all timestamps
    all_times = sorted(set().union(*(set(df.index) for df in datasets.values())))

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
            cap = capitals[asset]

            # === Check exits ===
            if asset in open_trades:
                trade = open_trades[asset]
                exit_reason = None
                exit_price = None

                if trade.direction == "LONG":
                    if low_val <= trade.stop_price:
                        exit_reason = "STOP"
                        exit_price = trade.stop_price
                    elif high_val >= trade.target_price:
                        exit_reason = "TARGET"
                        exit_price = trade.target_price
                else:
                    if high_val >= trade.stop_price:
                        exit_reason = "STOP"
                        exit_price = trade.stop_price
                    elif low_val <= trade.target_price:
                        exit_reason = "TARGET"
                        exit_price = trade.target_price

                if not exit_reason:
                    sig = strat.check_exit(df, idx, trade)
                    if isinstance(sig, dict) and sig.get("action") == "PARTIAL_TP":
                        ps = sig["partial_size"]
                        pp = sig.get("partial_price", price)
                        if trade.direction == "LONG":
                            raw = (pp - trade.entry_price) / trade.entry_price
                        else:
                            raw = (trade.entry_price - pp) / trade.entry_price
                        capitals[asset] += ps * (raw - fee_rate * 2)
                    elif sig:
                        exit_reason = sig
                        exit_price = price

                if exit_reason:
                    trade.exit_time = ts
                    trade.exit_price = exit_price
                    trade.exit_reason = exit_reason
                    if trade.direction == "LONG":
                        raw = (exit_price - trade.entry_price) / trade.entry_price
                    else:
                        raw = (trade.entry_price - exit_price) / trade.entry_price
                    pnl_pct = raw - fee_rate * 2
                    trade.pnl_pct = round(pnl_pct * 100, 2)
                    trade.pnl_usd = round(trade.size_usd * pnl_pct, 2)
                    capitals[asset] += trade.pnl_usd
                    all_trades[asset].append(trade)
                    del open_trades[asset]

            # === Generate signals ===
            if asset not in open_trades:
                sig = strat.generate_signal(df, idx)
                if sig and sig.get("action") in ("LONG", "SHORT"):
                    direction = sig["action"]

                    # Correlation guard
                    if max_same_direction is not None:
                        same = sum(1 for ot in open_trades.values() if ot.direction == direction)
                        if same >= max_same_direction:
                            continue

                    stop = sig["stop"]
                    target = sig["target"]
                    leverage = sig.get("leverage", 1.0)
                    size_mult = sig.get("size_multiplier", 1.0)

                    risk_per_unit = abs(price - stop) if stop else price * 0.02
                    margin = min(cap * 0.4 * size_mult, cap)
                    size = margin * leverage
                    max_loss = cap * max_risk
                    if price > 0:
                        loss = risk_per_unit * (size / price)
                    else:
                        loss = max_loss
                    if loss > max_loss and risk_per_unit > 0:
                        size = max_loss / (risk_per_unit / price)
                    size = min(size, cap * leverage)

                    new_trade = Trade(
                        entry_time=ts,
                        entry_price=price,
                        direction=direction,
                        signal=sig.get("signal", ""),
                        stop_price=stop,
                        target_price=target,
                        size_usd=round(size, 2),
                        leverage=leverage,
                        atr_at_entry=sig.get("atr_at_entry"),
                    )
                    open_trades[asset] = new_trade

            # Track drawdown
            peak_caps[asset] = max(peak_caps[asset], capitals[asset])
            dd = (peak_caps[asset] - capitals[asset]) / peak_caps[asset] if peak_caps[asset] > 0 else 0
            max_dds[asset] = max(max_dds[asset], dd)

    # Close remaining open trades
    for asset in list(open_trades.keys()):
        trade = open_trades[asset]
        df = datasets[asset]
        last_price = float(df.iloc[-1]["close"])
        trade.exit_time = df.index[-1]
        trade.exit_price = last_price
        trade.exit_reason = "END_OF_DATA"
        if trade.direction == "LONG":
            raw = (last_price - trade.entry_price) / trade.entry_price
        else:
            raw = (trade.entry_price - last_price) / trade.entry_price
        pnl_pct = raw - fee_rate * 2
        trade.pnl_pct = round(pnl_pct * 100, 2)
        trade.pnl_usd = round(trade.size_usd * pnl_pct, 2)
        capitals[asset] += trade.pnl_usd
        all_trades[asset].append(trade)

    # Build results
    results = {}
    for asset in assets:
        trades = all_trades[asset]
        cap = capitals[asset]
        winners = [t for t in trades if t.pnl_usd > 0]
        losers = [t for t in trades if t.pnl_usd <= 0]
        n = len(trades)
        wr = (len(winners) / n * 100) if n > 0 else 0.0
        pnl = round(cap - CAPITAL, 2)
        dd = round(max_dds[asset] * 100, 2)
        gp = sum(t.pnl_usd for t in winners) if winners else 0
        gl = abs(sum(t.pnl_usd for t in losers)) if losers else 1
        pf = round(gp / gl, 2) if gl > 0 else 0.0

        days = (datasets[asset].index[-1] - datasets[asset].index[0]).days
        tday = round(n / days, 2) if days > 0 else 0.0

        results[asset] = {
            "trades": n,
            "wr": wr,
            "pnl": pnl,
            "dd": dd,
            "pf": pf,
            "tday": tday,
        }

    return results


def print_results_table(configs_results):
    """Print the comparison table."""
    # Header
    fmt = "  {:<25s} {:>6s} {:>6s} {:>10s} {:>6s} {:>5s} {:>5s}"
    print(fmt.format("Config", "Trades", "WR", "PnL", "MaxDD", "PF", "T/Day"))
    print("  " + "-" * 70)

    for label, results in configs_results:
        total_trades = sum(results[a]["trades"] for a in results)
        wrs = [results[a]["wr"] for a in results if results[a]["trades"] > 0]
        avg_wr = np.mean(wrs) if wrs else 0.0
        total_pnl = sum(results[a]["pnl"] for a in results)
        max_dd = max(results[a]["dd"] for a in results)
        pfs = [results[a]["pf"] for a in results if results[a]["trades"] > 0]
        avg_pf = np.mean(pfs) if pfs else 0.0
        tdays = [results[a]["tday"] for a in results if results[a]["trades"] > 0]
        avg_tday = np.mean(tdays) if tdays else 0.0

        print(f"  {label:<25s} {total_trades:>6d} {avg_wr:>5.1f}% ${total_pnl:>+8.0f} "
              f"{max_dd:>5.1f}% {avg_pf:>5.2f} {avg_tday:>5.2f}")


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    print("Loading data for %d assets..." % len(ASSETS))
    datasets = {}
    for sym in ASSETS:
        try:
            datasets[sym] = load_data(sym)
        except FileNotFoundError:
            print(f"  WARNING: {sym}_USD_hourly.csv not found, skipping")
    assets_loaded = list(datasets.keys())
    print(f"  Loaded: {', '.join(assets_loaded)}")

    sample = datasets[assets_loaded[0]]
    date_range = f"{sample.index[0].date()} to {sample.index[-1].date()}"

    print()
    print("=" * 80)
    print("  CANDLE V2.4 — UNIVERSAL IMPROVEMENTS BACKTEST")
    print(f"  {len(assets_loaded)} assets | {date_range}")
    print(f"  Capital: ${CAPITAL:.0f}/asset | Fee: {FEE_PCT}% | Max risk: {MAX_RISK_PCT}%")
    print(f"  Base: score>=2, R:R 2:3, all 15 ind, MTF both, cd=12, te=144")
    print("=" * 80)

    configs = []

    # 1. Baseline (V2.3)
    print("\n  Running baseline (v2.3)...")
    r_base = run_multi_asset(datasets, lambda: CandleV2_3(**BASE_PARAMS))
    configs.append(("baseline (v2.3)", r_base))

    # 2. + Trailing stop
    print("  Running + trailing_stop...")
    r_trail = run_multi_asset(datasets, lambda: CandleV2_3(
        **BASE_PARAMS, use_trailing_stop=True,
        trail_activation_atr=1.5, trail_distance_atr=1.0,
    ))
    configs.append(("+ trailing_stop", r_trail))

    # 3. + Score sizing
    print("  Running + score_sizing...")
    r_score = run_multi_asset(datasets, lambda: CandleV2_3(
        **BASE_PARAMS, use_score_sizing=True,
        score_size_tiers=[(2, 1.0), (4, 1.5), (6, 2.0)],
    ))
    configs.append(("+ score_sizing", r_score))

    # 4. + Correlation guard (uses multi-asset coordination)
    print("  Running + correlation_guard...")
    r_corr = run_multi_asset(
        datasets,
        lambda: CandleV2_3(**BASE_PARAMS, use_correlation_guard=True, max_same_direction=3),
        max_same_direction=3,
    )
    configs.append(("+ correlation_guard", r_corr))

    # 5. + Partial TP
    print("  Running + partial_tp...")
    r_partial = run_multi_asset(datasets, lambda: CandleV2_3(
        **BASE_PARAMS, use_partial_tp=True,
        partial_tp_atr=2.0, partial_tp_pct=0.5,
    ))
    configs.append(("+ partial_tp", r_partial))

    # 6. ALL combined
    print("  Running ALL combined...")
    r_all = run_multi_asset(
        datasets,
        lambda: CandleV2_3(
            **BASE_PARAMS,
            use_trailing_stop=True, trail_activation_atr=1.5, trail_distance_atr=1.0,
            use_score_sizing=True, score_size_tiers=[(2, 1.0), (4, 1.5), (6, 2.0)],
            use_correlation_guard=True, max_same_direction=3,
            use_partial_tp=True, partial_tp_atr=2.0, partial_tp_pct=0.5,
        ),
        max_same_direction=3,
    )
    configs.append(("ALL combined", r_all))

    # Print results
    print()
    print("=" * 80)
    print("  RESULTS")
    print("=" * 80)
    print()
    print_results_table(configs)

    # Per-asset detail for ALL combined
    print()
    print("  Per-asset detail (ALL combined):")
    for asset in assets_loaded:
        r = r_all[asset]
        print(f"    {asset:>4s}: {r['trades']:>4}t {r['wr']:>5.1f}%w ${r['pnl']:>+7.0f} "
              f"DD:{r['dd']:.1f}% PF:{r['pf']:.2f} T/D:{r['tday']:.2f}")

    elapsed = time.time() - t_start
    print(f"\n  Runtime: {elapsed:.1f}s")

    return configs


if __name__ == "__main__":
    main()
