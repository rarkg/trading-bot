"""
Candle V2.1 Cross-Asset Backtest — Candlestick Pattern-First Strategy.

Runs on BTC/ETH/SOL/LINK with detailed per-pattern breakdown.
Shows which candlestick patterns actually work on crypto hourly data.
"""

import sys
import time
import os
import pandas as pd
import numpy as np
from collections import defaultdict

sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, BacktestResult, Trade
from strategies.candle_v2_1 import CandleV2_1, get_tier, ALL_CDL_FUNCTIONS


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def mo(result, data):
    """Monthly return."""
    years = (data.index[-1] - data.index[0]).days / 365.25
    if years <= 0:
        return 0
    return result.total_pnl_pct / (years * 12)


def analyze_pattern_stats(trades, label=""):
    """Analyze per-pattern performance from trades."""
    stats = defaultdict(lambda: {
        "long_wins": 0, "long_losses": 0,
        "short_wins": 0, "short_losses": 0,
        "total_pnl": 0.0,
    })

    for t in trades:
        # Signal format: "PATTERNNAME(T1)"
        sig = t.signal
        if not sig or "(" not in sig:
            continue
        pat_name = sig.split("(")[0]
        tier_str = sig.split("(")[1].rstrip(")")

        won = t.pnl_usd > 0
        if t.direction == "LONG":
            if won:
                stats[pat_name]["long_wins"] += 1
            else:
                stats[pat_name]["long_losses"] += 1
        else:
            if won:
                stats[pat_name]["short_wins"] += 1
            else:
                stats[pat_name]["short_losses"] += 1
        stats[pat_name]["total_pnl"] += t.pnl_usd

    return dict(stats)


def print_pattern_table(all_stats, assets):
    """Print combined per-pattern performance table."""
    # Merge stats across assets
    combined = defaultdict(lambda: {
        "long_wins": 0, "long_losses": 0,
        "short_wins": 0, "short_losses": 0,
        "total_pnl": 0.0,
        "per_asset": {},
    })

    for sym in assets:
        if sym not in all_stats:
            continue
        for pat, s in all_stats[sym].items():
            combined[pat]["long_wins"] += s["long_wins"]
            combined[pat]["long_losses"] += s["long_losses"]
            combined[pat]["short_wins"] += s["short_wins"]
            combined[pat]["short_losses"] += s["short_losses"]
            combined[pat]["total_pnl"] += s["total_pnl"]
            combined[pat]["per_asset"][sym] = s

    if not combined:
        print("  No pattern data.")
        return combined

    # Sort by total trades descending
    sorted_pats = sorted(
        combined.items(),
        key=lambda x: (x[1]["long_wins"] + x[1]["long_losses"] +
                        x[1]["short_wins"] + x[1]["short_losses"]),
        reverse=True,
    )

    header = (f"  {'Pattern':<25s} {'Tier':>4} {'Total':>5} "
              f"{'LongW':>5} {'LongL':>5} {'L_WR':>6} "
              f"{'ShrtW':>5} {'ShrtL':>5} {'S_WR':>6} "
              f"{'WR%':>6} {'P&L$':>8}")
    print(header)
    print("  " + "-" * (len(header) - 2))

    for pat, s in sorted_pats:
        tier = get_tier(pat)
        lw, ll = s["long_wins"], s["long_losses"]
        sw, sl = s["short_wins"], s["short_losses"]
        lt = lw + ll
        st = sw + sl
        total = lt + st
        if total == 0:
            continue

        l_wr = (lw / lt * 100) if lt > 0 else 0
        s_wr = (sw / st * 100) if st > 0 else 0
        total_wr = ((lw + sw) / total * 100) if total > 0 else 0

        print(f"  {pat:<25s} T{tier:>2}  {total:>5} "
              f"{lw:>5} {ll:>5} {l_wr:>5.1f}% "
              f"{sw:>5} {sl:>5} {s_wr:>5.1f}% "
              f"{total_wr:>5.1f}% ${s['total_pnl']:>+7.0f}")

    return combined


