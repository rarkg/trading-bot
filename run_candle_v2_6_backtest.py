"""
Candle V2.6 Backtest — Full spec: wick guard, smart entries, regime sizing,
pyramiding, scale-out, time-of-day sizing, dynamic capital allocation.

Output:
  1. Full 4-year run (2022-2026)
  2. Yearly breakdown (2022, 2023, 2024, 2025, 2026 YTD)
  3. Stress test (all 17 periods) — V2.5 vs V2.6 per period
  4. Per-regime breakdown
  5. Clear summary table

Fees: 0.05% taker per side (0.10% round trip) per Kraken futures.
"""

import sys
import time
import os
import pandas as pd
import numpy as np
from typing import Dict, List

sys.path.insert(0, ".")

from backtest.engine import Trade
from strategies.candle_v2_3 import CandleV2_3
from live.regime import RegimeDetector
from live.wick_guard import WickGuardBacktest
from live.entry_optimizer import EntryOptimizerBacktest
from backtest.stress_periods import STRESS_PERIODS


ASSETS = ["BTC", "ETH", "SOL", "LINK", "ADA", "AVAX", "DOGE", "XRP", "ICP", "SHIB"]

CAPITAL = 500.0             # $500/asset as spec says ($5000 total for 10 assets)
FEE_PCT = 0.05              # 0.05% taker per side (Kraken futures)
MAX_RISK_PCT = 2.0

# V2.5 baseline params
V25_PARAMS = dict(
    use_rsi=True, use_stoch_rsi=True, use_williams_r=True, use_macd=True,
    use_cci=True, use_ema_alignment=True, use_adx=True, use_bb=True,
    use_atr_percentile=True, use_keltner=True, use_volume=True, use_mfi=True,
    use_obv_slope=True, use_range_position=True, use_hh_ll=True,
    use_mtf=True, mtf_require="both",
    min_score=1, stop_atr=2.0, target_atr=4.0,
    cooldown=12, time_exit_bars=144, base_leverage=2.0,
    adx_max=50,
    use_trailing_stop=True,
    trail_activation_atr=1.5,
    trail_distance_atr=0.3,
)

# Time-of-day sizing multipliers (hour UTC -> multiplier)
TOD_MULTIPLIERS = {}
for _h in range(24):
    if 13 <= _h <= 21:
        TOD_MULTIPLIERS[_h] = 1.2   # US + London overlap
    elif 1 <= _h <= 7:
        TOD_MULTIPLIERS[_h] = 0.8   # Asian dead zone
    else:
        TOD_MULTIPLIERS[_h] = 1.0

# Pyramiding constants
PYRAMID_PROFIT_ATR = 1.0     # Min profit in ATR to pyramid
PYRAMID_SIZE_PCT = 0.5       # Add 50% of original size
MAX_PYRAMIDS = 2

# Scale-out constants
SCALE_OUT_TRIGGER_PCT = 0.5  # At 50% of stop distance
SCALE_OUT_REDUCE_PCT = 0.3   # Reduce by 30%


def load_data(symbol, timeframe="hourly"):
    fp = f"data/{symbol}_USD_{timeframe}.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def load_15m_data(symbol):
    try:
        return load_data(symbol, "15m")
    except FileNotFoundError:
        return None


def get_15m_closes_for_hour(df_15m, hour_ts):
    if df_15m is None:
        return []
    hour_end = hour_ts + pd.Timedelta(hours=1)
    mask = (df_15m.index >= hour_ts) & (df_15m.index < hour_end)
    bars = df_15m[mask]
    if len(bars) == 0:
        return []
    return bars["close"].values.tolist()


