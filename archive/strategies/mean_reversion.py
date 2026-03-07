"""
Mean Reversion Strategy
- Fades overextended moves (RSI extreme + Bollinger Band breach)
- Works best in ranging markets
- Tight stops because if the trend is real, you want out fast
"""

import numpy as np


class MeanReversion:
    """Fade extremes, profit from snap-back."""
    
    def __init__(self, rsi_oversold=25, rsi_overbought=75, 
                 bb_period=20, bb_std=2.0, min_extension_pct=3.0):
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.min_extension_pct = min_extension_pct
    
    def _calc_indicators(self, data, i):
        closes = data["close"].iloc[:i+1].astype(float)
        highs = data["high"].iloc[:i+1].astype(float)
        lows = data["low"].iloc[:i+1].astype(float)
        
        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else 100
        rsi = 100 - (100 / (1 + rs))
        
        # Bollinger Bands
        sma = closes.rolling(self.bb_period).mean().iloc[-1]
        std = closes.rolling(self.bb_period).std().iloc[-1]
        bb_lower = sma - (self.bb_std * std)
        bb_upper = sma + (self.bb_std * std)
        bb_mid = sma
        
        # Extension from 20-SMA
        extension_pct = (float(closes.iloc[-1]) - sma) / sma * 100
        
        # ATR for stops
        tr = np.maximum(
            highs.iloc[-14:] - lows.iloc[-14:],
            np.maximum(
                abs(highs.iloc[-14:] - closes.shift(1).iloc[-14:]),
                abs(lows.iloc[-14:] - closes.shift(1).iloc[-14:])
            )
        )
        atr = float(tr.mean())
        
        return {
            "rsi": rsi,
            "close": float(closes.iloc[-1]),
            "bb_lower": bb_lower,
            "bb_upper": bb_upper,
            "bb_mid": bb_mid,
            "extension_pct": extension_pct,
            "atr": atr,
        }
    
    def generate_signal(self, data, i):
        if i < 30:
            return None
        
        ind = self._calc_indicators(data, i)
        price = ind["close"]
        atr = ind["atr"]
        
        # LONG: Oversold + below lower Bollinger + extended down 3%+
        if (ind["rsi"] < self.rsi_oversold and 
            price < ind["bb_lower"] and 
            ind["extension_pct"] < -self.min_extension_pct):
            return {
                "action": "LONG",
                "signal": "MEAN_REV_OVERSOLD",
                "stop": price - (atr * 1.0),          # Tight stop
                "target": ind["bb_mid"],                # Target: back to mean
            }
        
        # SHORT: Overbought + above upper Bollinger + extended up 3%+
        if (ind["rsi"] > self.rsi_overbought and 
            price > ind["bb_upper"] and 
            ind["extension_pct"] > self.min_extension_pct):
            return {
                "action": "SHORT",
                "signal": "MEAN_REV_OVERBOUGHT",
                "stop": price + (atr * 1.0),
                "target": ind["bb_mid"],
            }
        
        return None
    
    def check_exit(self, data, i, trade):
        if i < 30:
            return None
        
        ind = self._calc_indicators(data, i)
        
        # Exit when price returns to mean (Bollinger midline)
        if trade.direction == "LONG" and ind["close"] >= ind["bb_mid"]:
            return "MEAN_REACHED"
        if trade.direction == "SHORT" and ind["close"] <= ind["bb_mid"]:
            return "MEAN_REACHED"
        
        return None
