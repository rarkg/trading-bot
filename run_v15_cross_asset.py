"""
V15 Cross-Asset Backtest — Hybrid Adaptive Strategy.

Tests V15 on all 4 assets with:
- Per-asset results with adaptive parameters
- Regime breakdown
- V14 vs V15 comparison
- OOS validation (60/40 split)
- Parameter evolution summary (which params changed most per asset)
"""

import sys
import time
import os
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, BacktestResult, Trade
from strategies.squeeze_v15 import SqueezeV15
from strategies.squeeze_v14 import SqueezeV14


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


class V15Engine(BacktestEngine):

    def run_with_kelly_feedback(self, data, strategy, name="unnamed", cross_momentum=None):
        capital = self.initial_capital
        peak_capital = capital
        max_drawdown = 0
        trades = []
        equity_curve = [capital]
        open_trade = None
        daily_pnls = []
        kelly_history = []

        data = data.copy()
        data.columns = [c.lower() for c in data.columns]

        for i in range(20, len(data)):
            row = data.iloc[i]
            price = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])

            if cross_momentum is not None and hasattr(strategy, 'set_cross_asset_momentum'):
                ts = data.index[i]
                if ts in cross_momentum.index:
                    strategy.set_cross_asset_momentum(cross_momentum[ts])
            if hasattr(strategy, 'update_equity'):
                strategy.update_equity(capital)

            if open_trade:
                exit_reason = None
                exit_price = None

                if open_trade.direction == "LONG":
                    if low <= open_trade.stop_price:
                        exit_reason = "STOP"
                        exit_price = open_trade.stop_price
                    elif high >= open_trade.target_price:
                        exit_reason = "TARGET"
                        exit_price = open_trade.target_price
                elif open_trade.direction == "SHORT":
                    if high >= open_trade.stop_price:
                        exit_reason = "STOP"
                        exit_price = open_trade.stop_price
                    elif low <= open_trade.target_price:
                        exit_reason = "TARGET"
                        exit_price = open_trade.target_price

                if not exit_reason:
                    sig = strategy.check_exit(data, i, open_trade)
                    if isinstance(sig, str) and sig.startswith("PYRAMID_"):
                        pct = int(sig.split("_")[1]) / 100.0
                        add_size = open_trade.size_usd * pct
                        max_add = capital * self.max_risk_pct * 2
                        add_size = min(add_size, max_add)
                        open_trade.size_usd = round(open_trade.size_usd + add_size, 2)
                        atr_now = float(data.iloc[i]["high"] - data.iloc[i]["low"])
                        if open_trade.direction == "LONG":
                            be_stop = open_trade.entry_price + atr_now * 0.5
                            if be_stop > open_trade.stop_price:
                                open_trade.stop_price = be_stop
                                strategy._trailing_stop = be_stop
                        else:
                            be_stop = open_trade.entry_price - atr_now * 0.5
                            if be_stop < open_trade.stop_price:
                                open_trade.stop_price = be_stop
                                strategy._trailing_stop = be_stop
                    elif sig:
                        exit_reason = sig
                        exit_price = price

                if exit_reason:
                    open_trade.exit_time = data.index[i]
                    open_trade.exit_price = exit_price
                    open_trade.exit_reason = exit_reason

                    if open_trade.direction == "LONG":
                        raw_pnl_pct = (exit_price - open_trade.entry_price) / open_trade.entry_price
                    else:
                        raw_pnl_pct = (open_trade.entry_price - exit_price) / open_trade.entry_price

                    pnl_pct = raw_pnl_pct - (self.fee_pct * 2)
                    pnl_usd = open_trade.size_usd * pnl_pct

                    open_trade.pnl_pct = round(pnl_pct * 100, 2)
                    open_trade.pnl_usd = round(pnl_usd, 2)

                    capital += pnl_usd
                    trades.append(open_trade)
                    daily_pnls.append(pnl_pct)

                    strategy.record_trade(open_trade.direction, open_trade.pnl_pct)
                    open_trade = None

            if not open_trade:
                signal = strategy.generate_signal(data, i)

                if signal and signal.get("action") in ("LONG", "SHORT"):
                    direction = signal["action"]
                    stop = signal.get("stop", 0)
                    target = signal.get("target", 0)
                    sig_leverage = signal.get("leverage", 1.0)

                    risk_per_unit = abs(price - stop) if stop else price * 0.02
                    margin = min(capital * 0.4, capital)
                    size = margin * sig_leverage
                    max_loss = capital * self.max_risk_pct
                    loss_per_unit = risk_per_unit * (size / price) if price > 0 else max_loss
                    if loss_per_unit > max_loss and risk_per_unit > 0:
                        size = max_loss / (risk_per_unit / price)
                    size = min(size, capital * sig_leverage)

                    open_trade = Trade(
                        entry_time=data.index[i],
                        entry_price=price,
                        direction=direction,
                        signal=signal.get("signal", ""),
                        stop_price=stop,
                        target_price=target,
                        size_usd=round(size, 2),
                    )

                    kelly_history.append({
                        "bar": i, "time": data.index[i],
                        "leverage": sig_leverage, "direction": direction,
                    })

            equity_curve.append(capital)
            peak_capital = max(peak_capital, capital)
            drawdown = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)

        if open_trade:
            last_price = float(data.iloc[-1]["close"])
            open_trade.exit_time = data.index[-1]
            open_trade.exit_price = last_price
            open_trade.exit_reason = "END_OF_DATA"
            if open_trade.direction == "LONG":
                pnl_pct = (last_price - open_trade.entry_price) / open_trade.entry_price - (self.fee_pct * 2)
            else:
                pnl_pct = (open_trade.entry_price - last_price) / open_trade.entry_price - (self.fee_pct * 2)
            open_trade.pnl_pct = round(pnl_pct * 100, 2)
            open_trade.pnl_usd = round(open_trade.size_usd * pnl_pct, 2)
            capital += open_trade.pnl_usd
            trades.append(open_trade)
            strategy.record_trade(open_trade.direction, open_trade.pnl_pct)

        result = BacktestResult(
            strategy_name=name,
            period=f"{data.index[0].date()} to {data.index[-1].date()}",
            total_trades=len(trades),
            trades=trades,
            equity_curve=equity_curve,
        )

        if trades:
            winners = [t for t in trades if t.pnl_usd > 0]
            losers = [t for t in trades if t.pnl_usd <= 0]
            result.wins = len(winners)
            result.losses = len(losers)
            result.win_rate = len(winners) / len(trades) * 100
            result.avg_win_pct = np.mean([t.pnl_pct for t in winners]) if winners else 0
            result.avg_loss_pct = np.mean([t.pnl_pct for t in losers]) if losers else 0
            result.total_pnl_usd = round(capital - self.initial_capital, 2)
            result.total_pnl_pct = round((capital - self.initial_capital) / self.initial_capital * 100, 2)
            result.max_drawdown_pct = round(max_drawdown * 100, 2)

            gross_profit = sum(t.pnl_usd for t in winners) if winners else 0
            gross_loss = abs(sum(t.pnl_usd for t in losers)) if losers else 1
            result.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

            if daily_pnls:
                avg_ret = np.mean(daily_pnls)
                std_ret = np.std(daily_pnls) if len(daily_pnls) > 1 else 1
                result.sharpe_ratio = round(avg_ret / std_ret * np.sqrt(365), 2) if std_ret > 0 else 0

            durations = []
            for t in trades:
                if t.exit_time and t.entry_time:
                    durations.append((t.exit_time - t.entry_time).total_seconds() / 3600)
            result.avg_trade_duration_hours = round(np.mean(durations), 1) if durations else 0

        return result, kelly_history


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    ASSETS = ["BTC", "ETH", "SOL", "LINK"]
    datasets = {sym: load_data(sym) for sym in ASSETS}

    btc_data = datasets["BTC"]
    sample = datasets["SOL"]
    total_bars = len(sample)
    date_range = f"{sample.index[0].date()} to {sample.index[-1].date()}"

    ASSET_MAX_RISK = {"BTC": 42.0, "ETH": 16.0, "SOL": 8.0, "LINK": 13.0}  # V15.2: ETH 10.5→16, LINK 11→13

    print("=" * 90)
    print("  V15 CROSS-ASSET BACKTEST — Hybrid Adaptive Strategy")
    print(f"  {total_bars} bars/asset | {date_range}")
    print(f"  Fee: 0.10% | Target: ALL 4 assets 10%+/mo, max DD <25%")
    print("=" * 90)

    # Cross-asset momentum (same as V14)
    asset_trends = {}
    for sym in ASSETS:
        closes = datasets[sym]["close"].astype(float)
        ema_fast = closes.ewm(span=192, adjust=False).mean()
        ema_slow = closes.ewm(span=504, adjust=False).mean()
        asset_trends[sym] = (ema_fast > ema_slow).astype(int)

    common_idx = datasets["BTC"].index
    cross_momentum = pd.Series(0.0, index=common_idx)
    for idx in common_idx:
        bull_count = 0
        for sym in ASSETS:
            if idx in asset_trends[sym].index:
                bull_count += asset_trends[sym].get(idx, 0)
        cross_momentum[idx] = (bull_count / len(ASSETS)) * 2 - 1

    # =====================================================
    # 1. V15 Per-Asset Results
    # =====================================================
    print("\n" + "=" * 90)
    print("  [1] V15 PER-ASSET RESULTS (Adaptive Parameters)")
    print("=" * 90)

    v15_results = {}
    v15_strats = {}

    for sym in ASSETS:
        data = datasets[sym]
        max_risk = ASSET_MAX_RISK.get(sym, 8.0)
        engine = V15Engine(initial_capital=1000, fee_pct=0.10, max_risk_pct=max_risk)
        strat = SqueezeV15(btc_data=btc_data, asset_name=sym)
        if sym != "BTC":
            strat.set_btc_data(btc_data)

        result, kelly_hist = engine.run_with_kelly_feedback(data, strat, f"V15 {sym}",
                                                            cross_momentum=cross_momentum)
        monthly = mo(result, data)
        v15_results[sym] = (result, monthly)
        v15_strats[sym] = strat

        target_hit = monthly >= 10.0
        print(f"\n  {'[OK]' if target_hit else '[!!]'} {sym}: {result.total_trades}t "
              f"{result.win_rate:.0f}%w ${result.total_pnl_usd:+,.0f} "
              f"({monthly:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}% "
              f"PF:{result.profit_factor:.2f} Sharpe:{result.sharpe_ratio:.2f}")

    print(f"\n  {'─' * 50}")
    all_monthly = [v15_results[s][1] for s in ASSETS]
    all_above_10 = all(m >= 10.0 for m in all_monthly)
    avg_monthly = np.mean(all_monthly)
    print(f"  Average: {avg_monthly:+.2f}%/mo | All 10%+: {all_above_10}")
    print(f"  Per-asset: {', '.join(f'{s}:{v15_results[s][1]:+.2f}' for s in ASSETS)}")

    # =====================================================
    # 2. Regime Breakdown
    # =====================================================
    print("\n" + "=" * 90)
    print("  [2] REGIME BREAKDOWN PER ASSET")
    print("=" * 90)

    for sym in ASSETS:
        strat = v15_strats[sym]
        result = v15_results[sym][0]

        print(f"\n  {sym}:")
        print(f"    Regime trade counts: {strat.regime_counts}")

        if strat.trade_regimes and result.trades:
            regime_pnl = {}
            for idx_t, trade in enumerate(result.trades):
                if idx_t < len(strat.trade_regimes):
                    regime, direction, stype = strat.trade_regimes[idx_t]
                    key = f"{regime}/{stype}"
                    if key not in regime_pnl:
                        regime_pnl[key] = {"count": 0, "pnl": 0, "wins": 0}
                    regime_pnl[key]["count"] += 1
                    regime_pnl[key]["pnl"] += trade.pnl_usd
                    if trade.pnl_usd > 0:
                        regime_pnl[key]["wins"] += 1

            for key, stats in sorted(regime_pnl.items()):
                wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
                print(f"    {key:25s}: {stats['count']:3d}t {wr:.0f}%w ${stats['pnl']:+,.0f}")

    # =====================================================
    # 3. V14 vs V15 Comparison
    # =====================================================
    print("\n" + "=" * 90)
    print("  [3] V14 vs V15 COMPARISON")
    print("=" * 90)

    print(f"\n  {'Asset':<6} {'V14 %/mo':>10} {'V15 %/mo':>10} {'Delta':>10} {'V15 DD':>8}")
    for sym in ASSETS:
        data = datasets[sym]
        max_risk = ASSET_MAX_RISK.get(sym, 8.0)
        engine14 = V15Engine(initial_capital=1000, fee_pct=0.10, max_risk_pct=max_risk)
        strat14 = SqueezeV14(btc_data=btc_data, asset_name=sym)
        if sym != "BTC":
            strat14.set_btc_data(btc_data)
        r14, _ = engine14.run_with_kelly_feedback(data, strat14, f"V14 {sym}",
                                                   cross_momentum=cross_momentum)
        m14 = mo(r14, data)
        m15 = v15_results[sym][1]
        dd15 = v15_results[sym][0].max_drawdown_pct
        delta = m15 - m14
        better = "+" if delta > 0 else ""
        print(f"  {sym:<6} {m14:+10.2f} {m15:+10.2f} {better}{delta:9.2f} {dd15:7.1f}%")

    # =====================================================
    # 4. OOS Validation
    # =====================================================
    print("\n" + "=" * 90)
    print("  [4] OUT-OF-SAMPLE VALIDATION (60/40 split)")
    print("=" * 90)

    for sym in ASSETS:
        data = datasets[sym]
        split = int(len(data) * 0.6)
        train_data = data.iloc[:split]
        test_data = data.iloc[split:]

        engine_tr = V15Engine(initial_capital=1000, fee_pct=0.10, max_risk_pct=ASSET_MAX_RISK.get(sym, 8.0))
        strat_tr = SqueezeV15(btc_data=btc_data, asset_name=sym)
        if sym != "BTC":
            strat_tr.set_btc_data(btc_data)
        r_tr, _ = engine_tr.run_with_kelly_feedback(train_data, strat_tr, f"{sym} Train")
        m_tr = mo(r_tr, train_data)

        engine_te = V15Engine(initial_capital=1000, fee_pct=0.10, max_risk_pct=ASSET_MAX_RISK.get(sym, 8.0))
        strat_te = SqueezeV15(btc_data=btc_data, asset_name=sym)
        if sym != "BTC":
            strat_te.set_btc_data(btc_data)
        r_te, _ = engine_te.run_with_kelly_feedback(test_data, strat_te, f"{sym} Test")
        m_te = mo(r_te, test_data)

        oos_ok = m_te > 0
        print(f"  {sym}: Train {r_tr.total_trades}t ({m_tr:+.2f}%/mo) DD:{r_tr.max_drawdown_pct:.1f}% | "
              f"Test {r_te.total_trades}t ({m_te:+.2f}%/mo) DD:{r_te.max_drawdown_pct:.1f}% "
              f"{'[OOS OK]' if oos_ok else '[OOS FAIL]'}")

    # =====================================================
    # 5. Parameter Evolution Summary
    # =====================================================
    print("\n" + "=" * 90)
    print("  [5] PARAMETER EVOLUTION (Adaptive Changes)")
    print("=" * 90)

    for sym in ASSETS:
        strat = v15_strats[sym]
        evolution = strat.apm.get_param_evolution()
        total_changes = sum(v["count"] for v in evolution.values())
        print(f"\n  {sym}: {total_changes} total parameter changes")

        if evolution:
            sorted_params = sorted(evolution.items(), key=lambda x: x[1]["count"], reverse=True)
            for param_name, info in sorted_params[:8]:
                last_val = info["values"][-1][2] if info["values"] else "?"
                first_val = info["values"][0][1] if info["values"] else "?"
                print(f"    {param_name:25s}: {info['count']:3d} changes  "
                      f"{first_val} -> {last_val}")

    # =====================================================
    # 6. Final Summary
    # =====================================================
    print("\n" + "=" * 90)
    print("  FINAL SUMMARY")
    print("=" * 90)

    for sym in ASSETS:
        r, m = v15_results[sym]
        target_hit = m >= 10.0
        status = "[OK]" if target_hit else "[!!]"
        print(f"  {status} {sym}: {m:+.2f}%/mo | {r.total_trades}t {r.win_rate:.0f}%w "
              f"DD:{r.max_drawdown_pct:.1f}% PF:{r.profit_factor:.2f}")

    print(f"\n  Average monthly: {avg_monthly:+.2f}%/mo")
    print(f"  All 10%+/mo: {'YES' if all_above_10 else 'NO'}")
    max_dd = max(v15_results[s][0].max_drawdown_pct for s in ASSETS)
    print(f"  Max DD across assets: {max_dd:.1f}%")

    targets_met = all_above_10 and max_dd < 25.0
    print(f"\n  TARGET MET: {'YES' if targets_met else 'NO'}")
    if not all_above_10:
        below = [s for s in ASSETS if v15_results[s][1] < 10.0]
        print(f"  Below 10%: {', '.join(f'{s}:{v15_results[s][1]:+.2f}' for s in below)}")

    elapsed = time.time() - t_start
    print(f"\n  Runtime: {elapsed:.1f}s")

    return v15_results, all_above_10, avg_monthly


if __name__ == "__main__":
    main()