def _compute_size(price, stop, cap, max_risk, leverage, regime_state, direction,
                  tod_mult, use_pct_sizing, total_equity, margin_in_use, score):
    """Unified position sizing with all V2.6 features."""
    if use_pct_sizing:
        available = max(total_equity - margin_in_use, 0)
        base_size = available * 0.15  # 15% base

        # Score multiplier
        if score >= 4:
            score_mult = 1.3
        elif score >= 3:
            score_mult = 1.0
        else:
            score_mult = 0.7

        size = base_size * score_mult * tod_mult

        # Regime multiplier
        if regime_state is not None:
            rsm = regime_state.regime_size_multiplier(direction)
            size *= rsm

        # Clamp: 3% min, 30% max of total equity
        size = max(size, total_equity * 0.03)
        size = min(size, total_equity * 0.30)

        # Exposure cap: 200% total
        if margin_in_use + size > total_equity * 2.0:
            size = max(0, total_equity * 2.0 - margin_in_use)

        size *= leverage
    else:
        # Legacy fixed-cap sizing
        risk_per_unit = abs(price - stop) if stop else price * 0.02
        margin = min(cap * 0.4, cap)
        size = margin * leverage
        max_loss = cap * max_risk
        if price > 0:
            loss = risk_per_unit * (size / price)
        else:
            loss = max_loss
        if loss > max_loss and risk_per_unit > 0:
            size = max_loss / (risk_per_unit / price)
        size = min(size, cap * leverage)

        if regime_state is not None:
            rsm = regime_state.regime_size_multiplier(direction)
            size *= rsm

        size *= tod_mult

    return max(size, 0)


