"""Standard stress test periods — run against these for EVERY backtest.

Usage:
    from backtest.stress_periods import STRESS_PERIODS, run_stress_test

These cover crashes, sideways, euphoria, sudden reversals, and full yearly regimes.
Any new backtest config MUST be validated against these before deployment.
"""

# (name, start, end, category)
STRESS_PERIODS = [
    # CRASHES
    ("Luna/UST collapse", "2022-05-01", "2022-06-15", "crash"),
    ("FTX collapse", "2022-11-01", "2022-12-15", "crash"),
    ("2022 bear capitulation", "2022-06-01", "2022-07-31", "crash"),
    ("Aug 2023 flash crash", "2023-08-15", "2023-09-15", "crash"),
    ("Apr 2024 Iran tension", "2024-04-10", "2024-04-25", "crash"),
    ("2024 Aug yen unwind", "2024-08-01", "2024-08-15", "crash"),
    ("2025 tariff crash", "2025-01-15", "2025-03-05", "crash"),

    # BORING SIDEWAYS
    ("2023 Q3 dead zone", "2023-07-01", "2023-09-30", "sideways"),
    ("2024 Q3 summer chop", "2024-07-01", "2024-09-30", "sideways"),
    ("Post-FTX flatline", "2022-12-01", "2023-02-28", "sideways"),

    # EUPHORIA / PUMP
    ("2024 Q4 Trump pump", "2024-10-15", "2024-12-31", "euphoria"),
    ("2023 Q4 ETF rally", "2023-10-01", "2023-12-31", "euphoria"),
    ("Jan 2024 ETF approval", "2024-01-01", "2024-01-31", "euphoria"),

    # FULL YEAR REGIMES
    ("Full bear 2022", "2022-01-01", "2022-12-31", "yearly"),
    ("Recovery 2023", "2023-01-01", "2023-12-31", "yearly"),
    ("Bull 2024", "2024-01-01", "2024-12-31", "yearly"),
    ("Uncertain 2025-26", "2025-01-01", "2026-03-05", "yearly"),
]


def run_stress_test(datasets, config_dict, common_params, engine_class, strategy_class,
                    initial_capital=500, fee_pct=0.15, max_risk_pct=2.0):
    """Run stress test across all periods for multiple configs.

    Args:
        datasets: dict of {symbol: DataFrame}
        config_dict: dict of {config_name: {param overrides}}
        common_params: dict of shared strategy params
        engine_class: BacktestEngine class
        strategy_class: Strategy class (e.g. CandleV2_3)
        initial_capital: per-asset starting capital
        fee_pct: fee percentage
        max_risk_pct: max risk per trade

    Returns:
        list of result dicts, also prints formatted table
    """
    import numpy as np

    all_results = []
    symbols = list(datasets.keys())

    print("=" * 100)
    print("  STRESS TEST: Strategy Performance Across Market Regimes")
    print("=" * 100)

    for period_name, start, end, category in STRESS_PERIODS:
        # Check if we have enough data
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
        print(f"  {'Config':<28s} {'Trades':>6} {'WR':>6} {'PnL':>10} {'MaxDD':>6} {'PF':>5}")

        for cfg_name, cfg in config_dict.items():
            tt = 0
            tp = 0.0
            wrs = []
            dds = []
            pfs = []

            for sym in symbols:
                sliced = datasets[sym][(datasets[sym].index >= start) & (datasets[sym].index <= end)]
                if len(sliced) < 50:
                    continue
                engine = engine_class(initial_capital=initial_capital, fee_pct=fee_pct, max_risk_pct=max_risk_pct)
                strat = strategy_class(**{**common_params, **cfg})
                r = engine.run(sliced, strat, sym)
                tt += r.total_trades
                tp += r.total_pnl_usd
                if r.total_trades > 0:
                    wrs.append(r.win_rate)
                    dds.append(r.max_drawdown_pct)
                    pfs.append(r.profit_factor)

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
            print(f"  {cfg_name:<28s} {tt:>6} {aw:>5.1f}% ${tp:>+8,.0f} {md:>5.1f}% {ap:>5.2f}")

    # Summary
    print("\n" + "=" * 100)
    print("  SURVIVAL SUMMARY")
    print("=" * 100)

    for cfg_name in config_dict:
        cfg_results = [r for r in all_results if r['config'] == cfg_name]
        worst_dd = max(r['max_dd'] for r in cfg_results) if cfg_results else 0
        worst_pnl = min(r['pnl'] for r in cfg_results) if cfg_results else 0
        losing_periods = sum(1 for r in cfg_results if r['pnl'] < 0)
        total_periods = len(cfg_results)
        danger = "DANGER" if worst_dd > 30 else "OK"
        print(f"  {cfg_name:<28s} WorstDD: {worst_dd:.1f}% [{danger}] | WorstPnL: ${worst_pnl:+,.0f} | Losing periods: {losing_periods}/{total_periods}")

    return all_results
