"""
Candle V2.5 Cross-Asset Backtest — Adaptive Kelly Sizing + Regime Detection.

Compares four combos vs V2.4 baseline:
  1. V2.4 baseline (trailing stop only)
  2. + Adaptive sizing
  3. + Regime detection
  4. + Both (adaptive + regime)

Simulates cold start honestly: first 20 trades per bucket use fixed sizing.
"""

import sys
import time
import os
import pandas as pd
import numpy as np
from typing import Optional, Dict, List, Callable

sys.path.insert(0, ".")

from backtest.engine import Trade
from strategies.candle_v2_3 import CandleV2_3
from live.adaptive_sizer import AdaptiveSizer
from live.regime import RegimeDetector


ASSETS = ["BTC", "ETH", "SOL", "LINK", "ADA", "AVAX", "DOGE", "XRP", "ICP", "SHIB"]

CAPITAL = 400.0
FEE_PCT = 0.15
MAX_RISK_PCT = 2.0

# V2.4 baseline: trailing stop enabled
BASE_PARAMS = dict(
    use_rsi=True, use_stoch_rsi=True, use_williams_r=True, use_macd=True,
    use_cci=True, use_ema_alignment=True, use_adx=True, use_bb=True,
    use_atr_percentile=True, use_keltner=True, use_volume=True, use_mfi=True,
    use_obv_slope=True, use_range_position=True, use_hh_ll=True,
    use_mtf=True, mtf_require="both",
    min_score=2.0, stop_atr=2.0, target_atr=3.0,
    cooldown=12, time_exit_bars=144, base_leverage=2.0,
    # V2.4 trailing stop (our baseline)
    use_trailing_stop=True,
    trail_activation_atr=1.5,
    trail_distance_atr=0.5,
)


def load_data(symbol):
    # type: (str) -> pd.DataFrame
    fp = "data/{}_USD_hourly.csv".format(symbol)
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def run_multi_asset(
    datasets,           # type: Dict[str, pd.DataFrame]
    strategy_factory,   # type: Callable[[], CandleV2_3]
    use_adaptive,       # type: bool
    use_regime,         # type: bool
):
    # type: (...) -> Dict[str, dict]
    """Run synchronized multi-asset backtest with optional V2.5 features."""

    fee_rate = FEE_PCT / 100
    max_risk = MAX_RISK_PCT / 100
    assets = list(datasets.keys())

    strategies = {a: strategy_factory() for a in assets}
    capitals = {a: CAPITAL for a in assets}
    open_trades = {}  # type: Dict[str, Trade]
    all_trades = {a: [] for a in assets}  # type: Dict[str, List[Trade]]
    peak_caps = {a: CAPITAL for a in assets}
    max_dds = {a: 0.0 for a in assets}

    # V2.5 components (shared across assets, like live)
    adaptive_sizer = AdaptiveSizer(enabled=use_adaptive)
    regime_detector = RegimeDetector(enabled=use_regime)

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

                    # Feed completed trade to adaptive sizer
                    adaptive_sizer.record_trade(trade.direction, pnl_pct)

            # === Generate signals ===
            if asset not in open_trades:
                # Regime detection for score adjustment
                regime_state = regime_detector.detect(df, idx)
                regime_adj = None  # type: Optional[Callable]
                if regime_detector.enabled:
                    # Capture regime_state in closure
                    _rs = regime_state
                    regime_adj = lambda d, rs=_rs: regime_detector.get_score_adjustment(rs, d)

                sig = strat.generate_signal(df, idx, regime_score_adj=regime_adj)
                if sig and sig.get("action") in ("LONG", "SHORT"):
                    direction = sig["action"]
                    stop = sig["stop"]
                    target = sig["target"]
                    leverage = sig.get("leverage", 1.0)

                    # V2.5 sizing multipliers
                    adaptive_mult = adaptive_sizer.get_multiplier(direction)
                    regime_dir_mult = regime_state.direction_multiplier(direction)
                    vol_mult = regime_state.volatility_multiplier
                    combined_mult = adaptive_mult * regime_dir_mult * vol_mult
                    combined_mult = max(0.2, min(2.5, combined_mult))

                    adjusted_cap = cap * combined_mult

                    risk_per_unit = abs(price - stop) if stop else price * 0.02
                    margin = min(adjusted_cap * 0.4, adjusted_cap)
                    size = margin * leverage
                    max_loss = cap * max_risk  # risk based on actual capital
                    if price > 0:
                        loss = risk_per_unit * (size / price)
                    else:
                        loss = max_loss
                    if loss > max_loss and risk_per_unit > 0:
                        size = max_loss / (risk_per_unit / price)
                    size = min(size, adjusted_cap * leverage)

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
        adaptive_sizer.record_trade(trade.direction, pnl_pct)

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

        days = (datasets[asset].index[-1] - datasets[asset].index[0]).days
        tday = round(n / days, 2) if days > 0 else 0.0

        # Sharpe (per-trade)
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
        }

    return results