def run_multi_asset(
    datasets,
    strategy_factory,
    use_wick_guard=False,
    use_smart_entries=False,
    use_regime_sizing=False,
    use_pyramiding=False,
    use_scale_out=False,
    use_tod_sizing=False,
    use_pct_sizing=False,
    datasets_15m=None,
    start_date=None,
    end_date=None,
):
    """Run synchronized multi-asset backtest with all V2.6 features."""

    fee_rate = FEE_PCT / 100
    max_risk = MAX_RISK_PCT / 100
    assets = list(datasets.keys())

    strategies = {a: strategy_factory() for a in assets}
    capitals = {a: CAPITAL for a in assets}
    open_trades = {}          # asset -> Trade
    all_trades = {a: [] for a in assets}
    peak_caps = {a: CAPITAL for a in assets}
    max_dds = {a: 0.0 for a in assets}

    # V2.6 components
    wick_guard = WickGuardBacktest(enabled=use_wick_guard)
    entry_opt = EntryOptimizerBacktest(enabled=use_smart_entries, pullback_atr=0.3)
    regime_detector = RegimeDetector(enabled=use_regime_sizing)

    pending_entries = {}      # asset -> dict
    pyramid_counts = {}       # asset -> int
    scale_out_done = {}       # asset -> bool
    original_sizes = {}       # asset -> float (for pyramid sizing)

    # Per-regime trade tracking
    regime_trades = {a: {"TRENDING_UP": [], "TRENDING_DOWN": [], "RANGING": [], "NEUTRAL": []} for a in assets}

    # Dynamic capital
    total_equity = CAPITAL * len(assets)
    margin_in_use = 0.0

    # Filter datasets by date range
    filtered = {}
    for a in assets:
        df = datasets[a]
        if start_date:
            df = df[df.index >= start_date]
        if end_date:
            df = df[df.index <= end_date]
        filtered[a] = df

    all_times = sorted(set().union(*(set(df.index) for df in filtered.values())))

    for ts in all_times:
        for asset in assets:
            df = filtered[asset]
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
            open_val = float(row["open"])
            strat = strategies[asset]
            cap = capitals[asset]

            regime_state = regime_detector.detect(df, idx)

            tod_mult = TOD_MULTIPLIERS.get(ts.hour, 1.0) if use_tod_sizing else 1.0

            # === Check pending limit entry fills ===
            if asset in pending_entries and asset not in open_trades:
                pend = pending_entries[asset]
                if entry_opt.check_fill_next_bar(
                    pend["direction"], pend["limit_price"], high_val, low_val
                ):
                    sig = pend["signal"]
                    direction = pend["direction"]
                    fill_price = pend["limit_price"]
                    stop = sig["stop"]
                    target = sig["target"]
                    leverage = sig.get("leverage", 1.0)
                    score = sig.get("score", 0)

                    size = _compute_size(
                        fill_price, stop, cap, max_risk, leverage,
                        regime_state if use_regime_sizing else None,
                        direction, tod_mult,
                        use_pct_sizing, total_equity, margin_in_use, score,
                    )

                    new_trade = Trade(
                        entry_time=ts, entry_price=fill_price,
                        direction=direction, signal=sig.get("signal", ""),
                        stop_price=stop, target_price=target,
                        size_usd=round(size, 2), leverage=leverage,
                        atr_at_entry=sig.get("atr_at_entry"),
                        market_regime=regime_state.regime,
                    )
                    open_trades[asset] = new_trade
                    pyramid_counts[asset] = 0
                    scale_out_done[asset] = False
                    original_sizes[asset] = size
                    margin_in_use += size / leverage if leverage > 0 else size
                    del pending_entries[asset]
                else:
                    del pending_entries[asset]

            # === Check exits ===
            if asset in open_trades:
                trade = open_trades[asset]
                exit_reason = None
                exit_price = None

                # TP always executes immediately
                if trade.direction == "LONG" and high_val >= trade.target_price:
                    exit_reason = "TARGET"
                    exit_price = trade.target_price
                elif trade.direction == "SHORT" and low_val <= trade.target_price:
                    exit_reason = "TARGET"
                    exit_price = trade.target_price

                # V2.6: Wick-resistant stop
                if exit_reason is None:
                    stop_touched = False
                    if trade.direction == "LONG" and low_val <= trade.stop_price:
                        stop_touched = True
                    elif trade.direction == "SHORT" and high_val >= trade.stop_price:
                        stop_touched = True

                    if stop_touched:
                        if use_wick_guard:
                            df_15m = (datasets_15m or {}).get(asset)
                            closes_15m = get_15m_closes_for_hour(df_15m, ts)
                            if not closes_15m:
                                closes_15m = WickGuardBacktest.get_15m_closes_from_hourly(
                                    open_val, high_val, low_val, price
                                )
                            if wick_guard.check_stop(trade.direction, trade.stop_price, closes_15m):
                                exit_reason = "STOP"
                                exit_price = trade.stop_price
                        else:
                            exit_reason = "STOP"
                            exit_price = trade.stop_price

                # V2.6: Scale-out on losers (before strategy exit check)
                if exit_reason is None and use_scale_out and not scale_out_done.get(asset, False):
                    stop_dist = abs(trade.entry_price - trade.stop_price)
                    if stop_dist > 0:
                        if trade.direction == "LONG":
                            adverse = trade.entry_price - low_val
                        else:
                            adverse = high_val - trade.entry_price
                        if adverse >= stop_dist * SCALE_OUT_TRIGGER_PCT:
                            scale_out_done[asset] = True
                            reduce_size = trade.size_usd * SCALE_OUT_REDUCE_PCT
                            trade.size_usd -= reduce_size
                            # Book the partial loss
                            if trade.direction == "LONG":
                                raw = (price - trade.entry_price) / trade.entry_price
                            else:
                                raw = (trade.entry_price - price) / trade.entry_price
                            pnl = reduce_size * (raw - fee_rate * 2)
                            capitals[asset] += pnl
                            total_equity += pnl
                            margin_in_use -= reduce_size / trade.leverage if trade.leverage > 0 else reduce_size
                            margin_in_use = max(0, margin_in_use)

                # Strategy exit (trailing stop, time exit, partial TP)
                if exit_reason is None:
                    sig = strat.check_exit(df, idx, trade)
                    if isinstance(sig, dict) and sig.get("action") == "PARTIAL_TP":
                        ps = sig["partial_size"]
                        pp = sig.get("partial_price", price)
                        if trade.direction == "LONG":
                            raw = (pp - trade.entry_price) / trade.entry_price
                        else:
                            raw = (trade.entry_price - pp) / trade.entry_price
                        capitals[asset] += ps * (raw - fee_rate * 2)
                        total_equity += ps * (raw - fee_rate * 2)
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
                    total_equity += trade.pnl_usd
                    margin_in_use -= trade.size_usd / trade.leverage if trade.leverage > 0 else trade.size_usd
                    margin_in_use = max(0, margin_in_use)
                    all_trades[asset].append(trade)

                    tr = trade.market_regime or "NEUTRAL"
                    if tr in regime_trades[asset]:
                        regime_trades[asset][tr].append(trade)

                    del open_trades[asset]
                    pyramid_counts.pop(asset, None)
                    scale_out_done.pop(asset, None)
                    original_sizes.pop(asset, None)

            # === Pyramiding check (add to winners) ===
            if use_pyramiding and asset in open_trades:
                trade = open_trades[asset]
                pc = pyramid_counts.get(asset, 0)
                if pc < MAX_PYRAMIDS:
                    atr_val = getattr(trade, 'atr_at_entry', None)
                    if atr_val and atr_val > 0:
                        if trade.direction == "LONG":
                            profit_atr = (high_val - trade.entry_price) / atr_val
                        else:
                            profit_atr = (trade.entry_price - low_val) / atr_val
                        if profit_atr >= PYRAMID_PROFIT_ATR:
                            # Check if there's a fresh signal in same direction
                            sig = strat.generate_signal(df, idx)
                            if sig and sig.get("action") == trade.direction:
                                add_size = original_sizes.get(asset, trade.size_usd) * PYRAMID_SIZE_PCT
                                trade.size_usd += add_size
                                trade.stop_price = trade.entry_price  # Move to breakeven
                                pyramid_counts[asset] = pc + 1
                                margin_in_use += add_size / trade.leverage if trade.leverage > 0 else add_size

            # === Generate new signals ===
            if asset not in open_trades and asset not in pending_entries:
                sig = strat.generate_signal(df, idx)
                if sig and sig.get("action") in ("LONG", "SHORT"):
                    direction = sig["action"]
                    stop = sig["stop"]
                    target = sig["target"]
                    leverage = sig.get("leverage", 1.0)
                    atr_val = sig.get("atr_at_entry", 0)
                    score = sig.get("score", 0)

                    # Smart entry: place limit at pullback
                    if use_smart_entries and atr_val and atr_val > 0:
                        limit_price = entry_opt.compute_limit_price(direction, price, atr_val)
                        sig["_limit_price"] = limit_price
                        pending_entries[asset] = {
                            "direction": direction,
                            "limit_price": limit_price,
                            "signal": sig,
                            "bar_idx": idx,
                        }
                        continue

                    # Market entry
                    size = _compute_size(
                        price, stop, cap, max_risk, leverage,
                        regime_state if use_regime_sizing else None,
                        direction, tod_mult,
                        use_pct_sizing, total_equity, margin_in_use, score,
                    )

                    if use_pct_sizing and size < total_equity * 0.03:
                        continue

                    new_trade = Trade(
                        entry_time=ts, entry_price=price,
                        direction=direction, signal=sig.get("signal", ""),
                        stop_price=stop, target_price=target,
                        size_usd=round(size, 2), leverage=leverage,
                        atr_at_entry=atr_val,
                        market_regime=regime_state.regime,
                    )
                    open_trades[asset] = new_trade
                    pyramid_counts[asset] = 0
                    scale_out_done[asset] = False
                    original_sizes[asset] = size
                    margin_in_use += size / leverage if leverage > 0 else size

            # Track drawdown
            peak_caps[asset] = max(peak_caps[asset], capitals[asset])
            dd = (peak_caps[asset] - capitals[asset]) / peak_caps[asset] if peak_caps[asset] > 0 else 0
            max_dds[asset] = max(max_dds[asset], dd)

    # Close remaining open trades
    for asset in list(open_trades.keys()):
        trade = open_trades[asset]
        df = filtered[asset]
        if len(df) == 0:
            continue
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
        final_cap = capitals[asset]
        winners = [t for t in trades if t.pnl_usd > 0]
        losers = [t for t in trades if t.pnl_usd <= 0]
        n = len(trades)
        wr = (len(winners) / n * 100) if n > 0 else 0.0
        pnl = round(final_cap - CAPITAL, 2)
        dd = round(max_dds[asset] * 100, 2)
        gp = sum(t.pnl_usd for t in winners) if winners else 0
        gl = abs(sum(t.pnl_usd for t in losers)) if losers else 1
        pf = round(gp / gl, 2) if gl > 0 else 0.0

        df_f = filtered[asset]
        days = (df_f.index[-1] - df_f.index[0]).days if len(df_f) > 1 else 1
        tday = round(n / max(days, 1), 2)

        pnls = [t.pnl_pct for t in trades]
        if len(pnls) > 1:
            sharpe = round(np.mean(pnls) / np.std(pnls) * np.sqrt(365), 2) if np.std(pnls) > 0 else 0.0
        else:
            sharpe = 0.0

        results[asset] = {
            "trades": n, "wr": wr, "pnl": pnl, "dd": dd, "pf": pf,
            "tday": tday, "sharpe": sharpe, "regime_trades": regime_trades[asset],
        }

    return results


