"""
Candle V2.3 Cross-Asset Backtest — Push WR to 65%+.

Tests each V2.3 feature individually against V2.2 baseline, then stacks.
ONE set of params for ALL assets. No per-asset tuning.
"""

import sys
import time
import os
import pandas as pd
import numpy as np

sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, BacktestResult
from strategies.candle_v2_3 import CandleV2_3


ASSETS = ["BTC", "ETH", "SOL", "LINK"]


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def mo(result, data):
    years = (data.index[-1] - data.index[0]).days / 365.25
    if years <= 0:
        return 0
    return result.total_pnl_pct / (years * 12)


def run_config(datasets, label, **kwargs):
    results = {}
    for sym in ASSETS:
        data = datasets[sym]
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.10, max_risk_pct=2.0)
        strat = CandleV2_3(**kwargs)
        result = engine.run(data, strat, f"V2.3 {sym}")
        monthly = mo(result, data)
        results[sym] = {
            "trades": result.total_trades,
            "wr": result.win_rate,
            "pnl": result.total_pnl_usd,
            "monthly": monthly,
            "dd": result.max_drawdown_pct,
            "pf": result.profit_factor,
            "result": result,
        }
    return results


def print_row(label, results, baseline_avg=None):
    wrs = [results[s]["wr"] for s in ASSETS]
    avg_wr = np.mean(wrs)
    trades = sum(results[s]["trades"] for s in ASSETS)
    pnl = sum(results[s]["pnl"] for s in ASSETS)

    delta = f" ({avg_wr - baseline_avg:+.1f})" if baseline_avg is not None else ""

    print(f"  {label:<45s} {results['BTC']['wr']:>5.1f}% {results['ETH']['wr']:>5.1f}% "
          f"{results['SOL']['wr']:>5.1f}% {results['LINK']['wr']:>5.1f}% "
          f"{avg_wr:>5.1f}%{delta:>7s} {trades:>5} ${pnl:>+8.0f}")

    return avg_wr


HEADER = (f"  {'Config':<45s} {'BTC':>6} {'ETH':>6} {'SOL':>6} {'LINK':>6} "
          f"{'Avg':>6} {'Delta':>7} {'Trds':>5} {'P&L':>9}")
