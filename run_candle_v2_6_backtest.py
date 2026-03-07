"""
Candle V2.6 Backtest — Wick-resistant stops, smart entries, regime sizing.

Runs 4 comparison tests:
  1. Full backtest: V2.5 baseline vs each V2.6 feature vs V2.6 combined
  2. Stress test: all 17 stress periods
  3. YTD 2026 (2026-01-01 to present)
  4. Per-regime breakdown table
"""

import sys
import time
import os
import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Callable

sys.path.insert(0, ".")

from backtest.engine import Trade, BacktestEngine
from strategies.candle_v2_3 import CandleV2_3
from live.adaptive_sizer import AdaptiveSizer
from live.regime import RegimeDetector
from live.wick_guard import WickGuardBacktest
from live.entry_optimizer import EntryOptimizerBacktest
from backtest.stress_periods import STRESS_PERIODS, run_stress_test


ASSETS = ["BTC", "ETH", "SOL", "LINK", "ADA", "AVAX", "DOGE", "XRP", "ICP", "SHIB"]

CAPITAL = 400.0
FEE_PCT = 0.15
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
    """Get 15m close prices within a given hour."""
    if df_15m is None:
        return []
    hour_end = hour_ts + pd.Timedelta(hours=1)
    mask = (df_15m.index >= hour_ts) & (df_15m.index < hour_end)
    bars = df_15m[mask]
    if len(bars) == 0:
        return []
    return bars["close"].values.tolist()