# ============================================================================
# Runner shortcuts
# ============================================================================
def run_v25(datasets, datasets_15m, start_date=None, end_date=None):
    return run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        datasets_15m=datasets_15m,
        start_date=start_date, end_date=end_date,
    )


def run_v26(datasets, datasets_15m, start_date=None, end_date=None):
    return run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        use_wick_guard=True, use_smart_entries=True,
        use_regime_sizing=True, use_pyramiding=True,
        use_scale_out=True, use_tod_sizing=True,
        use_pct_sizing=True,
        datasets_15m=datasets_15m,
        start_date=start_date, end_date=end_date,
    )


# ============================================================================
# Printing helpers
# ============================================================================
def print_results_table(configs_results):
    fmt = "  {:<34s} {:>6s} {:>6s} {:>10s} {:>6s} {:>5s} {:>7s}"
    print(fmt.format("Config", "Trades", "WR", "PnL", "MaxDD", "PF", "Sharpe"))
    print("  " + "-" * 82)
    for label, results in configs_results:
        tt = sum(results[a]["trades"] for a in results)
        wrs = [results[a]["wr"] for a in results if results[a]["trades"] > 0]
        aw = np.mean(wrs) if wrs else 0.0
        tp = sum(results[a]["pnl"] for a in results)
        md = max((results[a]["dd"] for a in results), default=0)
        pfs = [results[a]["pf"] for a in results if results[a]["trades"] > 0]
        ap = np.mean(pfs) if pfs else 0.0
        shs = [results[a]["sharpe"] for a in results if results[a]["trades"] > 0]
        ash = np.mean(shs) if shs else 0.0
        print("  {:<34s} {:>6d} {:>5.1f}% ${:>+8.0f} {:>5.1f}% {:>5.2f} {:>7.2f}".format(
            label, tt, aw, tp, md, ap, ash))


