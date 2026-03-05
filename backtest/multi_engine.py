"""
Multi-Asset Portfolio Backtest Engine.
Runs multiple strategies on multiple assets simultaneously,
allocating capital across all open positions.

Key difference from single engine:
- Multiple positions can be open at once (one per asset)
- Capital is split across positions
- Portfolio-level drawdown tracking
- Compounding across all positions
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Dict, List
import sys
sys.path.insert(0, ".")
from backtest.engine import Trade, BacktestResult


class MultiAssetEngine:
    def __init__(self, initial_capital=1000, fee_pct=0.045, max_risk_pct=5.0,
                 max_open_positions=3, capital_per_slot=0.33):
        """
        Args:
            initial_capital: Total starting balance
            fee_pct: Fee per side (0.045%)
            max_risk_pct: Max risk per trade as % of total capital
            max_open_positions: How many positions can be open simultaneously
            capital_per_slot: Fraction of capital allocated to each slot
        """
        self.initial_capital = initial_capital
        self.fee_pct = fee_pct / 100
        self.max_risk_pct = max_risk_pct / 100
        self.max_open_positions = max_open_positions
        self.capital_per_slot = capital_per_slot

    def run(self, asset_data: Dict[str, pd.DataFrame],
            asset_strategies: Dict[str, object],
            name="portfolio") -> BacktestResult:
        """
        Run multi-asset backtest.

        Args:
            asset_data: {symbol: DataFrame} of OHLCV data
            asset_strategies: {symbol: strategy_object}
            name: Portfolio name
        """
        # Normalize all data
        normalized = {}
        for sym, df in asset_data.items():
            d = df.copy()
            d.columns = [c.lower() for c in d.columns]
            normalized[sym] = d

        # Align on common index
        common_index = None
        for sym, df in normalized.items():
            if common_index is None:
                common_index = df.index
            else:
                common_index = common_index.intersection(df.index)

        for sym in normalized:
            normalized[sym] = normalized[sym].loc[common_index]

        # Precompute all indicators
        for sym, strat in asset_strategies.items():
            if sym in normalized:
                strat._precompute(normalized[sym])

        capital = self.initial_capital
        peak_capital = capital
        max_drawdown = 0
        all_trades = []
        equity_curve = [capital]

        # Track open position per asset
        open_trades: Dict[str, Optional[Trade]] = {sym: None for sym in asset_strategies}
        # Track last exit per asset for strategy state
        asset_trailing = {sym: {} for sym in asset_strategies}

        n = len(common_index)
        warmup = 1400  # For daily EMA warmup

        for i in range(warmup, n):
            slot_capital = capital / self.max_open_positions
            open_count = sum(1 for t in open_trades.values() if t is not None)

            # --- Exit checks on all open positions ---
            for sym, open_trade in list(open_trades.items()):
                if open_trade is None:
                    continue

                data = normalized[sym]
                strat = asset_strategies[sym]
                row = data.iloc[i]
                price = float(row["close"])
                high = float(row["high"])
                low = float(row["low"])

                exit_reason = None
                exit_price = None

                # Stop/target hits
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

                # Strategy exit signal
                if not exit_reason:
                    sig = strat.check_exit(data, i, open_trade)
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
                    all_trades.append(open_trade)
                    open_trades[sym] = None

            # --- Entry checks for all assets without open position ---
            open_count = sum(1 for t in open_trades.values() if t is not None)

            for sym, open_trade in open_trades.items():
                if open_trade is not None:
                    continue
                if open_count >= self.max_open_positions:
                    break

                data = normalized[sym]
                strat = asset_strategies[sym]
                row = data.iloc[i]
                price = float(row["close"])

                signal = strat.generate_signal(data, i)

                if signal and signal.get("action") in ("LONG", "SHORT"):
                    direction = signal["action"]
                    stop = signal.get("stop", 0)
                    target = signal.get("target", 0)
                    sig_leverage = signal.get("leverage", 1.0)

                    # Allocate from available capital per slot
                    alloc_capital = slot_capital
                    margin = min(alloc_capital * 0.4, alloc_capital)
                    size = margin * sig_leverage

                    # Cap by max risk
                    risk_per_unit = abs(price - stop) if stop else price * 0.02
                    max_loss = capital * self.max_risk_pct
                    loss_per_unit = risk_per_unit * (size / price) if price > 0 else max_loss
                    if loss_per_unit > max_loss and risk_per_unit > 0:
                        size = max_loss / (risk_per_unit / price)

                    size = min(size, capital * sig_leverage)

                    trade = Trade(
                        entry_time=data.index[i],
                        entry_price=price,
                        direction=direction,
                        signal=f"{sym}:{signal.get('signal', '')}",
                        stop_price=stop,
                        target_price=target,
                        size_usd=round(size, 2),
                    )
                    open_trades[sym] = trade
                    open_count += 1

            # Track equity
            equity_curve.append(capital)
            peak_capital = max(peak_capital, capital)
            drawdown = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)

        # Close any remaining trades
        for sym, open_trade in open_trades.items():
            if open_trade is not None:
                data = normalized[sym]
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
                all_trades.append(open_trade)

        # Build result
        result = BacktestResult(
            strategy_name=name,
            period=f"{common_index[0].date()} to {common_index[-1].date()}",
            total_trades=len(all_trades),
            trades=all_trades,
            equity_curve=equity_curve,
        )

        if all_trades:
            winners = [t for t in all_trades if t.pnl_usd > 0]
            losers = [t for t in all_trades if t.pnl_usd <= 0]

            result.wins = len(winners)
            result.losses = len(losers)
            result.win_rate = len(winners) / len(all_trades) * 100
            result.avg_win_pct = np.mean([t.pnl_pct for t in winners]) if winners else 0
            result.avg_loss_pct = np.mean([t.pnl_pct for t in losers]) if losers else 0
            result.total_pnl_usd = round(capital - self.initial_capital, 2)
            result.total_pnl_pct = round((capital - self.initial_capital) / self.initial_capital * 100, 2)
            result.max_drawdown_pct = round(max_drawdown * 100, 2)

            gross_profit = sum(t.pnl_usd for t in winners) if winners else 0
            gross_loss = abs(sum(t.pnl_usd for t in losers)) if losers else 1
            result.profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

        return result