def run_multi_asset(
    datasets,
    strategy_factory,
    use_wick_guard=False,
    use_smart_entries=False,
    use_regime_sizing=False,
    datasets_15m=None,
    start_date=None,
    end_date=None,
):
    """Run synchronized multi-asset backtest with V2.6 features."""

    fee_rate = FEE_PCT / 100
    max_risk = MAX_RISK_PCT / 100
    assets = list(datasets.keys())

    strategies = {a: strategy_factory() for a in assets}
    capitals = {a: CAPITAL for a in assets}
    open_trades = {}  # type: Dict[str, Trade]
    all_trades = {a: [] for a in assets}
    peak_caps = {a: CAPITAL for a in assets}
    max_dds = {a: 0.0 for a in assets}

    # V2.6 components
    wick_guard = WickGuardBacktest(enabled=use_wick_guard)
    entry_opt = EntryOptimizerBacktest(enabled=use_smart_entries, pullback_atr=0.3)
    regime_detector = RegimeDetector(enabled=use_regime_sizing)

    # Pending limit entries: asset -> (limit_price, direction, signal, entry_bar_idx)
    pending_entries = {}

    # Per-regime trade tracking
    regime_trades = {a: {"TRENDING_UP": [], "TRENDING_DOWN": [], "RANGING": [], "NEUTRAL": []} for a in assets}

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

            # Detect regime for this bar
            regime_state = regime_detector.detect(df, idx)

            # === Check pending limit entry fills ===
            if asset in pending_entries and asset not in open_trades:
                pend = pending_entries[asset]
                if entry_opt.check_fill_next_bar(
                    pend["direction"], pend["limit_price"], high_val, low_val
                ):
                    # Filled! Create trade at limit price
                    sig = pend["signal"]
                    direction = pend["direction"]
                    fill_price = pend["limit_price"]
                    stop = sig["stop"]
                    target = sig["target"]
                    leverage = sig.get("leverage", 1.0)

                    # Recalculate size at fill price
                    risk_per_unit = abs(fill_price - stop) if stop else fill_price * 0.02
                    margin = min(cap * 0.4, cap)
                    size = margin * leverage
                    max_loss = cap * max_risk
                    if fill_price > 0:
                        loss = risk_per_unit * (size / fill_price)
                    else:
                        loss = max_loss
                    if loss > max_loss and risk_per_unit > 0:
                        size = max_loss / (risk_per_unit / fill_price)
                    size = min(size, cap * leverage)

                    # V2.6: Regime sizing
                    if use_regime_sizing:
                        rsm = regime_state.regime_size_multiplier(direction)
                        size *= rsm

                    new_trade = Trade(
                        entry_time=ts,
                        entry_price=fill_price,
                        direction=direction,
                        signal=sig.get("signal", ""),
                        stop_price=stop,
                        target_price=target,
                        size_usd=round(size, 2),
                        leverage=leverage,
                        atr_at_entry=sig.get("atr_at_entry"),
                        market_regime=regime_state.regime,
                    )
                    open_trades[asset] = new_trade
                    del pending_entries[asset]
                else:
                    # Not filled — entry expired after 1 bar
                    del pending_entries[asset]

            # === Check exits ===
            if asset in open_trades:
                trade = open_trades[asset]
                exit_reason = None
                exit_price = None

                # TP always executes immediately
                if trade.direction == "LONG":
                    if high_val >= trade.target_price:
                        exit_reason = "TARGET"
                        exit_price = trade.target_price
                else:
                    if low_val <= trade.target_price:
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
                            # Get 15m closes for this hour
                            df_15m = (datasets_15m or {}).get(asset)
                            closes_15m = get_15m_closes_for_hour(df_15m, ts)
                            if not closes_15m:
                                # No 15m data — simulate from hourly
                                closes_15m = WickGuardBacktest.get_15m_closes_from_hourly(
                                    open_val, high_val, low_val, price
                                )
                            if wick_guard.check_stop(trade.direction, trade.stop_price, closes_15m):
                                exit_reason = "STOP"
                                exit_price = trade.stop_price
                            # else: wick touch but no 15m close beyond stop — survive
                        else:
                            exit_reason = "STOP"
                            exit_price = trade.stop_price

                # Strategy exit (trailing stop, time exit)
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

                    # Track by regime
                    tr = trade.market_regime or "NEUTRAL"
                    if tr in regime_trades[asset]:
                        regime_trades[asset][tr].append(trade)

                    del open_trades[asset]

            # === Generate signals ===
            if asset not in open_trades and asset not in pending_entries:
                regime_adj = None
                if regime_detector.enabled:
                    _rs = regime_state
                    regime_adj = lambda d, rs=_rs: regime_detector.get_score_adjustment(rs, d)

                sig = strat.generate_signal(df, idx, regime_score_adj=regime_adj)
                if sig and sig.get("action") in ("LONG", "SHORT"):
                    direction = sig["action"]
                    stop = sig["stop"]
                    target = sig["target"]
                    leverage = sig.get("leverage", 1.0)
                    atr_val = sig.get("atr_at_entry", 0)

                    # V2.6: Smart entry — place limit at pullback
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

                    # V2.6: Regime sizing
                    if use_regime_sizing:
                        rsm = regime_state.regime_size_multiplier(direction)
                        size *= rsm

                    new_trade = Trade(
                        entry_time=ts,
                        entry_price=price,
                        direction=direction,
                        signal=sig.get("signal", ""),
                        stop_price=stop,
                        target_price=target,
                        size_usd=round(size, 2),
                        leverage=leverage,
                        atr_at_entry=atr_val,
                        market_regime=regime_state.regime,
                    )
                    open_trades[asset] = new_trade

            # Track drawdown
            peak_caps[asset] = max(peak_caps[asset], capitals[asset])
            dd = (peak_caps[asset] - capitals[asset]) / peak_caps[asset] if peak_caps[asset] > 0 else 0
            max_dds[asset] = max(max_dds[asset], dd)

    # Close remaining open trades
    for asset in list(open_trades.keys()):
        trade = open_trades[asset]
        df = filtered[asset]
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

        days = (filtered[asset].index[-1] - filtered[asset].index[0]).days if len(filtered[asset]) > 1 else 1
        tday = round(n / days, 2) if days > 0 else 0.0

        pnls = [t.pnl_pct for t in trades]
        if len(pnls) > 1:
            sharpe = round(np.mean(pnls) / np.std(pnls) * np.sqrt(365), 2) if np.std(pnls) > 0 else 0.0
        else:
            sharpe = 0.0

        results[asset] = {
            "trades": n,
            "wr": wr,
            "pnl": pnl,
            "dd": dd,
            "pf": pf,
            "tday": tday,
            "sharpe": sharpe,
            "regime_trades": regime_trades[asset],
        }

    return results


def print_results_table(configs_results):
    fmt = "  {:<32s} {:>6s} {:>6s} {:>10s} {:>6s} {:>5s} {:>7s}"
    print(fmt.format("Config", "Trades", "WR", "PnL", "MaxDD", "PF", "Sharpe"))
    print("  " + "-" * 80)

    for label, results in configs_results:
        total_trades = sum(results[a]["trades"] for a in results)
        wrs = [results[a]["wr"] for a in results if results[a]["trades"] > 0]
        avg_wr = np.mean(wrs) if wrs else 0.0
        total_pnl = sum(results[a]["pnl"] for a in results)
        max_dd = max(results[a]["dd"] for a in results)
        pfs = [results[a]["pf"] for a in results if results[a]["trades"] > 0]
        avg_pf = np.mean(pfs) if pfs else 0.0
        sharpes = [results[a]["sharpe"] for a in results if results[a]["trades"] > 0]
        avg_sharpe = np.mean(sharpes) if sharpes else 0.0

        print("  {:<32s} {:>6d} {:>5.1f}% ${:>+8.0f} {:>5.1f}% {:>5.2f} {:>7.2f}".format(
            label, total_trades, avg_wr, total_pnl, max_dd, avg_pf, avg_sharpe))


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