def print_per_asset(results, assets_loaded, label=""):
    if label:
        print(f"\n  Per-asset ({label}):")
    for asset in assets_loaded:
        r = results[asset]
        if r["trades"] > 0:
            print("    {:>4s}: {:>4}t {:>5.1f}%w ${:>+7.0f} DD:{:.1f}% PF:{:.2f} Sh:{:.2f}".format(
                asset, r["trades"], r["wr"], r["pnl"], r["dd"], r["pf"], r["sharpe"]))
        else:
            print(f"    {asset:>4s}: no trades")


def print_regime_breakdown(results, assets):
    print()
    print("  PER-REGIME BREAKDOWN")
    print("  " + "-" * 90)
    fmt = "  {:<8s} {:<15s} {:>6s} {:>6s} {:>10s} {:>10s}"
    print(fmt.format("Asset", "Regime", "Trades", "WR", "Avg PnL%", "Total $"))
    print("  " + "-" * 90)
    for asset in assets:
        rt = results[asset].get("regime_trades", {})
        for regime in ["TRENDING_UP", "TRENDING_DOWN", "RANGING", "NEUTRAL"]:
            trades = rt.get(regime, [])
            if not trades:
                continue
            n = len(trades)
            winners = [t for t in trades if t.pnl_usd > 0]
            wr = len(winners) / n * 100 if n > 0 else 0
            avg_pnl = np.mean([t.pnl_pct for t in trades])
            total = sum(t.pnl_usd for t in trades)
            print("  {:<8s} {:<15s} {:>6d} {:>5.1f}% {:>+9.2f}% ${:>+9.0f}".format(
                asset, regime, n, wr, avg_pnl, total))


