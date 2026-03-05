"""
V13 Cross-Asset Backtest — Regime Detection + Kelly Criterion.

Tests V13 on all 4 assets (BTC, ETH, SOL, LINK) with:
- Per-asset results
- Regime breakdown
- Long vs short breakdown
- OOS validation (60/40 split)
- Comparison to V10 baseline
- Kelly fraction evolution
"""

import sys
import time
import os
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, BacktestResult, Trade, print_result
from strategies.squeeze_v13 import SqueezeV13
from strategies.squeeze_v10 import SqueezeV10


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def mo(result, data):
    """Monthly return from backtest result."""
    years = (data.index[-1] - data.index[0]).days / 365.25
    if years <= 0:
        return 0
    return result.total_pnl_pct / (years * 12)


class V13Engine(BacktestEngine):
    """Extended engine that feeds trade results back to strategy for Kelly updates."""

    def run(self, data, strategy, name="unnamed"):
        result = super().run(data, strategy, name)
        # Feed completed trades back to strategy for Kelly
        for t in result.trades:
            strategy.record_trade(t.direction, t.pnl_pct)
        return result

    def run_with_kelly_feedback(self, data, strategy, name="unnamed"):
        """Run with real-time Kelly feedback (trades fed back during backtest)."""
        capital = self.initial_capital
        peak_capital = capital
        max_drawdown = 0
        trades = []
        equity_curve = [capital]
        open_trade = None
        daily_pnls = []
        kelly_history = []  # track Kelly evolution

        data = data.copy()
        data.columns = [c.lower() for c in data.columns]

        for i in range(20, len(data)):
            row = data.iloc[i]
            price = float(row["close"])
            high = float(row["high"])
            low = float(row["low"])

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
                    if sig:
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

                    # Feed trade back for Kelly updates
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

                    # Track Kelly
                    kelly_history.append({
                        "bar": i,
                        "time": data.index[i],
                        "leverage": sig_leverage,
                        "direction": direction,
                    })

            equity_curve.append(capital)
            peak_capital = max(peak_capital, capital)
            drawdown = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)

        # Close remaining
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


def run_v10_baseline(data, lev=5.0, name="V10"):
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strat = SqueezeV10(fixed_leverage=lev)
    return engine.run(data, strat, name)