def run_stress_v26(datasets, datasets_15m, config_dict, common_params):
    """Run V2.6 stress test across all periods."""
    print()
    print("=" * 100)
    print("  STRESS TEST: V2.6 Performance Across Market Regimes")
    print("=" * 100)

    all_results = []
    symbols = list(datasets.keys())

    for period_name, start, end, category in STRESS_PERIODS:
        btc = datasets.get('BTC')
        if btc is None:
            continue
        mask = (btc.index >= start) & (btc.index <= end)
        btc_slice = btc[mask]
        if len(btc_slice) < 50:
            continue

        btc_change = ((btc_slice['close'].iloc[-1] / btc_slice['close'].iloc[0]) - 1) * 100
        print(f"\n--- {period_name} [{category}] ({start} to {end}) ---")
        print(f"  BTC: ${btc_slice['close'].iloc[0]:,.0f} -> ${btc_slice['close'].iloc[-1]:,.0f} ({btc_change:+.1f}%)")
        print(f"  {'Config':<32s} {'Trades':>6} {'WR':>6} {'PnL':>10} {'MaxDD':>6} {'PF':>5}")

        for cfg_name, cfg_flags in config_dict.items():
            results = run_multi_asset(
                datasets,
                lambda p=common_params: CandleV2_3(**p),
                use_wick_guard=cfg_flags.get("wick_guard", False),
                use_smart_entries=cfg_flags.get("smart_entries", False),
                use_regime_sizing=cfg_flags.get("regime_sizing", False),
                datasets_15m=datasets_15m,
                start_date=start,
                end_date=end,
            )

            tt = sum(results[a]["trades"] for a in results)
            tp = sum(results[a]["pnl"] for a in results)
            wrs = [results[a]["wr"] for a in results if results[a]["trades"] > 0]
            dds = [results[a]["dd"] for a in results if results[a]["trades"] > 0]
            pfs = [results[a]["pf"] for a in results if results[a]["trades"] > 0]
            aw = np.mean(wrs) if wrs else 0
            md = max(dds) if dds else 0
            ap = np.mean(pfs) if pfs else 0

            result = {
                'period': period_name,
                'category': category,
                'config': cfg_name,
                'trades': tt,
                'win_rate': aw,
                'pnl': tp,
                'max_dd': md,
                'profit_factor': ap,
            }
            all_results.append(result)
            print(f"  {cfg_name:<32s} {tt:>6} {aw:>5.1f}% ${tp:>+8,.0f} {md:>5.1f}% {ap:>5.2f}")

    # Survival summary
    print("\n" + "=" * 100)
    print("  SURVIVAL SUMMARY")
    print("=" * 100)
    for cfg_name in config_dict:
        cfg_results = [r for r in all_results if r['config'] == cfg_name]
        if not cfg_results:
            continue
        worst_dd = max(r['max_dd'] for r in cfg_results)
        worst_pnl = min(r['pnl'] for r in cfg_results)
        losing = sum(1 for r in cfg_results if r['pnl'] < 0)
        total = len(cfg_results)
        danger = "DANGER" if worst_dd > 30 else "OK"
        print(f"  {cfg_name:<32s} WorstDD: {worst_dd:.1f}% [{danger}] | WorstPnL: ${worst_pnl:+,.0f} | Losing: {losing}/{total}")

    return all_results


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
    print(f"  Loaded: {', '.join(assets_loaded)}")
    print(f"  15m data: {', '.join(datasets_15m.keys()) or 'none'}")

    sample = datasets[assets_loaded[0]]
    date_range = f"{sample.index[0].date()} to {sample.index[-1].date()}"

    print()
    print("=" * 100)
    print("  CANDLE V2.6 BACKTEST")
    print(f"  Wick-resistant stops | Smart entries | Regime sizing")
    print(f"  {len(assets_loaded)} assets | {date_range}")
    print(f"  Capital: ${CAPITAL:.0f}/asset | Fee: {FEE_PCT}% | Max risk: {MAX_RISK_PCT}%")
    print("=" * 100)

    configs = []

    # ===== TEST 1: Full backtest — V2.5 vs each feature vs combined =====
    print("\n" + "=" * 100)
    print("  TEST 1: FULL PERIOD — FEATURE COMPARISON")
    print("=" * 100)

    # 1. V2.5 baseline
    print("\n  Running 1/5: V2.5 baseline...")
    r_base = run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        datasets_15m=datasets_15m,
    )
    configs.append(("V2.5 baseline", r_base))

    # 2. + Wick guard only
    print("  Running 2/5: + wick guard...")
    r_wick = run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        use_wick_guard=True,
        datasets_15m=datasets_15m,
    )
    configs.append(("+ wick guard", r_wick))

    # 3. + Smart entries only
    print("  Running 3/5: + smart entries...")
    r_entry = run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        use_smart_entries=True,
        datasets_15m=datasets_15m,
    )
    configs.append(("+ smart entries", r_entry))

    # 4. + Regime sizing only
    print("  Running 4/5: + regime sizing...")
    r_regime = run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        use_regime_sizing=True,
        datasets_15m=datasets_15m,
    )
    configs.append(("+ regime sizing", r_regime))

    # 5. All V2.6 combined
    print("  Running 5/5: V2.6 combined...")
    r_v26 = run_multi_asset(
        datasets, lambda: CandleV2_3(**V25_PARAMS),
        use_wick_guard=True,
        use_smart_entries=True,
        use_regime_sizing=True,
        datasets_15m=datasets_15m,
    )
    configs.append(("V2.6 combined", r_v26))

    print()
    print("=" * 100)
    print("  TEST 1: RESULTS COMPARISON")
    print("=" * 100)
    print()
    print_results_table(configs)

    # Per-asset detail for combined V2.6
    print()
    print("  Per-asset detail (V2.6 combined):")
    for asset in assets_loaded:
        r = r_v26[asset]
        print("    {:>4s}: {:>4}t {:>5.1f}%w ${:>+7.0f} DD:{:.1f}% PF:{:.2f} Sh:{:.2f}".format(
            asset, r["trades"], r["wr"], r["pnl"], r["dd"], r["pf"], r["sharpe"]))

    # Delta analysis
    print()
    print("  Delta vs V2.5 baseline:")
    base_total = sum(r_base[a]["pnl"] for a in assets_loaded)
    for label, results in configs[1:]:
        total = sum(results[a]["pnl"] for a in assets_loaded)
        delta = total - base_total
        pct = (delta / abs(base_total) * 100) if base_total != 0 else 0
        print(f"    {label:<32s} ${delta:>+8.0f} ({pct:>+.1f}%)")

    # ===== TEST 2: Stress test =====
    stress_configs = {
        "V2.5 baseline": {"wick_guard": False, "smart_entries": False, "regime_sizing": False},
        "V2.6 combined": {"wick_guard": True, "smart_entries": True, "regime_sizing": True},
    }
    run_stress_v26(datasets, datasets_15m, stress_configs, V25_PARAMS)

    # ===== TEST 3: YTD 2026 =====
    print()
    print("=" * 100)
    print("  TEST 3: YTD 2026 (2026-01-01 to present)")
    print("=" * 100)

    ytd_configs = []
    for label, flags in [("V2.5 baseline", {}),
                         ("V2.6 combined", {"use_wick_guard": True, "use_smart_entries": True, "use_regime_sizing": True})]:
        r = run_multi_asset(
            datasets, lambda: CandleV2_3(**V25_PARAMS),
            datasets_15m=datasets_15m,
            start_date="2026-01-01",
            **flags,
        )
        ytd_configs.append((label, r))

    print()
    print_results_table(ytd_configs)

    # Per-asset YTD
    for label, results in ytd_configs:
        print(f"\n  {label} per-asset YTD:")
        for asset in assets_loaded:
            r = results[asset]
            if r["trades"] > 0:
                print("    {:>4s}: {:>4}t {:>5.1f}%w ${:>+7.0f} DD:{:.1f}%".format(
                    asset, r["trades"], r["wr"], r["pnl"], r["dd"]))

    # ===== TEST 4: Per-regime breakdown =====
    print()
    print("=" * 100)
    print("  TEST 4: PER-REGIME BREAKDOWN (V2.6 combined)")
    print("=" * 100)
    print_regime_breakdown(r_v26, assets_loaded)

    # Summary
    elapsed = time.time() - t_start
    print()
    print("=" * 100)
    v25_pnl = sum(r_base[a]["pnl"] for a in assets_loaded)
    v26_pnl = sum(r_v26[a]["pnl"] for a in assets_loaded)
    v25_dd = max(r_base[a]["dd"] for a in assets_loaded)
    v26_dd = max(r_v26[a]["dd"] for a in assets_loaded)
    print(f"  SUMMARY: V2.5 ${v25_pnl:+,.0f} (DD {v25_dd:.1f}%) -> V2.6 ${v26_pnl:+,.0f} (DD {v26_dd:.1f}%)")
    print(f"  Delta: ${v26_pnl - v25_pnl:+,.0f}")
    print(f"  Runtime: {elapsed:.1f}s")
    print("=" * 100)

    return configs


if __name__ == "__main__":
    main()