def agg_results(results):
    """Aggregate results across assets into a single summary dict."""
    tt = sum(results[a]["trades"] for a in results)
    tp = sum(results[a]["pnl"] for a in results)
    wrs = [results[a]["wr"] for a in results if results[a]["trades"] > 0]
    dds = [results[a]["dd"] for a in results if results[a]["trades"] > 0]
    pfs = [results[a]["pf"] for a in results if results[a]["trades"] > 0]
    shs = [results[a]["sharpe"] for a in results if results[a]["trades"] > 0]
    return {
        "trades": tt, "pnl": tp,
        "wr": np.mean(wrs) if wrs else 0,
        "dd": max(dds) if dds else 0,
        "pf": np.mean(pfs) if pfs else 0,
        "sharpe": np.mean(shs) if shs else 0,
    }


# ============================================================================
# Stress test
# ============================================================================
def run_stress_comparison(datasets, datasets_15m, assets_loaded):
    """V2.5 vs V2.6 across all 17 stress periods."""
    print()
    print("=" * 110)
    print("  STRESS TEST: V2.5 vs V2.6 Across All 17 Market Regimes")
    print("=" * 110)

    all_results = []

    for period_name, start, end, category in STRESS_PERIODS:
        btc = datasets.get("BTC")
        if btc is None:
            continue
        mask = (btc.index >= start) & (btc.index <= end)
        btc_slice = btc[mask]
        if len(btc_slice) < 50:
            continue

        btc_change = ((btc_slice["close"].iloc[-1] / btc_slice["close"].iloc[0]) - 1) * 100
        print(f"\n  --- {period_name} [{category}] ({start} to {end}) ---")
        print(f"  BTC: ${btc_slice['close'].iloc[0]:,.0f} -> ${btc_slice['close'].iloc[-1]:,.0f} ({btc_change:+.1f}%)")
        print(f"  {'Config':<34s} {'Trades':>6} {'WR':>6} {'PnL':>10} {'MaxDD':>6} {'PF':>5}")

        for cfg_name, runner in [("V2.5 baseline", run_v25), ("V2.6 combined", run_v26)]:
            results = runner(datasets, datasets_15m, start_date=start, end_date=end)
            a = agg_results(results)
            result = {
                "period": period_name, "category": category, "config": cfg_name,
                "trades": a["trades"], "win_rate": a["wr"], "pnl": a["pnl"],
                "max_dd": a["dd"], "profit_factor": a["pf"],
            }
            all_results.append(result)
            print(f"  {cfg_name:<34s} {a['trades']:>6} {a['wr']:>5.1f}% ${a['pnl']:>+8,.0f} {a['dd']:>5.1f}% {a['pf']:>5.2f}")

    # Survival summary
    print("\n" + "=" * 110)
    print("  SURVIVAL SUMMARY")
    print("=" * 110)
    for cfg_name in ["V2.5 baseline", "V2.6 combined"]:
        cr = [r for r in all_results if r["config"] == cfg_name]
        if not cr:
            continue
        worst_dd = max(r["max_dd"] for r in cr)
        worst_pnl = min(r["pnl"] for r in cr)
        losing = sum(1 for r in cr if r["pnl"] < 0)
        total = len(cr)
        total_pnl = sum(r["pnl"] for r in cr)
        danger = "DANGER" if worst_dd > 30 else "OK"
        print(f"  {cfg_name:<34s} WorstDD: {worst_dd:.1f}% [{danger}] | WorstPnL: ${worst_pnl:+,.0f} | Losing: {losing}/{total} | TotalPnL: ${total_pnl:+,.0f}")

    return all_results