def print_results_table(configs_results):
    # type: (List) -> None
    """Print the comparison table."""
    fmt = "  {:<28s} {:>6s} {:>6s} {:>10s} {:>6s} {:>5s} {:>7s}"
    print(fmt.format("Config", "Trades", "WR", "PnL", "MaxDD", "PF", "Sharpe"))
    print("  " + "-" * 75)

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

        print("  {:<28s} {:>6d} {:>5.1f}% ${:>+8.0f} {:>5.1f}% {:>5.2f} {:>7.2f}".format(
            label, total_trades, avg_wr, total_pnl, max_dd, avg_pf, avg_sharpe))


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    print("Loading data for %d assets..." % len(ASSETS))
    datasets = {}  # type: Dict[str, pd.DataFrame]
    for sym in ASSETS:
        try:
            datasets[sym] = load_data(sym)
        except FileNotFoundError:
            print("  WARNING: {}_USD_hourly.csv not found, skipping".format(sym))
    assets_loaded = list(datasets.keys())
    print("  Loaded: {}".format(", ".join(assets_loaded)))

    sample = datasets[assets_loaded[0]]
    date_range = "{} to {}".format(sample.index[0].date(), sample.index[-1].date())

    print()
    print("=" * 80)
    print("  CANDLE V2.5 — ADAPTIVE SIZING + REGIME DETECTION BACKTEST")
    print("  {} assets | {}".format(len(assets_loaded), date_range))
    print("  Capital: ${:.0f}/asset | Fee: {}% | Max risk: {}%".format(CAPITAL, FEE_PCT, MAX_RISK_PCT))
    print("  Baseline: V2.4 (trailing stop 1.5/0.5 ATR)")
    print("=" * 80)

    configs = []  # type: List

    # 1. Baseline: V2.4 (trailing stop only, no V2.5 features)
    print("\n  Running 1/4: V2.4 baseline...")
    r_base = run_multi_asset(
        datasets, lambda: CandleV2_3(**BASE_PARAMS),
        use_adaptive=False, use_regime=False,
    )
    configs.append(("V2.4 baseline (trailing)", r_base))

    # 2. + Adaptive sizing only
    print("  Running 2/4: + adaptive sizing...")
    r_adapt = run_multi_asset(
        datasets, lambda: CandleV2_3(**BASE_PARAMS),
        use_adaptive=True, use_regime=False,
    )
    configs.append(("+ adaptive sizing", r_adapt))

    # 3. + Regime detection only
    print("  Running 3/4: + regime detection...")
    r_regime = run_multi_asset(
        datasets, lambda: CandleV2_3(**BASE_PARAMS),
        use_adaptive=False, use_regime=True,
    )
    configs.append(("+ regime detection", r_regime))

    # 4. + Both
    print("  Running 4/4: + adaptive + regime...")
    r_both = run_multi_asset(
        datasets, lambda: CandleV2_3(**BASE_PARAMS),
        use_adaptive=True, use_regime=True,
    )
    configs.append(("+ adaptive + regime (V2.5)", r_both))

    # Print comparison table
    print()
    print("=" * 80)
    print("  RESULTS COMPARISON")
    print("=" * 80)
    print()
    print_results_table(configs)

    # Per-asset detail for each config
    for label, results in configs:
        print()
        print("  Per-asset detail ({}):".format(label))
        for asset in assets_loaded:
            r = results[asset]
            print("    {:>4s}: {:>4}t {:>5.1f}%w ${:>+7.0f} DD:{:.1f}% PF:{:.2f} Sh:{:.2f}".format(
                asset, r["trades"], r["wr"], r["pnl"], r["dd"], r["pf"], r["sharpe"]))

    # Delta analysis
    print()
    print("  Delta vs V2.4 baseline:")
    base_total = sum(r_base[a]["pnl"] for a in assets_loaded)
    for label, results in configs[1:]:
        total = sum(results[a]["pnl"] for a in assets_loaded)
        delta = total - base_total
        pct = (delta / abs(base_total) * 100) if base_total != 0 else 0
        print("    {:<28s} ${:>+8.0f} ({:>+.1f}%)".format(label, delta, pct))

    elapsed = time.time() - t_start
    print("\n  Runtime: {:.1f}s".format(elapsed))

    return configs


if __name__ == "__main__":
    main()
