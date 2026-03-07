"""
Candle V2.4 Cross-Asset Backtest — Push WR to 90%+.

Pure WR optimization. Track long/short WR separately.
ONE set of params for ALL assets.
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
    """Calculate WR split by direction."""
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

    # Aggregate long/short WR
    total_longs = sum(results[s]["n_long"] for s in ASSETS)
    total_shorts = sum(results[s]["n_short"] for s in ASSETS)
    total_long_wins = sum(
        results[s]["long_wr"] * results[s]["n_long"] / 100 for s in ASSETS
    )
    total_short_wins = sum(
        results[s]["short_wr"] * results[s]["n_short"] / 100 for s in ASSETS
    )
    agg_long_wr = (total_long_wins / total_longs * 100) if total_longs > 0 else 0
    agg_short_wr = (total_short_wins / total_shorts * 100) if total_shorts > 0 else 0

    delta = f" ({avg_wr - baseline_avg:+.1f})" if baseline_avg is not None else ""

    print(f"  {label:<40s} {results['BTC']['wr']:>5.1f}% {results['ETH']['wr']:>5.1f}% "
          f"{results['SOL']['wr']:>5.1f}% {results['LINK']['wr']:>5.1f}% "
          f"{avg_wr:>5.1f}%{delta:>7s} {trades:>5} "
          f"L:{agg_long_wr:>4.0f}% S:{agg_short_wr:>4.0f}%")

    return avg_wr


HEADER = (f"  {'Config':<40s} {'BTC':>6} {'ETH':>6} {'SOL':>6} {'LINK':>6} "
          f"{'Avg':>6} {'Delta':>7} {'Trds':>5} {'Long/Short WR':>14}")
SEP = "  " + "-" * 105


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    datasets = {sym: load_data(sym) for sym in ASSETS}
    sample = datasets["BTC"]
    date_range = f"{sample.index[0].date()} to {sample.index[-1].date()}"

    print("=" * 115)
    print("  CANDLE V2.4 — PUSH WR TO 90%+ (Generic params)")
    print(f"  {len(sample)} bars/asset | {date_range}")
    print(f"  Fee: 0.10% | Capital: $1,000 | V2.3 baseline: 85.2% avg WR")
    print("=" * 115)

    # V2.3 best config as baseline
    v23_base = dict(
        use_rsi=True, use_bb=True, use_volume=True, use_adx=True,
        use_stoch_rsi=True, use_williams_r=True, use_macd=True,
        use_cci=True, use_ema_alignment=True, use_atr_percentile=True,
        use_keltner=True, use_mfi=True, use_obv_slope=True,
        use_range_position=True, use_hh_ll=True,
        min_score=3.0, stop_atr=5.0, target_atr=2.0,
        cooldown=12, base_leverage=2.0, time_exit_bars=144, adx_max=25,
        use_mtf=True, mtf_require="both",
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

    # =====================================================
    # Phase 0: Reproduce V2.3 baseline
    # =====================================================
    print(f"\n  PHASE 0: V2.3 BASELINE REPRODUCTION")
    print(HEADER)
    print(SEP)
    r_base, base_avg = track("V2.3 baseline (MTF both, 5:2, ADX<25)", v23_base)

    # =====================================================
    # Phase 1: 4H Primary Timeframe
    # =====================================================
    print(f"\n  PHASE 1: 4H PRIMARY TIMEFRAME")
    print(HEADER)
    print(SEP)

    # 4H primary with daily confirmation
    for stop, tgt in [(5.0, 2.0), (6.0, 2.0), (7.0, 2.0), (8.0, 2.0), (5.0, 1.5), (6.0, 1.5)]:
        cfg = {**v23_base, "primary_tf": "4h", "stop_atr": stop, "target_atr": tgt}
        track(f"4H primary, R:R {stop}:{tgt}", cfg, base_avg)

    # 4H primary + ADX sweep
    for adx in [20, 25, 30, 35]:
        cfg = {**v23_base, "primary_tf": "4h", "adx_max": adx}
        track(f"4H primary, ADX<{adx}", cfg, base_avg)

    # =====================================================
    # Phase 2: Extreme R:R (wider stop, tighter target)
    # =====================================================
    print(f"\n  PHASE 2: EXTREME R:R RATIOS")
    print(HEADER)
    print(SEP)

    for stop, tgt in [(6.0, 2.0), (7.0, 2.0), (8.0, 2.0), (10.0, 2.0),
                       (7.0, 1.5), (8.0, 1.5), (10.0, 1.5),
                       (8.0, 1.0), (10.0, 1.0), (12.0, 1.0),
                       (6.0, 1.0), (5.0, 1.0)]:
        cfg = {**v23_base, "stop_atr": stop, "target_atr": tgt}
        track(f"R:R {stop}:{tgt}", cfg, base_avg)

    # =====================================================
    # Phase 3: Score Threshold Sweep
    # =====================================================
    print(f"\n  PHASE 3: SCORE THRESHOLD SWEEP")
    print(HEADER)
    print(SEP)
    for ms in [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]:
        cfg = {**v23_base, "min_score": ms}
        track(f"s>={ms}", cfg, base_avg)

    # =====================================================
    # Phase 4: Pattern Sets
    # =====================================================
    print(f"\n  PHASE 4: PATTERN SETS")
    print(HEADER)
    print(SEP)
    for ps in ["marubozu", "marubozu_plus", "top5", "all"]:
        cfg = {**v23_base, "pattern_set": ps}
        track(f"patterns={ps}", cfg, base_avg)

    # =====================================================
    # Phase 5: Volume Spike Requirement
    # =====================================================
    print(f"\n  PHASE 5: VOLUME SPIKE REQUIREMENT")
    print(HEADER)
    print(SEP)
    for vm in [1.2, 1.5, 2.0, 2.5, 3.0]:
        cfg = {**v23_base, "vol_spike_mult": vm}
        track(f"vol_spike>{vm}x", cfg, base_avg)

    # =====================================================
    # Phase 6: Consecutive TF Agreement
    # =====================================================
    print(f"\n  PHASE 6: CONSECUTIVE TF AGREEMENT")
    print(HEADER)
    print(SEP)
    cfg = {**v23_base, "use_consecutive": True}
    track("consecutive (prev bar agrees too)", cfg, base_avg)

    # 4H primary + consecutive
    cfg = {**v23_base, "primary_tf": "4h", "use_consecutive": True}
    track("4H + consecutive", cfg, base_avg)

    # =====================================================
    # Phase 7: Direction Filter (long-only vs short-only)
    # =====================================================
    print(f"\n  PHASE 7: DIRECTION FILTER")
    print(HEADER)
    print(SEP)
    cfg = {**v23_base, "direction_filter": "long_only"}
    track("long_only", cfg, base_avg)
    cfg = {**v23_base, "direction_filter": "short_only"}
    track("short_only", cfg, base_avg)

    # =====================================================
    # Phase 8: Quality Filter + Body Ratio
    # =====================================================
    print(f"\n  PHASE 8: QUALITY FILTER")
    print(HEADER)
    print(SEP)
    for br in [0.3, 0.5, 0.6]:
        cfg = {**v23_base, "use_quality_filter": True, "min_body_ratio": br}
        track(f"body_ratio>{br}", cfg, base_avg)

    # =====================================================
    # Phase 9: STACK best features
    # =====================================================
    print(f"\n  PHASE 9: STACK BEST FEATURES")
    print(HEADER)
    print(SEP)

    # Combine most promising features from above
    # Try various combos
    combos = [
        ("4H + R:R 8:1", dict(primary_tf="4h", stop_atr=8.0, target_atr=1.0)),
        ("4H + R:R 10:1", dict(primary_tf="4h", stop_atr=10.0, target_atr=1.0)),
        ("4H + R:R 8:1.5", dict(primary_tf="4h", stop_atr=8.0, target_atr=1.5)),
        ("R:R 8:1 + s>=4", dict(stop_atr=8.0, target_atr=1.0, min_score=4.0)),
        ("R:R 10:1 + s>=4", dict(stop_atr=10.0, target_atr=1.0, min_score=4.0)),
        ("R:R 8:1 + vol2x", dict(stop_atr=8.0, target_atr=1.0, vol_spike_mult=2.0)),
        ("R:R 10:1 + vol2x", dict(stop_atr=10.0, target_atr=1.0, vol_spike_mult=2.0)),
        ("R:R 8:1 + marubozu", dict(stop_atr=8.0, target_atr=1.0, pattern_set="marubozu")),
        ("R:R 10:1 + marubozu", dict(stop_atr=10.0, target_atr=1.0, pattern_set="marubozu")),
        ("4H + consec + R:R 8:1", dict(primary_tf="4h", use_consecutive=True, stop_atr=8.0, target_atr=1.0)),
        ("R:R 8:1 + consec", dict(stop_atr=8.0, target_atr=1.0, use_consecutive=True)),
        ("R:R 10:1 + consec", dict(stop_atr=10.0, target_atr=1.0, use_consecutive=True)),
        ("marubozu + vol2x + R:R 8:1", dict(pattern_set="marubozu", vol_spike_mult=2.0, stop_atr=8.0, target_atr=1.0)),
        ("marubozu + s>=4 + R:R 8:1", dict(pattern_set="marubozu", min_score=4.0, stop_atr=8.0, target_atr=1.0)),
        ("all_pat + s>=5 + R:R 8:1", dict(pattern_set="all", min_score=5.0, stop_atr=8.0, target_atr=1.0)),
        ("all_pat + s>=6 + R:R 10:1", dict(pattern_set="all", min_score=6.0, stop_atr=10.0, target_atr=1.0)),
        ("body>0.5 + R:R 8:1", dict(use_quality_filter=True, min_body_ratio=0.5, stop_atr=8.0, target_atr=1.0)),
        ("4H + marubozu + R:R 8:1", dict(primary_tf="4h", pattern_set="marubozu", stop_atr=8.0, target_atr=1.0)),
    ]

    for lbl, overrides in combos:
        cfg = {**v23_base}
        cfg.update(overrides)
        track(lbl, cfg, base_avg)

    # =====================================================
    # Phase 10: Fine-tune best
    # =====================================================
    print(f"\n  PHASE 10: FINE-TUNE BEST ({best_label})")
    print(HEADER)
    print(SEP)

    # Cooldown sweep
    for cd in [4, 8, 12, 16, 20, 24]:
        cfg = {**best_cfg, "cooldown": cd}
        track(f"best + cd={cd}", cfg, base_avg)

    # Time exit sweep
    for te in [48, 72, 96, 120, 144, 192]:
        cfg = {**best_cfg, "time_exit_bars": te}
        track(f"best + te={te}", cfg, base_avg)

    # ADX sweep
    for adx in [15, 20, 25, 30, 35, 40]:
        cfg = {**best_cfg, "adx_max": adx}
        track(f"best + adx={adx}", cfg, base_avg)

    # =====================================================
    # Final Result
    # =====================================================
    print(f"\n{'='*115}")
    print("  FINAL BEST CONFIG")
    print(f"{'='*115}")

    r_final = run_config(datasets, "FINAL", **best_cfg)
    print(HEADER)
    print(SEP)
    print_row("FINAL BEST", r_final, base_avg)

    print(f"\n  Per-asset detail:")
    for sym in ASSETS:
        r = r_final[sym]
        print(f"    {sym}: {r['trades']:>4}t {r['wr']:>5.1f}%w "
              f"L:{r['long_wr']:>5.1f}%({r['n_long']}t) S:{r['short_wr']:>5.1f}%({r['n_short']}t) "
              f"${r['pnl']:>+7.0f} DD:{r['dd']:.1f}% PF:{r['pf']:.2f}")

    print(f"\n  Exit reasons:")
    for sym in ASSETS:
        result = r_final[sym]["result"]
        from collections import defaultdict
        exits = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
        for t in result.trades:
            reason = t.exit_reason or "UNKNOWN"
            exits[reason]["count"] += 1
            if t.pnl_usd > 0:
                exits[reason]["wins"] += 1
            exits[reason]["pnl"] += t.pnl_usd
        parts = []
        for reason, s in sorted(exits.items()):
            wr = s["wins"] / s["count"] * 100 if s["count"] > 0 else 0
            parts.append(f"{reason}:{s['count']}t/{wr:.0f}%w")
        print(f"    {sym}: {' | '.join(parts)}")

    print(f"\n  Best config params:")
    for k, v in sorted(best_cfg.items()):
        print(f"    {k}: {v}")

    elapsed = time.time() - t_start
    print(f"\n  Runtime: {elapsed:.1f}s")
    print(f"\n  RESULT: {best_label} -> Avg WR: {best_avg:.1f}%")
    print(f"  V2.3 baseline: {base_avg:.1f}% -> Delta: {best_avg - base_avg:+.1f}%")

    return r_final, best_cfg


if __name__ == "__main__":
    main()
