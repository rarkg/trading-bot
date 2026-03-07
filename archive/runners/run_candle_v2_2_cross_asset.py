"""
Candle V2.2 Cross-Asset Backtest — Multi-Indicator Confirmation Strategy.

Tests each indicator's contribution individually, then combines best ones.
ONE set of params for ALL assets. No per-asset tuning.
"""

import sys
import time
import os
import pandas as pd
import numpy as np

sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, BacktestResult
from strategies.candle_v2_2 import CandleV2_2


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
    """Run one configuration across all assets. Returns per-asset results."""
    results = {}
    for sym in ASSETS:
        data = datasets[sym]
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.10, max_risk_pct=2.0)
        strat = CandleV2_2(**kwargs)
        result = engine.run(data, strat, f"V2.2 {sym}")
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


def print_row(label, results):
    """Print one row of the indicator comparison table."""
    wrs = [results[s]["wr"] for s in ASSETS]
    avg_wr = np.mean(wrs)
    trades = sum(results[s]["trades"] for s in ASSETS)
    pnl = sum(results[s]["pnl"] for s in ASSETS)
    avg_mo = np.mean([results[s]["monthly"] for s in ASSETS])

    btc_wr = results["BTC"]["wr"]
    eth_wr = results["ETH"]["wr"]
    sol_wr = results["SOL"]["wr"]
    link_wr = results["LINK"]["wr"]

    verdict = "GOOD" if avg_wr >= 51 else ("OK" if avg_wr >= 48 else "WEAK")
    if all(wr >= 51 for wr in wrs):
        verdict = "TARGET"

    print(f"  {label:<35s} {btc_wr:>5.1f}% {eth_wr:>5.1f}% {sol_wr:>5.1f}% {link_wr:>5.1f}% "
          f"{avg_wr:>5.1f}% {trades:>5} ${pnl:>+7.0f} {avg_mo:>+.2f}%/mo {verdict}")

    return avg_wr


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    datasets = {sym: load_data(sym) for sym in ASSETS}
    sample = datasets["BTC"]
    date_range = f"{sample.index[0].date()} to {sample.index[-1].date()}"

    print("=" * 130)
    print("  CANDLE V2.2 — MULTI-INDICATOR CONFIRMATION STRATEGY (Iteration 2)")
    print(f"  {len(sample)} bars/asset | {date_range}")
    print(f"  Fee: 0.10% | Capital: $1,000 | Generic params (same for all assets)")
    print("=" * 130)

    header = (f"  {'Config':<40s} {'BTC':>6} {'ETH':>6} {'SOL':>6} {'LINK':>6} "
              f"{'Avg':>6} {'Trds':>5} {'P&L':>8} {'Mo%':>9} {'Verdict'}")
    sep = "  " + "-" * 120

    best_avg = 0
    best_label = ""
    best_cfg = {}

    def track(label, cfg):
        nonlocal best_avg, best_label, best_cfg
        r = run_config(datasets, label, **cfg)
        avg = print_row(label, r)
        if avg > best_avg:
            best_avg = avg
            best_label = label
            best_cfg = cfg.copy()
        return r, avg

    # =====================================================
    # Phase 1: Previous best (from iteration 1)
    # =====================================================
    print(f"\n{'='*130}")
    print("  PHASE 1: PREVIOUS BEST FROM ITERATION 1")
    print(f"{'='*130}")
    print(header)
    print(sep)

    prev_best = dict(
        use_rsi=True, use_bb=True, use_volume=True, use_adx=True,
        use_stoch_rsi=True, use_williams_r=True, use_macd=True,
        use_cci=True, use_ema_alignment=True, use_atr_percentile=True,
        use_keltner=True, use_mfi=True, use_obv_slope=True,
        use_range_position=True, use_hh_ll=True,
        min_score=3.0, stop_atr=2.0, target_atr=2.0, cooldown=12,
        base_leverage=2.0, time_exit_bars=72, adx_max=50,
    )
    track("Prev best (all ind, s>=3, 2:2)", prev_best)

    # =====================================================
    # Phase 2: MARUBOZU-only (strongest pattern)
    # =====================================================
    print(f"\n{'='*130}")
    print("  PHASE 2: MARUBOZU-ONLY vs TOP5 PATTERNS")
    print(f"{'='*130}")
    print(header)
    print(sep)

    for pat_set in ["top5", "marubozu", "marubozu_plus"]:
        for ms in [2.0, 3.0, 4.0]:
            cfg = {**prev_best, "pattern_set": pat_set, "min_score": ms}
            track(f"{pat_set} s>={ms:.0f} R:R 2:2", cfg)

    # =====================================================
    # Phase 3: Hard filter mode
    # =====================================================
    print(f"\n{'='*130}")
    print("  PHASE 3: HARD FILTER MODE (require ALL indicators)")
    print(f"{'='*130}")
    print(header)
    print(sep)

    # Hard filter with fewer but stronger indicators
    hard_combos = [
        ("RSI+BB+Vol hard", dict(use_rsi=True, use_bb=True, use_volume=True, use_adx=True, hard_filter=True)),
        ("RSI+BB+Vol+EMA hard", dict(use_rsi=True, use_bb=True, use_volume=True, use_adx=True, use_ema_alignment=True, hard_filter=True)),
        ("RSI+BB+Vol+Range hard", dict(use_rsi=True, use_bb=True, use_volume=True, use_adx=True, use_range_position=True, hard_filter=True)),
        ("RSI+Vol+Range hard", dict(use_rsi=True, use_volume=True, use_adx=True, use_range_position=True, hard_filter=True)),
        ("RSI+Vol+EMA hard", dict(use_rsi=True, use_volume=True, use_adx=True, use_ema_alignment=True, hard_filter=True)),
        ("BB+Vol+EMA hard", dict(use_bb=True, use_volume=True, use_adx=True, use_ema_alignment=True, hard_filter=True)),
    ]

    for label, ind_cfg in hard_combos:
        for rr in [(2.0, 2.0), (2.0, 3.0), (2.0, 2.5)]:
            cfg = dict(stop_atr=rr[0], target_atr=rr[1], cooldown=8,
                       base_leverage=2.0, time_exit_bars=48, adx_max=40,
                       **ind_cfg)
            track(f"{label} {rr[0]}:{rr[1]}", cfg)

    # =====================================================
    # Phase 4: Long-only vs Short-only
    # =====================================================
    print(f"\n{'='*130}")
    print("  PHASE 4: DIRECTIONAL BIAS")
    print(f"{'='*130}")
    print(header)
    print(sep)

    for direction in ["both", "long_only", "short_only"]:
        cfg = {**prev_best, "direction_filter": direction}
        track(f"All ind s>=3 {direction}", cfg)

    # =====================================================
    # Phase 5: Wider RSI zones (more lenient entry)
    # =====================================================
    print(f"\n{'='*130}")
    print("  PHASE 5: RSI ZONE WIDTH (wider = more entries)")
    print(f"{'='*130}")
    print(header)
    print(sep)

    for os_val, ob_val in [(35, 65), (40, 60), (45, 55)]:
        for ms in [3.0, 4.0, 5.0]:
            cfg = {**prev_best, "rsi_oversold": os_val, "rsi_overbought": ob_val, "min_score": ms}
            track(f"RSI {os_val}/{ob_val} s>={ms:.0f}", cfg)

    # =====================================================
    # Phase 6: Tighter R:R ratios with high score
    # =====================================================
    print(f"\n{'='*130}")
    print("  PHASE 6: TIGHT R:R WITH HIGH SCORE THRESHOLD")
    print(f"{'='*130}")
    print(header)
    print(sep)

    # Key insight: R:R 2:2 gives highest WR. Try even tighter to push WR higher.
    for stop, tgt in [(2.0, 1.5), (1.5, 1.5), (2.5, 2.0), (3.0, 2.0), (2.0, 2.0), (2.5, 2.5)]:
        for ms in [3.0, 4.0]:
            cfg = {**prev_best, "stop_atr": stop, "target_atr": tgt, "min_score": ms}
            track(f"R:R {stop}:{tgt} s>={ms:.0f}", cfg)

    # =====================================================
    # Phase 7: Wider RSI + EMA + key indicators only
    # =====================================================
    print(f"\n{'='*130}")
    print("  PHASE 7: CURATED INDICATOR COMBOS (no kitchen sink)")
    print(f"{'='*130}")
    print(header)
    print(sep)

    # Top contributors from iter1: EMA (+2.0%), OBV (+1.6%), CCI (+0.6%)
    curated_combos = [
        ("EMA+OBV+Vol", dict(use_ema_alignment=True, use_obv_slope=True, use_volume=True, use_adx=True)),
        ("EMA+OBV+RSI", dict(use_ema_alignment=True, use_obv_slope=True, use_rsi=True, use_adx=True)),
        ("EMA+OBV+BB", dict(use_ema_alignment=True, use_obv_slope=True, use_bb=True, use_adx=True)),
        ("EMA+CCI+Vol", dict(use_ema_alignment=True, use_cci=True, use_volume=True, use_adx=True)),
        ("EMA+RSI+Vol", dict(use_ema_alignment=True, use_rsi=True, use_volume=True, use_adx=True)),
        ("EMA+Range+Vol", dict(use_ema_alignment=True, use_range_position=True, use_volume=True, use_adx=True)),
        ("EMA+OBV+CCI+Vol", dict(use_ema_alignment=True, use_obv_slope=True, use_cci=True, use_volume=True, use_adx=True)),
        ("EMA+OBV+RSI+Vol", dict(use_ema_alignment=True, use_obv_slope=True, use_rsi=True, use_volume=True, use_adx=True)),
        ("EMA+OBV+RSI+BB+Vol", dict(use_ema_alignment=True, use_obv_slope=True, use_rsi=True, use_bb=True, use_volume=True, use_adx=True)),
    ]

    for label, ind_cfg in curated_combos:
        for ms in [1.5, 2.0, 2.5, 3.0]:
            for rr in [(2.0, 2.0), (2.5, 2.5)]:
                cfg = dict(stop_atr=rr[0], target_atr=rr[1], cooldown=8,
                           base_leverage=2.0, time_exit_bars=48, adx_max=40,
                           **ind_cfg, min_score=ms)
                track(f"{label} s>={ms} {rr[0]}:{rr[1]}", cfg)

    # =====================================================
    # Phase 8: Fine-tune best config
    # =====================================================
    print(f"\n{'='*130}")
    print(f"  PHASE 8: FINE-TUNE BEST ({best_label})")
    print(f"{'='*130}")
    print(header)
    print(sep)

    # Vary cooldown
    for cd in [4, 6, 8, 10, 12, 16]:
        cfg = {**best_cfg, "cooldown": cd}
        track(f"best + cd={cd}", cfg)

    # Vary time exit
    for te in [24, 36, 48, 60, 72, 96]:
        cfg = {**best_cfg, "time_exit_bars": te}
        track(f"best + te={te}", cfg)

    # Vary ADX max
    for adx in [20, 25, 30, 35, 40, 50, 999]:
        cfg = {**best_cfg, "adx_max": adx}
        track(f"best + adx_max={adx}", cfg)

    # Vary leverage
    for lev in [1.0, 1.5, 2.0, 2.5, 3.0]:
        cfg = {**best_cfg, "base_leverage": lev}
        track(f"best + lev={lev}", cfg)

    # =====================================================
    # Final Result
    # =====================================================
    print(f"\n{'='*130}")
    print("  FINAL BEST CONFIG")
    print(f"{'='*130}")

    r_final = run_config(datasets, "FINAL", **best_cfg)
    print(header)
    print(sep)
    print_row("FINAL BEST", r_final)

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
        if k.startswith("use_") and v:
            print(f"    {k}: {v}")
    print()
    for k, v in sorted(best_cfg.items()):
        if not k.startswith("use_"):
            print(f"    {k}: {v}")

    elapsed = time.time() - t_start
    print(f"\n  Runtime: {elapsed:.1f}s")
    print(f"\n  Best: {best_label} -> Avg WR: {best_avg:.1f}%")

    return r_final, best_cfg


if __name__ == "__main__":
    main()
