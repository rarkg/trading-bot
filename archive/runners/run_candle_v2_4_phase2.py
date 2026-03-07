"""
Candle V2.4 Phase 2 — Push from 98.2% even higher. Find the absolute ceiling.
"""

import sys
import time
import os
import pandas as pd
import numpy as np

sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, BacktestResult
from strategies.candle_v2_4 import CandleV2_4


ASSETS = ["BTC", "ETH", "SOL", "LINK"]


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def get_long_short_wr(result):
    longs = [t for t in result.trades if t.direction == "LONG"]
    shorts = [t for t in result.trades if t.direction == "SHORT"]
    long_wins = sum(1 for t in longs if t.pnl_usd > 0)
    short_wins = sum(1 for t in shorts if t.pnl_usd > 0)
    long_wr = (long_wins / len(longs) * 100) if longs else 0
    short_wr = (short_wins / len(shorts) * 100) if shorts else 0
    return long_wr, short_wr, len(longs), len(shorts)


def run_config(datasets, label, **kwargs):
    results = {}
    for sym in ASSETS:
        data = datasets[sym]
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.10, max_risk_pct=2.0)
        strat = CandleV2_4(**kwargs)
        result = engine.run(data, strat, f"V2.4 {sym}")
        long_wr, short_wr, n_long, n_short = get_long_short_wr(result)
        results[sym] = {
            "trades": result.total_trades,
            "wr": result.win_rate,
            "pnl": result.total_pnl_usd,
            "dd": result.max_drawdown_pct,
            "pf": result.profit_factor,
            "long_wr": long_wr,
            "short_wr": short_wr,
            "n_long": n_long,
            "n_short": n_short,
            "result": result,
        }
    return results


def print_row(label, results, baseline_avg=None):
    wrs = [results[s]["wr"] for s in ASSETS]
    avg_wr = np.mean(wrs)
    trades = sum(results[s]["trades"] for s in ASSETS)
    pnl = sum(results[s]["pnl"] for s in ASSETS)

    total_longs = sum(results[s]["n_long"] for s in ASSETS)
    total_shorts = sum(results[s]["n_short"] for s in ASSETS)
    total_long_wins = sum(results[s]["long_wr"] * results[s]["n_long"] / 100 for s in ASSETS)
    total_short_wins = sum(results[s]["short_wr"] * results[s]["n_short"] / 100 for s in ASSETS)
    agg_long_wr = (total_long_wins / total_longs * 100) if total_longs > 0 else 0
    agg_short_wr = (total_short_wins / total_shorts * 100) if total_shorts > 0 else 0

    delta = f" ({avg_wr - baseline_avg:+.1f})" if baseline_avg is not None else ""

    print(f"  {label:<40s} {results['BTC']['wr']:>5.1f}% {results['ETH']['wr']:>5.1f}% "
          f"{results['SOL']['wr']:>5.1f}% {results['LINK']['wr']:>5.1f}% "
          f"{avg_wr:>5.1f}%{delta:>7s} {trades:>5} ${pnl:>+8.0f} "
          f"L:{agg_long_wr:>4.0f}% S:{agg_short_wr:>4.0f}%")

    return avg_wr


HEADER = (f"  {'Config':<40s} {'BTC':>6} {'ETH':>6} {'SOL':>6} {'LINK':>6} "
          f"{'Avg':>6} {'Delta':>7} {'Trds':>5} {'P&L':>9} {'Long/Short WR':>14}")
SEP = "  " + "-" * 115


