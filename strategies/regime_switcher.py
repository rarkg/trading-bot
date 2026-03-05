"""
Regime Switcher — The AI brain
Detects market regime and runs the right strategy:
- Trending up → Momentum LONG only
- Trending down → Momentum SHORT only  
- Ranging → DCA on dips
- Volatile crash → Aggressive DCA (buy the blood)
- No clear regime → sit out (cash)

This is the edge. Static bots run one strategy and die when regime changes.
"""

import numpy as np


class RegimeSwitcher:
    """Switches between strategies based on detected market regime."""
    
    def __init__(self):
        from strategies.momentum_v2 import MomentumV2
        from strategies.dca_v2 import DCAv2
        
        self.momentum = MomentumV2()
        self.dca = DCAv2()
        self._regime = "UNKNOWN"
        self._regime_bars = 0
    
    def _detect_regime(self, data, i):
        """Classify current market regime."""
        if i < 60:
            return "UNKNOWN"
        
        closes = data["close"].iloc[:i+1].astype(float)
        highs = data["high"].iloc[:i+1].astype(float)
        lows = data["low"].iloc[:i+1].astype(float)
        
        # EMAs for trend
        ema21 = closes.ewm(span=21, adjust=False).mean().iloc[-1]
        ema55 = closes.ewm(span=55, adjust=False).mean().iloc[-1]
        
        # Price vs EMAs
        price = float(closes.iloc[-1])
        above_21 = price > ema21
        above_55 = price > ema55
        ema21_above_55 = ema21 > ema55
        
        # Volatility (20-day ATR as % of price)
        tr = np.maximum(
            highs.iloc[-20:].values - lows.iloc[-20:].values,
            np.maximum(
                np.abs(highs.iloc[-20:].values - closes.shift(1).iloc[-20:].values),
                np.abs(lows.iloc[-20:].values - closes.shift(1).iloc[-20:].values)
            )
        )
        atr_pct = float(np.nanmean(tr)) / price * 100
        
        # 20-day return
        ret_20d = (price / float(closes.iloc[-20]) - 1) * 100 if i >= 20 else 0
        
        # Consecutive direction
        red_count = 0
        for j in range(len(closes)-1, max(len(closes)-8, 0), -1):
            if closes.iloc[j] < closes.iloc[j-1]:
                red_count += 1
            else:
                break
        
        # ADX proxy: slope of EMA21 (steepness = trend strength)
        if len(closes) >= 25:
            ema21_5ago = closes.ewm(span=21, adjust=False).mean().iloc[-5]
            ema_slope = (ema21 - ema21_5ago) / ema21_5ago * 100
        else:
            ema_slope = 0
        
        # REGIME CLASSIFICATION
        
        # CRASH: high volatility + deep red + below all EMAs
        if atr_pct > 4.0 and ret_20d < -15 and not above_55:
            return "CRASH"
        
        # VOLATILE SELLOFF: high vol + declining
        if atr_pct > 3.0 and ret_20d < -8 and red_count >= 3:
            return "VOLATILE_DOWN"
        
        # STRONG UPTREND: above all EMAs + EMAs aligned + positive slope
        if above_21 and above_55 and ema21_above_55 and ema_slope > 0.5:
            return "TREND_UP"
        
        # STRONG DOWNTREND: below all EMAs + EMAs aligned bearish + negative slope
        if not above_21 and not above_55 and not ema21_above_55 and ema_slope < -0.5:
            return "TREND_DOWN"
        
        # RANGING: price oscillating around EMAs, low directional movement
        if abs(ema_slope) < 0.3 and atr_pct < 3.0:
            return "RANGING"
        
        return "UNCLEAR"
    
    def generate_signal(self, data, i):
        if i < 200:
            return None
        
        regime = self._detect_regime(data, i)
        self._regime = regime
        
        if regime == "TREND_UP":
            # Only take long momentum signals
            sig = self.momentum.generate_signal(data, i)
            if sig and sig["action"] == "LONG":
                sig["signal"] = f"[{regime}] {sig['signal']}"
                return sig
        
        elif regime == "TREND_DOWN":
            # Only take short momentum signals
            sig = self.momentum.generate_signal(data, i)
            if sig and sig["action"] == "SHORT":
                sig["signal"] = f"[{regime}] {sig['signal']}"
                return sig
        
        elif regime == "CRASH" or regime == "VOLATILE_DOWN":
            # Aggressive DCA — buy the blood
            sig = self.dca.generate_signal(data, i)
            if sig:
                sig["signal"] = f"[{regime}] {sig['signal']}"
                return sig
        
        elif regime == "RANGING":
            # Light DCA on dips
            sig = self.dca.generate_signal(data, i)
            if sig:
                sig["signal"] = f"[{regime}] {sig['signal']}"
                return sig
        
        # UNCLEAR → sit out
        return None
    
    def check_exit(self, data, i, trade):
        """Delegate exit to the appropriate strategy."""
        # Check if regime changed dramatically
        regime = self._detect_regime(data, i)
        
        # If we're long and regime flips to downtrend → exit
        if trade.direction == "LONG" and regime in ("TREND_DOWN", "CRASH"):
            return "REGIME_FLIP_EXIT"
        
        # If we're short and regime flips to uptrend → exit
        if trade.direction == "SHORT" and regime == "TREND_UP":
            return "REGIME_FLIP_EXIT"
        
        # Otherwise delegate to child strategy
        mom_exit = self.momentum.check_exit(data, i, trade)
        if mom_exit:
            return mom_exit
        
        dca_exit = self.dca.check_exit(data, i, trade)
        if dca_exit:
            return dca_exit
        
        return None
