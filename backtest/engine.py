"""
Backtesting engine — runs strategies against historical data.
Tracks trades, P&L, drawdown, win rate, Sharpe ratio.

Supports:
- Full period testing
- Out-of-sample splits (train/test)
- Random period sampling for stress testing
- Multiple strategy comparison
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
import random


@dataclass
class Trade:
    entry_time: pd.Timestamp
    entry_price: float
    direction: str  # "LONG" or "SHORT"
    signal: str
    stop_price: float = 0
    target_price: float = 0
    size_usd: float = 0
    confidence_score: int = 0
    leverage: float = 1.0
    exit_time: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    pnl_pct: float = 0
    pnl_usd: float = 0
    # Indicator snapshots at entry
    rsi_at_entry: Optional[float] = None
    atr_at_entry: Optional[float] = None
    atr_pct_at_entry: Optional[float] = None
    vol_ratio_at_entry: Optional[float] = None
    bb_width_at_entry: Optional[float] = None
    ema_trend_at_entry: Optional[str] = None
    range_position_at_entry: Optional[float] = None
    adx_at_entry: Optional[float] = None
    market_regime: Optional[str] = None


@dataclass
class BacktestResult:
    strategy_name: str
    period: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0
    avg_win_pct: float = 0
    avg_loss_pct: float = 0
    total_pnl_usd: float = 0
    total_pnl_pct: float = 0
    max_drawdown_pct: float = 0
    sharpe_ratio: float = 0
    profit_factor: float = 0
    avg_trade_duration_hours: float = 0
    trades: list = field(default_factory=list)
    equity_curve: list = field(default_factory=list)


class BacktestEngine:
    def __init__(self, initial_capital=1000, fee_pct=0.10, max_risk_pct=2.0):
        """
        Args:
            initial_capital: Starting balance in USD
            fee_pct: Fee per trade as percentage (0.10 = 0.10%)
            max_risk_pct: Max risk per trade as % of capital
        """
        self.initial_capital = initial_capital
        self.fee_pct = fee_pct / 100
        self.max_risk_pct = max_risk_pct / 100
        
    def run(self, data: pd.DataFrame, strategy, name="unnamed") -> BacktestResult:
        """
        Run a strategy against historical data.
        
        Args:
            data: DataFrame with OHLCV columns (open, high, low, close, volume)
            strategy: Object with generate_signals(data, i) method
            name: Strategy name for reporting
        """
        capital = self.initial_capital
        peak_capital = capital
        max_drawdown = 0
        trades = []
        equity_curve = [capital]
        open_trade = None
        daily_pnls = []
        
        # Normalize column names
        data = data.copy()
        data.columns = [c.lower() for c in data.columns]

        # Pre-extract numpy arrays for fast candle access (avoids iloc overhead)
        closes = data["close"].values.astype(float)
        highs = data["high"].values.astype(float)
        lows = data["low"].values.astype(float)

        for i in range(20, len(data)):  # Skip first 20 for indicator warmup
            price = closes[i]
            high = highs[i]
            low = lows[i]
            
            # Check exit conditions on open trade
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
                
                # Also check strategy exit signal
                if not exit_reason:
                    sig = strategy.check_exit(data, i, open_trade)
                    if sig:
                        exit_reason = sig
                        exit_price = price
                
                if exit_reason:
                    # Close trade
                    open_trade.exit_time = data.index[i]
                    open_trade.exit_price = exit_price
                    open_trade.exit_reason = exit_reason
                    
                    if open_trade.direction == "LONG":
                        raw_pnl_pct = (exit_price - open_trade.entry_price) / open_trade.entry_price
                    else:
                        raw_pnl_pct = (open_trade.entry_price - exit_price) / open_trade.entry_price
                    
                    # Subtract fees (entry + exit)
                    pnl_pct = raw_pnl_pct - (self.fee_pct * 2)
                    pnl_usd = open_trade.size_usd * pnl_pct
                    
                    open_trade.pnl_pct = round(pnl_pct * 100, 2)
                    open_trade.pnl_usd = round(pnl_usd, 2)
                    
                    capital += pnl_usd
                    trades.append(open_trade)
                    daily_pnls.append(pnl_pct)
                    open_trade = None
            
            # Check for new entry signal (only if no open trade)
            if not open_trade:
                signal = strategy.generate_signal(data, i)
                
                if signal and signal.get("action") in ("LONG", "SHORT"):
                    direction = signal["action"]
                    stop = signal.get("stop", 0)
                    target = signal.get("target", 0)
                    
                    # Dynamic leverage from signal (if provided)
                    sig_leverage = signal.get("leverage", 1.0)
                    
                    # Position sizing: risk-based, fixed initial capital reference
                    # Max loss per trade = max_risk_pct of INITIAL capital (not rolling)
                    # This prevents runaway compounding in backtest
                    risk_per_unit = abs(price - stop) if stop else price * 0.02
                    max_loss = self.initial_capital * self.max_risk_pct
                    
                    if risk_per_unit > 0 and price > 0:
                        # Size so that stop-loss hit = max_loss
                        contracts = max_loss / risk_per_unit
                        size = contracts * price / sig_leverage
                    else:
                        size = self.initial_capital * 0.15  # fallback: 15% of initial
                    
                    # Hard cap: never risk more than 30% of initial capital per trade
                    size = min(size, self.initial_capital * 0.30 * sig_leverage)
                    
                    open_trade = Trade(
                        entry_time=data.index[i],
                        entry_price=price,
                        direction=direction,
                        signal=signal.get("signal", ""),
                        stop_price=stop,
                        target_price=target,
                        size_usd=round(size, 2),
                        confidence_score=signal.get("confidence_score", 0),
                        leverage=sig_leverage,
                        rsi_at_entry=signal.get("rsi_at_entry"),
                        atr_at_entry=signal.get("atr_at_entry"),
                        atr_pct_at_entry=signal.get("atr_pct_at_entry"),
                        vol_ratio_at_entry=signal.get("vol_ratio_at_entry"),
                        bb_width_at_entry=signal.get("bb_width_at_entry"),
                        ema_trend_at_entry=signal.get("ema_trend_at_entry"),
                        range_position_at_entry=signal.get("range_position_at_entry"),
                        adx_at_entry=signal.get("adx_at_entry"),
                        market_regime=signal.get("market_regime"),
                    )
            
            # Track equity
            equity_curve.append(capital)
            peak_capital = max(peak_capital, capital)
            drawdown = (peak_capital - capital) / peak_capital if peak_capital > 0 else 0
            max_drawdown = max(max_drawdown, drawdown)
        
        # Close any remaining open trade at last price
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
        
        # Calculate stats
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
            
            # Sharpe ratio (annualized, using daily PnL)
            if daily_pnls:
                avg_return = np.mean(daily_pnls)
                std_return = np.std(daily_pnls) if len(daily_pnls) > 1 else 1
                result.sharpe_ratio = round(avg_return / std_return * np.sqrt(365), 2) if std_return > 0 else 0
            
            # Average trade duration
            durations = []
            for t in trades:
                if t.exit_time and t.entry_time:
                    dur = (t.exit_time - t.entry_time).total_seconds() / 3600
                    durations.append(dur)
            result.avg_trade_duration_hours = round(np.mean(durations), 1) if durations else 0
        
        return result
    
    def run_split(self, data, strategy, name="unnamed", train_pct=0.6):
        """Run with train/test split for out-of-sample validation."""
        split_idx = int(len(data) * train_pct)
        train_data = data.iloc[:split_idx]
        test_data = data.iloc[split_idx:]
        
        train_result = self.run(train_data, strategy, f"{name} [TRAIN]")
        test_result = self.run(test_data, strategy, f"{name} [TEST]")
        
        return train_result, test_result
    
    def run_random_periods(self, data, strategy, name="unnamed",
                           num_periods=10, period_days=30):
        """Run on random time periods for stress testing."""
        results = []
        max_start = len(data) - period_days
        
        for i in range(num_periods):
            start = random.randint(0, max_start)
            chunk = data.iloc[start:start + period_days]
            result = self.run(chunk, strategy, 
                            f"{name} [RANDOM {chunk.index[0].date()}-{chunk.index[-1].date()}]")
            results.append(result)
        
        return results


def print_result(r: BacktestResult):
    """Pretty print backtest results."""
    print(f"\n{'='*60}")
    print(f"  {r.strategy_name}")
    print(f"  Period: {r.period}")
    print(f"{'='*60}")
    print(f"  Trades: {r.total_trades} | Wins: {r.wins} | Losses: {r.losses}")
    print(f"  Win Rate: {r.win_rate:.1f}%")
    print(f"  Avg Win: {r.avg_win_pct:+.2f}% | Avg Loss: {r.avg_loss_pct:+.2f}%")
    print(f"  Profit Factor: {r.profit_factor:.2f}")
    print(f"  Total P&L: ${r.total_pnl_usd:+,.2f} ({r.total_pnl_pct:+.1f}%)")
    print(f"  Max Drawdown: {r.max_drawdown_pct:.1f}%")
    print(f"  Sharpe Ratio: {r.sharpe_ratio:.2f}")
    print(f"  Avg Trade Duration: {r.avg_trade_duration_hours:.1f}h")
    
    if r.total_pnl_usd > 0:
        print(f"  ✅ PROFITABLE")
    else:
        print(f"  ❌ NOT PROFITABLE")
    print()
