"""
Momentum / Breakout Strategy
- Detects breakout from prior candle range
- Uses EMA trend + volume confirmation
- ATR-based stops (1.5x) and targets (3x) for 2:1 R/R
"""

import numpy as np


class MomentumStrategy:
    """Breakout + trend following with ATR stops."""
    
    def __init__(self, ema_fast=12, ema_slow=26, atr_period=14, 
                 atr_stop_mult=1.5, atr_target_mult=3.0,
                 volume_confirm=True):
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.atr_period = atr_period
        self.atr_stop_mult = atr_stop_mult
        self.atr_target_mult = atr_target_mult
        self.volume_confirm = volume_confirm
    
    def _calc_indicators(self, data, i):
        """Calculate indicators up to index i."""
        closes = data["close"].iloc[:i+1].astype(float)
        highs = data["high"].iloc[:i+1].astype(float)
        lows = data["low"].iloc[:i+1].astype(float)
        volumes = data["volume"].iloc[:i+1].astype(float)
        
        # EMAs
        ema_fast = closes.ewm(span=self.ema_fast, adjust=False).mean().iloc[-1]
        ema_slow = closes.ewm(span=self.ema_slow, adjust=False).mean().iloc[-1]
        
        # ATR
        tr = np.maximum(
            highs.iloc[-self.atr_period:] - lows.iloc[-self.atr_period:],
            np.maximum(
                abs(highs.iloc[-self.atr_period:] - closes.shift(1).iloc[-self.atr_period:]),
                abs(lows.iloc[-self.atr_period:] - closes.shift(1).iloc[-self.atr_period:])
            )
        )
        atr = float(tr.mean())
        
        # Volume ratio (current vs 20-period avg)
        vol_avg = volumes.iloc[-20:].mean()
        vol_ratio = float(volumes.iloc[-1] / vol_avg) if vol_avg > 0 else 1.0
        
        # RSI (14-period)
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else 100
        rsi = 100 - (100 / (1 + rs))
        
        return {
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "atr": atr,
            "vol_ratio": vol_ratio,
            "rsi": rsi,
            "close": float(closes.iloc[-1]),
            "prev_high": float(highs.iloc[-2]),
            "prev_low": float(lows.iloc[-2]),
            "curr_high": float(highs.iloc[-1]),
            "curr_low": float(lows.iloc[-1]),
        }
    
    def generate_signal(self, data, i):
        """Generate trading signal at index i."""
        if i < 26:  # Need warmup
            return None
        
        ind = self._calc_indicators(data, i)
        
        price = ind["close"]
        atr = ind["atr"]
        
        # Trend filter: EMA alignment
        bullish_trend = ind["ema_fast"] > ind["ema_slow"]
        bearish_trend = ind["ema_fast"] < ind["ema_slow"]
        
        # Breakout detection
        broke_high = ind["curr_high"] > ind["prev_high"]
        broke_low = ind["curr_low"] < ind["prev_low"]
        closed_above = price > ind["prev_high"]
        closed_below = price < ind["prev_low"]
        
        # Volume confirmation
        vol_ok = ind["vol_ratio"] > 1.0 if self.volume_confirm else True
        
        # RSI filter — don't buy overbought, don't short oversold
        rsi_ok_long = ind["rsi"] < 70
        rsi_ok_short = ind["rsi"] > 30
        
        # LONG: breakout above prev high + bullish trend + volume
        if broke_high and closed_above and bullish_trend and vol_ok and rsi_ok_long:
            return {
                "action": "LONG",
                "signal": "BREAKOUT_UP",
                "stop": price - (atr * self.atr_stop_mult),
                "target": price + (atr * self.atr_target_mult),
            }
        
        # SHORT: breakdown below prev low + bearish trend + volume
        if broke_low and closed_below and bearish_trend and vol_ok and rsi_ok_short:
            return {
                "action": "SHORT",
                "signal": "BREAKDOWN",
                "stop": price + (atr * self.atr_stop_mult),
                "target": price - (atr * self.atr_target_mult),
            }
        
        return None
    
    def check_exit(self, data, i, trade):
        """Check for exit signals beyond stop/target."""
        if i < 26:
            return None
        
        ind = self._calc_indicators(data, i)
        
        # Exit long if trend flips bearish
        if trade.direction == "LONG" and ind["ema_fast"] < ind["ema_slow"]:
            return "TREND_REVERSAL"
        
        # Exit short if trend flips bullish
        if trade.direction == "SHORT" and ind["ema_fast"] > ind["ema_slow"]:
            return "TREND_REVERSAL"
        
        return None