def main():
    t_start = time.time()
    datasets = {sym: load_data(sym) for sym in ASSETS}

    print("=" * 125)
    print("  CANDLE V2.4 PHASE 2 — PUSH BEYOND 98.2%")
    print("=" * 125)

    # Current best from Phase 1
    best_base = dict(
        use_rsi=True, use_bb=True, use_volume=True, use_adx=True,
        use_stoch_rsi=True, use_williams_r=True, use_macd=True,
        use_cci=True, use_ema_alignment=True, use_atr_percentile=True,
        use_keltner=True, use_mfi=True, use_obv_slope=True,
        use_range_position=True, use_hh_ll=True,
        min_score=6.0, stop_atr=10.0, target_atr=1.0,
        cooldown=12, base_leverage=2.0, time_exit_bars=144, adx_max=40,
        use_mtf=True, mtf_require="both",
        pattern_set="all",
    )

    best_avg = 0
    best_label = ""
    best_cfg = {}

    def track(label, cfg, baseline_avg=None):
        nonlocal best_avg, best_label, best_cfg
        r = run_config(datasets, label, **cfg)
        avg = print_row(label, r, baseline_avg)
        if avg > best_avg:
            best_avg = avg
            best_label = label
            best_cfg = cfg.copy()
        return r, avg

    # Reproduce current best
    print(f"\n  BASELINE: Current Best (s>=6, R:R 10:1, all, ADX<40)")
    print(HEADER)
    print(SEP)
    r_base, base_avg = track("current best", best_base)

    # =====================================================
    # Even more extreme R:R
    # =====================================================
    print(f"\n  EXTREME R:R")
    print(HEADER)
    print(SEP)
    for stop, tgt in [(15.0, 1.0), (20.0, 1.0), (12.0, 0.8), (15.0, 0.8),
                       (10.0, 0.8), (10.0, 0.5), (15.0, 0.5), (20.0, 0.5)]:
        cfg = {**best_base, "stop_atr": stop, "target_atr": tgt}
        track(f"R:R {stop}:{tgt}", cfg, base_avg)

    # =====================================================
    # Higher score thresholds
    # =====================================================
    print(f"\n  HIGHER SCORE THRESHOLDS")
    print(HEADER)
    print(SEP)
    for ms in [6.5, 7.0, 7.5, 8.0]:
        cfg = {**best_base, "min_score": ms}
        track(f"s>={ms}", cfg, base_avg)

    # Score + extreme R:R
    for ms in [6.5, 7.0]:
        for stop, tgt in [(15.0, 1.0), (15.0, 0.5), (20.0, 0.5)]:
            cfg = {**best_base, "min_score": ms, "stop_atr": stop, "target_atr": tgt}
            track(f"s>={ms} + R:R {stop}:{tgt}", cfg, base_avg)

    # =====================================================
    # 4H primary + extreme R:R + high score
    # =====================================================
    print(f"\n  4H PRIMARY + EXTREME")
    print(HEADER)
    print(SEP)
    for ms in [5.0, 6.0, 6.5, 7.0]:
        for stop, tgt in [(10.0, 1.0), (15.0, 1.0), (10.0, 0.5), (15.0, 0.5)]:
            cfg = {**best_base, "primary_tf": "4h", "min_score": ms,
                   "stop_atr": stop, "target_atr": tgt}
            track(f"4H s>={ms} R:R {stop}:{tgt}", cfg, base_avg)

    # =====================================================
    # ADX removal (no filter at all)
    # =====================================================
    print(f"\n  ADX REMOVAL")
    print(HEADER)
    print(SEP)
    for adx in [50, 60, 80, 100]:
        cfg = {**best_base, "adx_max": adx}
        track(f"adx<{adx}", cfg, base_avg)

    # =====================================================
    # Best combos with 4H
    # =====================================================
    print(f"\n  4H + ADX COMBOS")
    print(HEADER)
    print(SEP)
    for adx in [30, 40, 50, 60]:
        cfg = {**best_base, "primary_tf": "4h", "adx_max": adx}
        track(f"4H + adx<{adx}", cfg, base_avg)

    for adx in [40, 50, 60]:
        cfg = {**best_base, "primary_tf": "4h", "adx_max": adx,
               "stop_atr": 15.0, "target_atr": 0.5}
        track(f"4H + adx<{adx} + R:R 15:0.5", cfg, base_avg)

    # =====================================================
    # Final
    # =====================================================
    print(f"\n{'='*125}")
    print(f"  ABSOLUTE BEST: {best_label} -> {best_avg:.1f}%")
    print(f"{'='*125}")

    r_final = run_config(datasets, "FINAL", **best_cfg)
    print(HEADER)
    print(SEP)
    print_row("FINAL", r_final, base_avg)

    print(f"\n  Per-asset detail:")
    for sym in ASSETS:
        r = r_final[sym]
        print(f"    {sym}: {r['trades']:>4}t {r['wr']:>5.1f}%w "
              f"L:{r['long_wr']:>5.1f}%({r['n_long']}t) S:{r['short_wr']:>5.1f}%({r['n_short']}t) "
              f"${r['pnl']:>+7.0f} DD:{r['dd']:.1f}% PF:{r['pf']:.2f}")

    print(f"\n  Config:")
    for k, v in sorted(best_cfg.items()):
        print(f"    {k}: {v}")

    elapsed = time.time() - t_start
    print(f"\n  Runtime: {elapsed:.1f}s")

    return r_final, best_cfg


if __name__ == "__main__":
    main()
