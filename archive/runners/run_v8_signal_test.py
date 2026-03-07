"""
V8 Signal Discovery — Test each new signal type on BTC, ETH, SOL independently.
Find which signals work on which assets, then combine the winners.
"""

import sys
import time
import os
import pandas as pd
import numpy as np
sys.path.insert(0, ".")

from backtest.engine import BacktestEngine, BacktestResult
from strategies.squeeze_only_v7 import SqueezeOnlyV7
from strategies.signals.donchian import DonchianBreakout
from strategies.signals.keltner import KeltnerBreakout
from strategies.signals.market_structure import MarketStructureBreak
from strategies.signals.adx_di import ADXSystem
from strategies.signals.vwap_deviation import VWAPMomentum
from strategies.signals.obv_divergence import OBVMomentum


def load_data(symbol):
    fp = f"data/{symbol}_USD_hourly.csv"
    df = pd.read_csv(fp, index_col=0, parse_dates=True)
    df.columns = [c.lower() for c in df.columns]
    return df


def eval_result(r: BacktestResult, years: float) -> dict:
    months = years * 12
    monthly = r.total_pnl_pct / months if months > 0 else 0
    return {
        "trades": r.total_trades,
        "win_rate": r.win_rate,
        "monthly_pct": monthly,
        "drawdown": r.max_drawdown_pct,
        "pf": r.profit_factor,
        "sharpe": r.sharpe_ratio,
        "pnl_usd": r.total_pnl_usd,
        "pass": monthly >= 5.0 and r.max_drawdown_pct <= 35.0,
    }


def test_signal(name, strategy_cls, asset, data, years, **kwargs):
    engine = BacktestEngine(initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0)
    strat = strategy_cls(**kwargs)
    t0 = time.time()
    result = engine.run(data, strat, f"{name} {asset}")
    elapsed = time.time() - t0
    ev = eval_result(result, years)
    status = "✅ PASS" if ev["pass"] else ("🟡" if ev["monthly_pct"] > 2 else "❌")
    print(f"  {status} {name:28s} {asset}: "
          f"{ev['trades']:3d}t  {ev['win_rate']:4.0f}%w  "
          f"{ev['monthly_pct']:+6.2f}%/mo  DD:{ev['drawdown']:4.1f}%  "
          f"PF:{ev['pf']:.2f}  ({elapsed:.1f}s)")
    return ev, result