SEP = "  " + "-" * 110


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    datasets = {sym: load_data(sym) for sym in ASSETS}
    sample = datasets["BTC"]
    date_range = f"{sample.index[0].date()} to {sample.index[-1].date()}"

    print("=" * 120)
    print("  CANDLE V2.3 — PUSH WR TO 65%+ (Generic params)")
    print(f"  {len(sample)} bars/asset | {date_range}")
    print(f"  Fee: 0.10% | Capital: $1,000 | V2.2 baseline: 60.5% avg WR")
    print("=" * 120)

    # V2.2 best config as baseline
    v22_base = dict(
        use_rsi=True, use_bb=True, use_volume=True, use_adx=True,
        use_stoch_rsi=True, use_williams_r=True, use_macd=True,
        use_cci=True, use_ema_alignment=True, use_atr_percentile=True,
        use_keltner=True, use_mfi=True, use_obv_slope=True,
        use_range_position=True, use_hh_ll=True,
        min_score=3.0, stop_atr=3.0, target_atr=2.0,
        cooldown=16, base_leverage=2.0, time_exit_bars=96, adx_max=20,
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
    # Phase 0: Reproduce V2.2 baseline
    # =====================================================
    print(f"\n  PHASE 0: V2.2 BASELINE REPRODUCTION")
    print(HEADER)
    print(SEP)
    r_base, base_avg = track("V2.2 baseline (all ind, s>=3, 3:2)", v22_base)

    # =====================================================
    # Phase 1: Score threshold sweep (cheapest test)
    # =====================================================
    print(f"\n  PHASE 1: SCORE THRESHOLD SWEEP")
    print(HEADER)
    print(SEP)
    for ms in [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]:
        cfg = {**v22_base, "min_score": ms}
        track(f"s>={ms}", cfg, base_avg)

    # =====================================================
    # Phase 2: Volatility regime filter
    # =====================================================
    print(f"\n  PHASE 2: VOLATILITY REGIME FILTER")
    print(HEADER)
    print(SEP)
    for bb_pct in [20, 30, 40, 50, 60, 80]:
        cfg = {**v22_base, "use_vol_regime": True, "bb_width_max_pct": bb_pct}
        track(f"BB width <{bb_pct}pct", cfg, base_avg)

    for atr_pct in [30, 40, 50, 60]:
        cfg = {**v22_base, "use_vol_regime": True, "bb_width_max_pct": 100, "atr_max_pct": atr_pct}
        track(f"ATR <{atr_pct}pct", cfg, base_avg)

    # Combined
    for bb_pct in [40, 50, 60]:
        for atr_pct in [40, 50, 60]:
            cfg = {**v22_base, "use_vol_regime": True, "bb_width_max_pct": bb_pct, "atr_max_pct": atr_pct}
            track(f"BB<{bb_pct} + ATR<{atr_pct}", cfg, base_avg)

    # =====================================================
    # Phase 3: Body/wick quality filters
    # =====================================================
    print(f"\n  PHASE 3: CANDLE QUALITY FILTERS")
    print(HEADER)
    print(SEP)
    for br in [0.3, 0.5, 0.6, 0.7]:
        cfg = {**v22_base, "use_quality_filter": True, "min_body_ratio": br}
        track(f"body>{br}", cfg, base_avg)

    # Volume on pattern candle
    for vp in [1.2, 1.5, 2.0]:
        cfg = {**v22_base, "use_quality_filter": True, "vol_on_pattern": vp}
        track(f"vol_pattern>{vp}x", cfg, base_avg)

    # Combined body + volume
    for br in [0.3, 0.5]:
        for vp in [1.2, 1.5]:
            cfg = {**v22_base, "use_quality_filter": True,
                   "min_body_ratio": br, "vol_on_pattern": vp}
            track(f"body>{br}+vol>{vp}x", cfg, base_avg)

    # =====================================================
    # Phase 4: Multi-timeframe confirmation
    # =====================================================
    print(f"\n  PHASE 4: MULTI-TIMEFRAME CONFIRMATION")
    print(HEADER)
    print(SEP)
    for req in ["any", "both"]:
        cfg = {**v22_base, "use_mtf": True, "mtf_require": req}
        track(f"MTF require={req}", cfg, base_avg)

    # MTF + higher score
    for ms in [3.0, 4.0, 5.0]:
        cfg = {**v22_base, "use_mtf": True, "mtf_require": "any", "min_score": ms}
        track(f"MTF any + s>={ms}", cfg, base_avg)

    # =====================================================
    # Phase 5: Candle sequences
    # =====================================================
    print(f"\n  PHASE 5: CANDLE SEQUENCE PATTERNS")
    print(HEADER)
    print(SEP)
    for sb in [0.5, 1.0, 1.5]:
        cfg = {**v22_base, "use_sequences": True, "seq_bonus": sb}
        track(f"sequences bonus={sb}", cfg, base_avg)

    # Sequences + higher score to filter
    for sb in [1.0, 1.5]:
        for ms in [3.5, 4.0, 4.5]:
            cfg = {**v22_base, "use_sequences": True, "seq_bonus": sb, "min_score": ms}
            track(f"seq={sb} + s>={ms}", cfg, base_avg)

    # =====================================================
    # Phase 6: Time-of-day filter
    # =====================================================
    print(f"\n  PHASE 6: TIME-OF-DAY FILTER")
    print(HEADER)
    print(SEP)
    # Common high-activity hours (UTC)
    hour_sets = [
        ("US session 13-21", list(range(13, 22))),
        ("EU session 7-16", list(range(7, 17))),
        ("Asia session 0-8", list(range(0, 9))),
        ("EU+US 7-21", list(range(7, 22))),
        ("Off-hours 21-7", list(range(21, 24)) + list(range(0, 8))),
        ("Best 8h: 8-16", list(range(8, 16))),
        ("Best 12h: 6-18", list(range(6, 18))),
    ]
    for label, hours in hour_sets:
        cfg = {**v22_base, "use_tod_filter": True, "good_hours": hours}
        track(f"TOD: {label}", cfg, base_avg)

    # =====================================================
    # Phase 7: Previous candle direction
    # =====================================================
    print(f"\n  PHASE 7: PREVIOUS CANDLE DIRECTION")
    print(HEADER)
    print(SEP)
    cfg = {**v22_base, "use_prev_candle": True, "prev_candle_same_dir": False}
    track("prev candle opposite (reversal)", cfg, base_avg)
    cfg = {**v22_base, "use_prev_candle": True, "prev_candle_same_dir": True}
    track("prev candle same (momentum)", cfg, base_avg)

    # =====================================================
    # Phase 8: STACK best features
    # =====================================================
    print(f"\n  PHASE 8: STACK BEST FEATURES")
    print(HEADER)
    print(SEP)

    # We'll try various combos of the best individual features
    # Start with baseline + each pair, then triple, etc.
    stack_features = {
        "score": {"min_score": 4.0},
        "vol_regime_40": {"use_vol_regime": True, "bb_width_max_pct": 40},
        "vol_regime_50": {"use_vol_regime": True, "bb_width_max_pct": 50},
        "body_03": {"use_quality_filter": True, "min_body_ratio": 0.3},
        "body_05": {"use_quality_filter": True, "min_body_ratio": 0.5},
        "mtf_any": {"use_mtf": True, "mtf_require": "any"},
        "mtf_both": {"use_mtf": True, "mtf_require": "both"},
        "seq_1": {"use_sequences": True, "seq_bonus": 1.0},
        "prev_rev": {"use_prev_candle": True, "prev_candle_same_dir": False},
        "vol_pat_15": {"use_quality_filter": True, "vol_on_pattern": 1.5},
    }

    # Pairs of most promising
    promising = ["score", "vol_regime_40", "vol_regime_50", "body_03",
                 "mtf_any", "seq_1", "prev_rev", "vol_pat_15"]

    for i, f1 in enumerate(promising):
        for f2 in promising[i+1:]:
            cfg = {**v22_base}
            cfg.update(stack_features[f1])
            cfg.update(stack_features[f2])
            track(f"{f1} + {f2}", cfg, base_avg)

    # Triple stacks of best
    top3 = ["score", "vol_regime_50", "mtf_any", "body_03", "seq_1", "prev_rev"]
    for i, f1 in enumerate(top3):
        for j, f2 in enumerate(top3[i+1:], i+1):
            for f3 in top3[j+1:]:
                cfg = {**v22_base}
                cfg.update(stack_features[f1])
                cfg.update(stack_features[f2])
                cfg.update(stack_features[f3])
                track(f"{f1}+{f2}+{f3}", cfg, base_avg)

    # =====================================================
    # Phase 9: Fine-tune best stack
    # =====================================================
    print(f"\n  PHASE 9: FINE-TUNE BEST ({best_label})")
    print(HEADER)
    print(SEP)

    # Vary R:R around best
    for stop, tgt in [(2.5, 1.5), (3.0, 1.5), (3.5, 2.0), (3.0, 2.0),
                       (4.0, 2.0), (3.5, 2.5), (4.0, 2.5), (3.0, 2.5),
                       (5.0, 2.0), (4.0, 3.0)]:
        cfg = {**best_cfg, "stop_atr": stop, "target_atr": tgt}
        track(f"best + R:R {stop}:{tgt}", cfg, base_avg)

    # Vary cooldown
    for cd in [8, 12, 16, 20, 24]:
        cfg = {**best_cfg, "cooldown": cd}
        track(f"best + cd={cd}", cfg, base_avg)

    # Vary time exit
    for te in [48, 72, 96, 120, 144]:
        cfg = {**best_cfg, "time_exit_bars": te}
        track(f"best + te={te}", cfg, base_avg)

    # Vary ADX
    for adx in [15, 20, 25, 30]:
        cfg = {**best_cfg, "adx_max": adx}
        track(f"best + adx={adx}", cfg, base_avg)

    # =====================================================
    # Final Result
    # =====================================================
    print(f"\n{'='*120}")
    print("  FINAL BEST CONFIG")
    print(f"{'='*120}")

    r_final = run_config(datasets, "FINAL", **best_cfg)
    print(HEADER)
    print(SEP)
    print_row("FINAL BEST", r_final, base_avg)

    print(f"\n  Per-asset detail:")
    for sym in ASSETS:
        r = r_final[sym]
        print(f"    {sym}: {r['trades']:>4}t {r['wr']:>5.1f}%w ${r['pnl']:>+7.0f} "
              f"({r['monthly']:>+.2f}%/mo) DD:{r['dd']:.1f}% PF:{r['pf']:.2f}")

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
        if v is True or (isinstance(v, (int, float)) and k.startswith("use_") and v):
            print(f"    {k}: {v}")
    print()
    for k, v in sorted(best_cfg.items()):
        if not k.startswith("use_"):
            print(f"    {k}: {v}")

    elapsed = time.time() - t_start
    print(f"\n  Runtime: {elapsed:.1f}s")
    print(f"\n  RESULT: {best_label} -> Avg WR: {best_avg:.1f}%")
    print(f"  V2.2 baseline: {base_avg:.1f}% -> Delta: {best_avg - base_avg:+.1f}%")

    return r_final, best_cfg


if __name__ == "__main__":
    main()