def print_tier_summary(combined):
    """Print tier-level summary."""
    tier_stats = {1: {"w": 0, "l": 0, "pnl": 0}, 2: {"w": 0, "l": 0, "pnl": 0},
                  3: {"w": 0, "l": 0, "pnl": 0}}

    for pat, s in combined.items():
        tier = get_tier(pat)
        w = s["long_wins"] + s["short_wins"]
        l = s["long_losses"] + s["short_losses"]
        tier_stats[tier]["w"] += w
        tier_stats[tier]["l"] += l
        tier_stats[tier]["pnl"] += s["total_pnl"]

    print(f"\n  {'Tier':<8} {'Wins':>6} {'Losses':>6} {'Total':>6} {'WR%':>7} {'P&L$':>9}")
    print("  " + "-" * 46)
    for tier in [1, 2, 3]:
        ts = tier_stats[tier]
        total = ts["w"] + ts["l"]
        wr = (ts["w"] / total * 100) if total > 0 else 0
        print(f"  Tier {tier:<2} {ts['w']:>6} {ts['l']:>6} {total:>6} {wr:>6.1f}% ${ts['pnl']:>+8.0f}")


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    ASSETS = ["BTC", "ETH", "SOL", "LINK"]
    datasets = {sym: load_data(sym) for sym in ASSETS}

    sample = datasets["BTC"]
    total_bars = len(sample)
    date_range = f"{sample.index[0].date()} to {sample.index[-1].date()}"

    print("=" * 90)
    print("  CANDLE V2.1 CROSS-ASSET BACKTEST — Candlestick Pattern-First Strategy")
    print(f"  {total_bars} bars/asset | {date_range}")
    print(f"  Fee: 0.10% | Risk: 2% per trade | All 61 CDL patterns enabled")
    print("=" * 90)

    # =====================================================
    # 1. Per-Asset Results
    # =====================================================
    print("\n" + "=" * 90)
    print("  [1] PER-ASSET RESULTS")
    print("=" * 90)

    results = {}
    all_pattern_stats = {}

    for sym in ASSETS:
        data = datasets[sym]
        engine = BacktestEngine(initial_capital=1000, fee_pct=0.10, max_risk_pct=2.0)
        strat = CandleV2_1(asset_name=sym)

        result = engine.run(data, strat, f"CandleV2.1 {sym}")
        monthly = mo(result, data)
        results[sym] = (result, monthly)

        # Collect pattern stats from trades
        pat_stats = analyze_pattern_stats(result.trades)
        all_pattern_stats[sym] = pat_stats

        print(f"\n  {sym}: {result.total_trades}t "
              f"{result.win_rate:.1f}%w ${result.total_pnl_usd:+,.0f} "
              f"({monthly:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}% "
              f"PF:{result.profit_factor:.2f} Sharpe:{result.sharpe_ratio:.2f} "
              f"Avg:{result.avg_trade_duration_hours:.1f}h")

    # =====================================================
    # 2. Per-Pattern Breakdown (ALL assets combined)
    # =====================================================
    print("\n" + "=" * 90)
    print("  [2] PER-PATTERN PERFORMANCE (ALL ASSETS COMBINED)")
    print("=" * 90 + "\n")

    combined = print_pattern_table(all_pattern_stats, ASSETS)

    # =====================================================
    # 3. Tier Summary
    # =====================================================
    print("\n" + "=" * 90)
    print("  [3] TIER SUMMARY")
    print("=" * 90)

    print_tier_summary(combined)

    # =====================================================
    # 4. Per-Asset Pattern Breakdown
    # =====================================================
    print("\n" + "=" * 90)
    print("  [4] PER-ASSET PATTERN BREAKDOWN")
    print("=" * 90)

    for sym in ASSETS:
        print(f"\n  --- {sym} ---")
        sym_combined = {}
        if sym in all_pattern_stats:
            for pat, s in all_pattern_stats[sym].items():
                sym_combined[pat] = s
                sym_combined[pat]["per_asset"] = {}

        if not sym_combined:
            print("  No trades.")
            continue

        sorted_pats = sorted(
            sym_combined.items(),
            key=lambda x: (x[1]["long_wins"] + x[1]["long_losses"] +
                            x[1]["short_wins"] + x[1]["short_losses"]),
            reverse=True,
        )

        print(f"  {'Pattern':<25s} {'Tier':>4} {'Tot':>4} {'W':>3} {'L':>3} {'WR%':>6} {'P&L$':>8}")
        for pat, s in sorted_pats[:15]:
            tier = get_tier(pat)
            w = s["long_wins"] + s["short_wins"]
            l = s["long_losses"] + s["short_losses"]
            total = w + l
            if total == 0:
                continue
            wr = w / total * 100
            print(f"  {pat:<25s} T{tier:>2}  {total:>4} {w:>3} {l:>3} {wr:>5.1f}% ${s['total_pnl']:>+7.0f}")

    # =====================================================
    # 5. Best Patterns (>45% WR, >5 trades)
    # =====================================================
    print("\n" + "=" * 90)
    print("  [5] BEST PATTERNS (WR > 45%, MIN 5 TRADES)")
    print("=" * 90 + "\n")

    good_patterns = []
    for pat, s in combined.items():
        w = s["long_wins"] + s["short_wins"]
        l = s["long_losses"] + s["short_losses"]
        total = w + l
        if total < 5:
            continue
        wr = w / total * 100
        if wr >= 45:
            good_patterns.append((pat, wr, total, s["total_pnl"]))

    good_patterns.sort(key=lambda x: x[1], reverse=True)

    print(f"  {'Pattern':<25s} {'Tier':>4} {'WR%':>6} {'Trades':>6} {'P&L$':>8}")
    print("  " + "-" * 52)
    for pat, wr, total, pnl in good_patterns:
        tier = get_tier(pat)
        print(f"  {pat:<25s} T{tier:>2}  {wr:>5.1f}% {total:>6} ${pnl:>+7.0f}")

    if not good_patterns:
        print("  None found.")

    # =====================================================
    # 6. Exit Reason Breakdown
    # =====================================================
    print("\n" + "=" * 90)
    print("  [6] EXIT REASON BREAKDOWN")
    print("=" * 90 + "\n")

    for sym in ASSETS:
        result = results[sym][0]
        exits = defaultdict(lambda: {"count": 0, "wins": 0, "pnl": 0})
        for t in result.trades:
            reason = t.exit_reason or "UNKNOWN"
            exits[reason]["count"] += 1
            if t.pnl_usd > 0:
                exits[reason]["wins"] += 1
            exits[reason]["pnl"] += t.pnl_usd

        print(f"  {sym}:")
        for reason, s in sorted(exits.items()):
            wr = s["wins"] / s["count"] * 100 if s["count"] > 0 else 0
            print(f"    {reason:<20s}: {s['count']:>4}t {wr:>5.1f}%w ${s['pnl']:>+7.0f}")

    # =====================================================
    # 7. Long vs Short
    # =====================================================
    print("\n" + "=" * 90)
    print("  [7] LONG vs SHORT PERFORMANCE")
    print("=" * 90 + "\n")

    for sym in ASSETS:
        result = results[sym][0]
        longs = [t for t in result.trades if t.direction == "LONG"]
        shorts = [t for t in result.trades if t.direction == "SHORT"]

        l_wins = sum(1 for t in longs if t.pnl_usd > 0)
        s_wins = sum(1 for t in shorts if t.pnl_usd > 0)
        l_wr = l_wins / len(longs) * 100 if longs else 0
        s_wr = s_wins / len(shorts) * 100 if shorts else 0
        l_pnl = sum(t.pnl_usd for t in longs)
        s_pnl = sum(t.pnl_usd for t in shorts)

        print(f"  {sym}: Long {len(longs):>4}t {l_wr:>5.1f}%w ${l_pnl:>+7.0f} | "
              f"Short {len(shorts):>4}t {s_wr:>5.1f}%w ${s_pnl:>+7.0f}")

    # =====================================================
    # 8. MARUBOZU-Only Variants
    # =====================================================
    print("\n" + "=" * 90)
    print("  [8] MARUBOZU-ONLY VARIANT TESTS")
    print("=" * 90)

    marubozu_only = {"CDLMARUBOZU"}
    configs = [
        ("1x lev, 2:2.5 R:R", 1.0, 2.0, 2.5, {1: 1}),
        ("1x lev, 2:3.5 R:R", 1.0, 2.0, 3.5, {1: 1}),
        ("2x lev, 2:2.5 R:R", 2.0, 2.0, 2.5, {1: 1}),
        ("2x lev, 2:3.5 R:R", 2.0, 2.0, 3.5, {1: 1}),
        ("3x lev, 2.5:3.5 R:R", 3.0, 2.5, 3.5, {1: 1}),
        ("No filter (0 conf)", 1.0, 2.0, 2.5, {1: 0}),
        ("Score>=2, 2x, 2:3", 2.0, 2.0, 3.0, {1: 2}),
    ]

    for label, lev, stop, tgt, confs in configs:
        print(f"\n  --- MARUBOZU {label} ---")
        for sym in ASSETS:
            data = datasets[sym]
            engine = BacktestEngine(initial_capital=1000, fee_pct=0.10, max_risk_pct=2.0)
            strat = CandleV2_1(
                asset_name=sym, enabled_patterns=marubozu_only,
                min_tier=1, require_confirmations=confs,
                cooldown=8, stop_atr=stop, target_atr=tgt,
                base_leverage=lev, time_exit_bars=48,
            )
            r = engine.run(data, strat, f"MBOZU {sym}")
            m = mo(r, data)
            print(f"    {sym}: {r.total_trades:>4}t {r.win_rate:>5.1f}%w "
                  f"${r.total_pnl_usd:>+7.0f} ({m:>+.2f}%/mo) DD:{r.max_drawdown_pct:.1f}%")

    # =====================================================
    # Summary
    # =====================================================
    print("\n" + "=" * 90)
    print("  FINAL SUMMARY")
    print("=" * 90)

    for sym in ASSETS:
        r, m = results[sym]
        print(f"  {sym}: {m:+.2f}%/mo | {r.total_trades}t {r.win_rate:.1f}%w "
              f"DD:{r.max_drawdown_pct:.1f}% PF:{r.profit_factor:.2f}")

    all_monthly = [results[s][1] for s in ASSETS]
    avg_m = np.mean(all_monthly)
    print(f"\n  Average monthly: {avg_m:+.2f}%/mo")
    print(f"  Good patterns (>45% WR): {len(good_patterns)}")
    if good_patterns:
        print(f"  Best: {good_patterns[0][0]} ({good_patterns[0][1]:.1f}% WR, {good_patterns[0][2]} trades)")

    elapsed = time.time() - t_start
    print(f"\n  Runtime: {elapsed:.1f}s")

    return results, combined, good_patterns


if __name__ == "__main__":
    main()