def main():
    os.makedirs("results", exist_ok=True)

    assets = {
        "BTC": load_data("BTC"),
        "ETH": load_data("ETH"),
        "SOL": load_data("SOL"),
    }

    years_map = {}
    for sym, df in assets.items():
        years_map[sym] = (df.index[-1] - df.index[0]).days / 365.25

    print("=" * 90)
    print("  V8 Signal Discovery — Testing each signal type on BTC, ETH, SOL")
    print(f"  Data: BTC {len(assets['BTC'])} | ETH {len(assets['ETH'])} | SOL {len(assets['SOL'])} hourly candles")
    print(f"  Period: {assets['SOL'].index[0].date()} to {assets['SOL'].index[-1].date()}")
    print(f"  Target: ≥5%/mo with ≤35% drawdown")
    print("=" * 90)

    signals = [
        ("V7-Squeeze (baseline)", SqueezeOnlyV7, {}),
        ("Donchian-20",          DonchianBreakout, {"entry_period": 20}),
        ("Donchian-55",          DonchianBreakout, {"entry_period": 55}),
        ("Keltner-2ATR",         KeltnerBreakout,  {"atr_mult": 2.0}),
        ("Keltner-1.5ATR",       KeltnerBreakout,  {"atr_mult": 1.5}),
        ("MarketStructure-10",   MarketStructureBreak, {"swing_lookback": 10}),
        ("MarketStructure-20",   MarketStructureBreak, {"swing_lookback": 20}),
        ("ADX-DI-22",            ADXSystem,        {"adx_min": 22}),
        ("ADX-DI-28",            ADXSystem,        {"adx_min": 28}),
        ("VWAP-Momentum-24h",    VWAPMomentum,     {"vwap_period": 24}),
        ("VWAP-Momentum-48h",    VWAPMomentum,     {"vwap_period": 48}),
        ("OBV-Momentum-21",      OBVMomentum,      {"obv_ema_period": 21}),
        ("OBV-Momentum-50",      OBVMomentum,      {"obv_ema_period": 50}),
    ]

    all_results = {}

    for sig_name, sig_cls, sig_kwargs in signals:
        print(f"\n--- {sig_name} ---")
        asset_results = {}
        for sym in ["BTC", "ETH", "SOL"]:
            ev, result = test_signal(sig_name, sig_cls, sym,
                                      assets[sym], years_map[sym], **sig_kwargs)
            asset_results[sym] = (ev, result)
        all_results[sig_name] = asset_results

    # Summary table
    print("\n" + "=" * 90)
    print("  SUMMARY — Monthly Returns by Signal × Asset")
    print("=" * 90)
    print(f"  {'Signal':35s} {'BTC':>12s} {'ETH':>12s} {'SOL':>12s}  Best")
    print(f"  {'-'*35} {'-'*12} {'-'*12} {'-'*12}  ----")

    winners = []
    for sig_name, asset_results in all_results.items():
        row = f"  {sig_name:35s}"
        best = -999
        for sym in ["BTC", "ETH", "SOL"]:
            ev = asset_results[sym][0]
            m = ev["monthly_pct"]
            dd = ev["drawdown"]
            marker = "✅" if ev["pass"] else ("🟡" if m > 2 else "  ")
            row += f" {marker}{m:+5.2f}%({dd:.0f}%dd)"
            if m > best:
                best = m
                best_sym = sym
        row += f"  {best_sym}: {best:+.2f}%/mo"
        print(row)

        # Track candidates (>2%/mo on any asset)
        for sym in ["BTC", "ETH", "SOL"]:
            ev = asset_results[sym][0]
            if ev["monthly_pct"] > 2.0 and ev["drawdown"] < 50:
                winners.append({
                    "signal": sig_name,
                    "asset": sym,
                    "monthly": ev["monthly_pct"],
                    "drawdown": ev["drawdown"],
                    "trades": ev["trades"],
                    "win_rate": ev["win_rate"],
                    "pf": ev["pf"],
                    "cls": sig_cls,
                    "kwargs": sig_kwargs,
                })

    print("\n" + "=" * 90)
    print("  WINNING SIGNALS (>2%/mo, <50% drawdown):")
    winners.sort(key=lambda x: x["monthly"], reverse=True)
    for w in winners[:20]:
        print(f"  {w['signal']:35s} {w['asset']:5s}: "
              f"{w['monthly']:+6.2f}%/mo  DD:{w['drawdown']:.1f}%  "
              f"{w['trades']}t  {w['win_rate']:.0f}%w  PF:{w['pf']:.2f}")

    # Log to file
    log_entry = f"""
## V8 Signal Discovery Run
Date: 2026-03-05

### Results by Signal × Asset

| Signal | BTC %/mo | ETH %/mo | SOL %/mo |
|--------|----------|----------|----------|
"""
    for sig_name, asset_results in all_results.items():
        btc_m = asset_results["BTC"][0]["monthly_pct"]
        eth_m = asset_results["ETH"][0]["monthly_pct"]
        sol_m = asset_results["SOL"][0]["monthly_pct"]
        log_entry += f"| {sig_name} | {btc_m:+.2f}% | {eth_m:+.2f}% | {sol_m:+.2f}% |\n"

    log_entry += "\n### Winning Signals (>2%/mo)\n\n"
    for w in winners[:20]:
        log_entry += f"- {w['signal']} on {w['asset']}: {w['monthly']:+.2f}%/mo, {w['drawdown']:.1f}% DD\n"

    with open("results/iteration_log.md", "a") as f:
        f.write(log_entry)

    print(f"\n  Results saved to results/iteration_log.md")
    return winners, all_results


if __name__ == "__main__":
    winners, all_results = main()