# ============================================================================
# Main
# ============================================================================
def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    print("Loading data for %d assets..." % len(ASSETS))
    datasets = {}
    datasets_15m = {}
    for sym in ASSETS:
        try:
            datasets[sym] = load_data(sym)
        except FileNotFoundError:
            print(f"  WARNING: {sym}_USD_hourly.csv not found, skipping")
        df_15m = load_15m_data(sym)
        if df_15m is not None:
            datasets_15m[sym] = df_15m

    assets_loaded = list(datasets.keys())
    print(f"  Loaded hourly: {', '.join(assets_loaded)}")
    print(f"  Loaded 15m:    {', '.join(datasets_15m.keys()) or 'none'}")

    sample = datasets[assets_loaded[0]]
    date_range = f"{sample.index[0].date()} to {sample.index[-1].date()}"

    print()
    print("=" * 110)
    print("  CANDLE V2.6 -- REGIME-OPTIMIZED TRADING BACKTEST")
    print(f"  Features: wick guard | smart entries | regime sizing | pyramiding | scale-out | ToD sizing | pct sizing")
    print(f"  {len(assets_loaded)} assets | {date_range}")
    print(f"  Capital: ${CAPITAL:.0f}/asset (${CAPITAL * len(assets_loaded):,.0f} total) | Fee: {FEE_PCT}% taker/side | Max risk: {MAX_RISK_PCT}%")
    print("=" * 110)

    # ================================================================
    # TEST 1: FULL PERIOD -- FEATURE COMPARISON (2022-2026)
    # ================================================================
    print("\n" + "=" * 110)
    print("  TEST 1: FULL PERIOD -- FEATURE COMPARISON (2022-2026)")
    print("=" * 110)

    configs = []

    print("\n  Running 1/7: V2.5 baseline...")
    r_base = run_v25(datasets, datasets_15m)
    configs.append(("V2.5 baseline", r_base))

    print("  Running 2/7: + wick guard...")
    r_wick = run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        use_wick_guard=True, datasets_15m=datasets_15m,
    )
    configs.append(("+ wick guard", r_wick))

    print("  Running 3/7: + smart entries...")
    r_entry = run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        use_smart_entries=True, datasets_15m=datasets_15m,
    )
    configs.append(("+ smart entries", r_entry))

    print("  Running 4/7: + regime sizing...")
    r_regime = run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        use_regime_sizing=True, datasets_15m=datasets_15m,
    )
    configs.append(("+ regime sizing", r_regime))

    print("  Running 5/7: + pyramiding + scale-out...")
    r_pyramid = run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        use_pyramiding=True, use_scale_out=True, datasets_15m=datasets_15m,
    )
    configs.append(("+ pyramid + scale-out", r_pyramid))

    print("  Running 6/7: + ToD + pct sizing...")
    r_tod = run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        use_tod_sizing=True, use_pct_sizing=True, datasets_15m=datasets_15m,
    )
    configs.append(("+ ToD + pct sizing", r_tod))

    print("  Running 7/7: V2.6 combined (all features)...")
    r_v26 = run_v26(datasets, datasets_15m)
    configs.append(("V2.6 COMBINED", r_v26))

    print()
    print("=" * 110)
    print("  TEST 1 RESULTS: FULL PERIOD")
    print("=" * 110)
    print()
    print_results_table(configs)

    # Delta analysis
    print()
    print("  Delta vs V2.5 baseline:")
    base_total = sum(r_base[a]["pnl"] for a in assets_loaded)
    for label, results in configs[1:]:
        total = sum(results[a]["pnl"] for a in assets_loaded)
        delta = total - base_total
        pct = (delta / abs(base_total) * 100) if base_total != 0 else 0
        print(f"    {label:<34s} ${delta:>+8.0f} ({pct:>+.1f}%)")

    print_per_asset(r_v26, assets_loaded, "V2.6 combined")

    # ================================================================
    # TEST 2: YEARLY BREAKDOWN
    # ================================================================
    print("\n" + "=" * 110)
    print("  TEST 2: YEARLY BREAKDOWN -- V2.5 vs V2.6")
    print("=" * 110)

    years = [
        ("2022", "2022-01-01", "2022-12-31"),
        ("2023", "2023-01-01", "2023-12-31"),
        ("2024", "2024-01-01", "2024-12-31"),
        ("2025", "2025-01-01", "2025-12-31"),
        ("2026 YTD", "2026-01-01", "2026-12-31"),
    ]

    yearly_data = []
    fmt = "  {:<10s} | {:<12s} {:>6s} {:>6s} {:>10s} {:>6s} {:>5s} {:>7s}"
    print()
    print(fmt.format("Year", "Config", "Trades", "WR", "PnL", "MaxDD", "PF", "Sharpe"))
    print("  " + "-" * 85)

    for year_label, yr_start, yr_end in years:
        for cfg_name, runner in [("V2.5", run_v25), ("V2.6", run_v26)]:
            results = runner(datasets, datasets_15m, start_date=yr_start, end_date=yr_end)
            a = agg_results(results)
            yearly_data.append({"year": year_label, "config": cfg_name, **a})
            print("  {:<10s} | {:<12s} {:>6d} {:>5.1f}% ${:>+8.0f} {:>5.1f}% {:>5.2f} {:>7.2f}".format(
                year_label, cfg_name, a["trades"], a["wr"], a["pnl"], a["dd"], a["pf"], a["sharpe"]))

    print()
    print("  Yearly V2.6 vs V2.5 delta:")
    for year_label, _, _ in years:
        v25_yr = [r for r in yearly_data if r["year"] == year_label and r["config"] == "V2.5"]
        v26_yr = [r for r in yearly_data if r["year"] == year_label and r["config"] == "V2.6"]
        if v25_yr and v26_yr:
            delta = v26_yr[0]["pnl"] - v25_yr[0]["pnl"]
            print(f"    {year_label:<10s} ${delta:>+8.0f}")

    # ================================================================
    # TEST 3: STRESS TEST (all 17 periods, V2.5 vs V2.6)
    # ================================================================
    run_stress_comparison(datasets, datasets_15m, assets_loaded)

    # ================================================================
    # TEST 4: PER-REGIME BREAKDOWN (V2.6 combined)
    # ================================================================
    print("\n" + "=" * 110)
    print("  TEST 4: PER-REGIME BREAKDOWN (V2.6 combined, full period)")
    print("=" * 110)
    print_regime_breakdown(r_v26, assets_loaded)

    # ================================================================
    # FINAL SUMMARY TABLE
    # ================================================================
    elapsed = time.time() - t_start
    v25 = agg_results(r_base)
    v26 = agg_results(r_v26)
    v25_pnl = v25["pnl"]
    v26_pnl = v26["pnl"]
    total_cap = CAPITAL * len(assets_loaded)

    print()
    print("=" * 110)
    print("  FINAL SUMMARY")
    print("=" * 110)
    print()
    print(f"  {'Metric':<20s} {'V2.5':>12s} {'V2.6':>12s} {'Delta':>12s}")
    print(f"  {'-'*56}")
    print(f"  {'Total PnL':<20s} ${v25_pnl:>+10,.0f} ${v26_pnl:>+10,.0f} ${v26_pnl - v25_pnl:>+10,.0f}")
    print(f"  {'Max Drawdown':<20s} {v25['dd']:>11.1f}% {v26['dd']:>11.1f}% {v26['dd'] - v25['dd']:>+11.1f}%")
    print(f"  {'Total Trades':<20s} {v25['trades']:>12d} {v26['trades']:>12d} {v26['trades'] - v25['trades']:>+12d}")
    print(f"  {'Avg Win Rate':<20s} {v25['wr']:>11.1f}% {v26['wr']:>11.1f}% {v26['wr'] - v25['wr']:>+11.1f}%")
    print(f"  {'Profit Factor':<20s} {v25['pf']:>12.2f} {v26['pf']:>12.2f} {v26['pf'] - v25['pf']:>+12.2f}")
    print(f"  {'Sharpe':<20s} {v25['sharpe']:>12.2f} {v26['sharpe']:>12.2f} {v26['sharpe'] - v25['sharpe']:>+12.2f}")

    roi_v25 = v25_pnl / total_cap * 100
    roi_v26 = v26_pnl / total_cap * 100
    print(f"  {'ROI':<20s} {roi_v25:>11.1f}% {roi_v26:>11.1f}% {roi_v26 - roi_v25:>+11.1f}%")
    print(f"  {'Capital':<20s} {'$' + f'{total_cap:,.0f}':>12s} {'$' + f'{total_cap:,.0f}':>12s}")
    print(f"  {'Fee/side':<20s} {str(FEE_PCT) + '%':>12s} {str(FEE_PCT) + '%':>12s}")

    print()
    print(f"  Runtime: {elapsed:.1f}s")
    print("=" * 110)

    return configs


if __name__ == "__main__":
    main()