def main():
    os.makedirs("results", exist_ok=True)
    t_start = time.time()

    ASSETS = ["BTC", "ETH", "SOL", "LINK"]
    datasets = {sym: load_data(sym) for sym in ASSETS}

    btc_data = datasets["BTC"]
    sample = datasets["SOL"]
    total_bars = len(sample)
    date_range = f"{sample.index[0].date()} to {sample.index[-1].date()}"

    print("=" * 90)
    print("  V13 CROSS-ASSET BACKTEST — Regime Detection + Kelly Criterion")
    print(f"  {total_bars} bars/asset | {date_range}")
    print(f"  Target: ALL 4 assets positive, avg 2%+/mo, max DD <25%")
    print("=" * 90)

    # =====================================================
    # 1. Per-asset V13 results with Kelly feedback
    # =====================================================
    print("\n" + "=" * 90)
    print("  [1] V13 PER-ASSET RESULTS (Kelly feedback enabled)")
    print("=" * 90)

    v13_results = {}
    v13_kelly = {}
    v13_strats = {}

    for sym in ASSETS:
        data = datasets[sym]
        engine = V13Engine(initial_capital=1000, fee_pct=0.045, max_risk_pct=8.0)
        strat = SqueezeV13(btc_data=btc_data, asset_name=sym)
        if sym != "BTC":
            strat.set_btc_data(btc_data)

        result, kelly_hist = engine.run_with_kelly_feedback(data, strat, f"V13 {sym}")
        monthly = mo(result, data)
        v13_results[sym] = (result, monthly)
        v13_kelly[sym] = kelly_hist
        v13_strats[sym] = strat

        status = "+" if monthly > 0 else "-"
        print(f"\n  {'[OK]' if monthly > 0 else '[!!]'} {sym}: {result.total_trades}t "
              f"{result.win_rate:.0f}%w ${result.total_pnl_usd:+,.0f} "
              f"({monthly:+.2f}%/mo) DD:{result.max_drawdown_pct:.1f}% "
              f"PF:{result.profit_factor:.2f} Sharpe:{result.sharpe_ratio:.2f}")

    # Summary
    print(f"\n  {'─' * 50}")
    all_monthly = [v13_results[s][1] for s in ASSETS]
    all_positive = all(m > 0 for m in all_monthly)
    avg_monthly = np.mean(all_monthly)
    print(f"  Average: {avg_monthly:+.2f}%/mo | All positive: {all_positive}")
    print(f"  Per-asset: {', '.join(f'{s}:{v13_results[s][1]:+.2f}' for s in ASSETS)}")

    # =====================================================
    # 2. Regime breakdown per asset
    # =====================================================
    print("\n" + "=" * 90)
    print("  [2] REGIME BREAKDOWN PER ASSET")
    print("=" * 90)

    for sym in ASSETS:
        strat = v13_strats[sym]
        result = v13_results[sym][0]

        print(f"\n  {sym}:")
        print(f"    Regime trade counts: {strat.regime_counts}")

        # Per-regime PnL
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
    # 3. Long vs Short breakdown
    # =====================================================
    print("\n" + "=" * 90)
    print("  [3] LONG vs SHORT BREAKDOWN")
    print("=" * 90)

    for sym in ASSETS:
        result = v13_results[sym][0]
        longs = [t for t in result.trades if t.direction == "LONG"]
        shorts = [t for t in result.trades if t.direction == "SHORT"]
        l_pnl = sum(t.pnl_usd for t in longs)
        s_pnl = sum(t.pnl_usd for t in shorts)
        l_wr = sum(1 for t in longs if t.pnl_usd > 0) / len(longs) * 100 if longs else 0
        s_wr = sum(1 for t in shorts if t.pnl_usd > 0) / len(shorts) * 100 if shorts else 0
        print(f"  {sym}: Longs {len(longs)}t {l_wr:.0f}%w ${l_pnl:+,.0f} | "
              f"Shorts {len(shorts)}t {s_wr:.0f}%w ${s_pnl:+,.0f}")

    # =====================================================
    # 4. OOS Validation (60/40)
    # =====================================================
    print("\n" + "=" * 90)
    print("  [4] OUT-OF-SAMPLE VALIDATION (60/40 split)")
    print("=" * 90)

    for sym in ASSETS:
        data = datasets[sym]
        split = int(len(data) * 0.6)
        train_data = data.iloc[:split]
        test_data = data.iloc[split:]

        # Train
        engine_tr = V13Engine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat_tr = SqueezeV13(btc_data=btc_data, asset_name=sym)
        if sym != "BTC":
            strat_tr.set_btc_data(btc_data)
        r_tr, _ = engine_tr.run_with_kelly_feedback(train_data, strat_tr, f"{sym} Train")
        m_tr = mo(r_tr, train_data)

        # Test (fresh strategy)
        engine_te = V13Engine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
        strat_te = SqueezeV13(btc_data=btc_data, asset_name=sym)
        if sym != "BTC":
            strat_te.set_btc_data(btc_data)
        r_te, _ = engine_te.run_with_kelly_feedback(test_data, strat_te, f"{sym} Test")
        m_te = mo(r_te, test_data)

        oos_ok = m_te > 0
        print(f"  {sym}: Train {r_tr.total_trades}t ({m_tr:+.2f}%/mo) DD:{r_tr.max_drawdown_pct:.1f}% | "
              f"Test {r_te.total_trades}t ({m_te:+.2f}%/mo) DD:{r_te.max_drawdown_pct:.1f}% "
              f"{'[OOS OK]' if oos_ok else '[OOS FAIL]'}")

    # =====================================================
    # 5. V10 Baseline Comparison
    # =====================================================
    print("\n" + "=" * 90)
    print("  [5] V10 vs V13 COMPARISON (5x leverage for V10)")
    print("=" * 90)

    print(f"\n  {'Asset':<6} {'V10 %/mo':>10} {'V13 %/mo':>10} {'Delta':>10} {'V13 DD':>8}")
    for sym in ASSETS:
        data = datasets[sym]
        r10 = run_v10_baseline(data, 5.0, f"V10 {sym}")
        m10 = mo(r10, data)
        m13 = v13_results[sym][1]
        dd13 = v13_results[sym][0].max_drawdown_pct
        delta = m13 - m10
        better = "+" if delta > 0 else ""
        print(f"  {sym:<6} {m10:+10.2f} {m13:+10.2f} {better}{delta:9.2f} {dd13:7.1f}%")

    # =====================================================
    # 6. Kelly fraction evolution
    # =====================================================
    print("\n" + "=" * 90)
    print("  [6] KELLY LEVERAGE EVOLUTION (first/last 10 trades per asset)")
    print("=" * 90)

    for sym in ASSETS:
        kelly_hist = v13_kelly[sym]
        if not kelly_hist:
            print(f"  {sym}: no trades")
            continue
        first5 = kelly_hist[:10]
        last5 = kelly_hist[-10:]
        avg_first = np.mean([k["leverage"] for k in first5])
        avg_last = np.mean([k["leverage"] for k in last5])
        avg_all = np.mean([k["leverage"] for k in kelly_hist])
        print(f"  {sym}: first10 avg lev={avg_first:.2f}x | last10 avg lev={avg_last:.2f}x | "
              f"overall avg={avg_all:.2f}x | total trades={len(kelly_hist)}")

    # =====================================================
    # Summary
    # =====================================================
    print("\n" + "=" * 90)
    print("  FINAL SUMMARY")
    print("=" * 90)

    for sym in ASSETS:
        r, m = v13_results[sym]
        status = "[OK]" if m > 0 else "[!!]"
        print(f"  {status} {sym}: {m:+.2f}%/mo | {r.total_trades}t {r.win_rate:.0f}%w "
              f"DD:{r.max_drawdown_pct:.1f}% PF:{r.profit_factor:.2f}")

    print(f"\n  Average monthly: {avg_monthly:+.2f}%/mo")
    print(f"  All positive: {'YES' if all_positive else 'NO'}")
    max_dd = max(v13_results[s][0].max_drawdown_pct for s in ASSETS)
    print(f"  Max DD across assets: {max_dd:.1f}%")

    targets_met = all_positive and avg_monthly >= 2.0 and max_dd < 25.0
    print(f"\n  TARGET MET: {'YES' if targets_met else 'NO'}")
    if not all_positive:
        losers = [s for s in ASSETS if v13_results[s][1] <= 0]
        print(f"  Negative assets: {losers}")

    elapsed = time.time() - t_start
    print(f"\n  Runtime: {elapsed:.1f}s")

    return v13_results, all_positive, avg_monthly


if __name__ == "__main__":
    main()
