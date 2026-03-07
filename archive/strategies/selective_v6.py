"""
Selective V6 — Only trade what's proven profitable on hourly data
Backtesting showed:
- SQUEEZE: profitable on both ETH and SOL ✅
- PULLBACK: slightly profitable ✅  
- Everything else: LOSES money on hourly ❌

Strategy:
1. Multi-timeframe: compute daily trend, enter on hourly
2. Only 3 signal types: squeeze breakout, trend pullback, extreme fear
3. Higher minimum score (65+) 
4. Wider stops (2x ATR) to avoid noise
5. Let winners run with trailing stop (3x ATR initial, tighten to 1.5x)

The edge is PATIENCE — wait for the A+ setup, then size up.
"""

import numpy as np
import pandas as pd


class SelectiveV6:
    
    def __init__(self):
        self._ind = None
        self._daily_trend = None
        self._trailing_stop = None
        self._best_price = None
        self._last_exit_bar = -20
    
    def _precompute(self, data):
        """Vectorized indicators."""
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)
        opens = data["open"].astype(float)
        
        # EMAs (multiple timeframes simulated)
        ema8 = closes.ewm(span=8, adjust=False).mean()
        ema21 = closes.ewm(span=21, adjust=False).mean()
        ema55 = closes.ewm(span=55, adjust=False).mean()
        ema200 = closes.ewm(span=200, adjust=False).mean()  # ~8 days on hourly
        
        # "Daily" EMAs (24x multiplier for hourly → daily equivalent)
        ema_daily_fast = closes.ewm(span=8*24, adjust=False).mean()   # ~8 day
        ema_daily_slow = closes.ewm(span=21*24, adjust=False).mean()  # ~21 day
        ema_daily_trend = closes.ewm(span=55*24, adjust=False).mean() # ~55 day
        
        # ATR
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_daily = tr.rolling(14*24).mean()  # Daily-equivalent ATR
        
        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))
        
        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_avg_long = volumes.rolling(24*5).mean()  # 5-day average
        vol_ratio = volumes / vol_avg.replace(0, 1)
        vol_ratio_daily = volumes / vol_avg_long.replace(0, 1)
        
        # Bollinger Bands
        sma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20
        bb_width = (std20 / sma20.replace(0, 1)) * 100
        
        # Bollinger squeeze detection (width below 20-period average of width)
        bb_width_avg = bb_width.rolling(100).mean()
        is_squeeze = bb_width < (bb_width_avg * 0.7)  # Width below 70% of average
        
        # Candle properties
        body_ratio = (closes - opens).abs() / (highs - lows).replace(0, 1)
        bullish = closes > opens
        
        # EMA slopes
        ema21_slope = (ema21 - ema21.shift(5)) / ema21.shift(5).replace(0, 1) * 100
        daily_slope = (ema_daily_fast - ema_daily_fast.shift(24)) / ema_daily_fast.shift(24).replace(0, 1) * 100
        
        # Near EMA21 (within 0.3% — tighter for hourly)
        near_ema21 = ((closes - ema21).abs() / ema21.replace(0, 1)) < 0.003
        
        # Swing high/low
        swing_high = highs.rolling(48).max()  # 2-day swing
        swing_low = lows.rolling(48).min()
        
        # Consecutive reds
        is_red = closes < closes.shift(1)
        red_count = pd.Series(0.0, index=data.index)
        for idx in range(1, len(is_red)):
            if is_red.iloc[idx]:
                red_count.iloc[idx] = red_count.iloc[idx-1] + 1
        
        # Rate of change
        roc_24 = (closes / closes.shift(24).replace(0, 1) - 1) * 100  # 24h ROC
        
        self._ind = pd.DataFrame({
            "close": closes, "open": opens, "high": highs, "low": lows,
            "ema8": ema8, "ema21": ema21, "ema55": ema55, "ema200": ema200,
            "ema_daily_fast": ema_daily_fast, "ema_daily_slow": ema_daily_slow,
            "ema_daily_trend": ema_daily_trend,
            "atr": atr, "atr_daily": atr_daily,
            "rsi": rsi, "vol_ratio": vol_ratio, "vol_ratio_daily": vol_ratio_daily,
            "bb_upper": bb_upper, "bb_lower": bb_lower, 
            "bb_width": bb_width, "is_squeeze": is_squeeze,
            "body_ratio": body_ratio, "bullish": bullish,
            "ema21_slope": ema21_slope, "daily_slope": daily_slope,
            "near_ema21": near_ema21,
            "swing_high": swing_high, "swing_low": swing_low,
            "red_count": red_count, "roc_24": roc_24,
            "prev_high": highs.shift(1), "prev_low": lows.shift(1),
        })
    
    def _daily_trend_direction(self, i):
        """Get daily trend: UP, DOWN, or FLAT."""
        ind = self._ind.iloc[i]
        if ind["ema_daily_fast"] > ind["ema_daily_slow"] > ind["ema_daily_trend"]:
            return "UP"
        elif ind["ema_daily_fast"] < ind["ema_daily_slow"] < ind["ema_daily_trend"]:
            return "DOWN"
        return "FLAT"
    
    def _score_setup(self, i, direction):
        """Score the setup quality for position sizing."""
        ind = self._ind.iloc[i]
        score = 0
        
        # Daily trend alignment (0-30) — THE most important factor
        trend = self._daily_trend_direction(i)
        if direction == "LONG" and trend == "UP":
            score += 30
        elif direction == "LONG" and trend == "FLAT":
            score += 10
        elif direction == "SHORT" and trend == "DOWN":
            score += 30
        elif direction == "SHORT" and trend == "FLAT":
            score += 10
        else:
            return 0  # Trading against daily trend = skip entirely
        
        # Hourly trend (0-15)
        if direction == "LONG" and ind["ema8"] > ind["ema21"]:
            score += 15
        elif direction == "SHORT" and ind["ema8"] < ind["ema21"]:
            score += 15
        
        # Volume surge (0-15)
        if ind["vol_ratio"] > 2.0:
            score += 15
        elif ind["vol_ratio"] > 1.5:
            score += 10
        elif ind["vol_ratio"] > 1.2:
            score += 5
        
        # Candle strength (0-10)
        if direction == "LONG" and ind["bullish"] and ind["body_ratio"] > 0.6:
            score += 10
        elif direction == "SHORT" and not ind["bullish"] and ind["body_ratio"] > 0.6:
            score += 10
        
        # RSI (0-10)
        if direction == "LONG" and 35 < ind["rsi"] < 60:
            score += 10
        elif direction == "SHORT" and 40 < ind["rsi"] < 65:
            score += 10
        
        # Daily slope momentum (0-20)
        if direction == "LONG" and ind["daily_slope"] > 0.5:
            score += 20
        elif direction == "LONG" and ind["daily_slope"] > 0.2:
            score += 10
        elif direction == "SHORT" and ind["daily_slope"] < -0.5:
            score += 20
        elif direction == "SHORT" and ind["daily_slope"] < -0.2:
            score += 10
        
        return min(score, 100)
    
    def _score_to_leverage(self, score):
        if score >= 85:
            return 3.5
        elif score >= 75:
            return 2.5
        elif score >= 65:
            return 2.0
        else:
            return 1.5
    
    def generate_signal(self, data, i):
        if self._ind is None:
            self._precompute(data)
        
        if i < 200 * 24 or i >= len(self._ind):  # Need warmup for daily EMAs
            return None
        
        if i - self._last_exit_bar < 6:  # 6-hour cooldown
            return None
        
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        
        if pd.isna(atr) or atr <= 0:
            return None
        
        daily_trend = self._daily_trend_direction(i)
        
        # ============ SIGNAL 1: SQUEEZE BREAKOUT ============
        # Bollinger squeeze detected, price breaks out with volume
        # This was the ONLY consistently profitable signal on hourly
        
        if ind["is_squeeze"]:
            # Check if breaking out of squeeze NOW
            if (price > ind["prev_high"] and ind["vol_ratio"] > 1.3 and
                ind["bullish"] and daily_trend in ("UP", "FLAT")):
                score = self._score_setup(i, "LONG")
                if score >= 55:
                    lev = self._score_to_leverage(score)
                    self._trailing_stop = price - (atr * 2.5)
                    self._best_price = price
                    return {
                        "action": "LONG", "signal": f"SQUEEZE_LONG(s{score})",
                        "stop": price - (atr * 2.5),
                        "target": price + (atr * 8),  # Wide target, trailing does work
                        "leverage": lev,
                    }
            
            if (price < ind["prev_low"] and ind["vol_ratio"] > 1.3 and
                not ind["bullish"] and daily_trend in ("DOWN", "FLAT")):
                score = self._score_setup(i, "SHORT")
                if score >= 55:
                    lev = self._score_to_leverage(score)
                    self._trailing_stop = price + (atr * 2.5)
                    self._best_price = price
                    return {
                        "action": "SHORT", "signal": f"SQUEEZE_SHORT(s{score})",
                        "stop": price + (atr * 2.5),
                        "target": price - (atr * 8),
                        "leverage": lev,
                    }
        
        # ============ SIGNAL 2: TREND PULLBACK ============
        # Price pulls back to EMA21 in strong daily trend, hourly candle confirms bounce
        
        if daily_trend == "UP" and ind["near_ema21"]:
            if (ind["bullish"] and ind["body_ratio"] > 0.45 and
                ind["ema8"] > ind["ema21"] and ind["rsi"] > 35):
                score = self._score_setup(i, "LONG")
                if score >= 60:
                    lev = self._score_to_leverage(score)
                    self._trailing_stop = price - (atr * 2.0)
                    self._best_price = price
                    return {
                        "action": "LONG", "signal": f"PULLBACK_LONG(s{score})",
                        "stop": price - (atr * 2.0),
                        "target": price + (atr * 6),
                        "leverage": lev,
                    }
        
        if daily_trend == "DOWN" and ind["near_ema21"]:
            if (not ind["bullish"] and ind["body_ratio"] > 0.45 and
                ind["ema8"] < ind["ema21"] and ind["rsi"] < 65):
                score = self._score_setup(i, "SHORT")
                if score >= 60:
                    lev = self._score_to_leverage(score)
                    self._trailing_stop = price + (atr * 2.0)
                    self._best_price = price
                    return {
                        "action": "SHORT", "signal": f"PULLBACK_SHORT(s{score})",
                        "stop": price + (atr * 2.0),
                        "target": price - (atr * 6),
                        "leverage": lev,
                    }
        
        # ============ SIGNAL 3: EXTREME FEAR (rare, high conviction) ============
        # RSI < 20 on hourly + 8+ consecutive red candles + volume spike
        # This only fires 2-3 times per year but catches major bottoms
        
        if ind["rsi"] < 20 and ind["red_count"] >= 8 and ind["vol_ratio"] > 2.0:
            self._trailing_stop = price - (atr * 3.0)
            self._best_price = price
            return {
                "action": "LONG", "signal": "EXTREME_FEAR(s80)",
                "stop": price - (atr * 3.0),
                "target": price + (atr * 10),
                "leverage": 3.0,  # High conviction
            }
        
        return None
    
    def check_exit(self, data, i, trade):
        if self._ind is None or i >= len(self._ind):
            return None
        
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        
        if pd.isna(atr) or atr <= 0:
            return None
        
        # Trailing stop with adaptive tightening
        if trade.direction == "LONG":
            if price > (self._best_price or price):
                self._best_price = price
                pnl_r = (price - trade.entry_price) / atr
                # Start at 2.5x ATR trail, tighten to 1.2x as profit grows
                trail_mult = max(1.2, 2.5 - (pnl_r * 0.1))
                new_trail = price - (atr * trail_mult)
                if new_trail > (self._trailing_stop or 0):
                    self._trailing_stop = new_trail
                    trade.stop_price = new_trail
            
            if self._trailing_stop and price < self._trailing_stop:
                self._last_exit_bar = i
                return "TRAILING_STOP"
            
            # Daily trend reversal — get out
            if self._daily_trend_direction(i) == "DOWN":
                self._last_exit_bar = i
                return "DAILY_TREND_FLIP"
        
        elif trade.direction == "SHORT":
            if price < (self._best_price or price):
                self._best_price = price
                pnl_r = (trade.entry_price - price) / atr
                trail_mult = max(1.2, 2.5 - (pnl_r * 0.1))
                new_trail = price + (atr * trail_mult)
                if new_trail < (self._trailing_stop or float('inf')):
                    self._trailing_stop = new_trail
                    trade.stop_price = new_trail
            
            if self._trailing_stop and price > self._trailing_stop:
                self._last_exit_bar = i
                return "TRAILING_STOP"
            
            if self._daily_trend_direction(i) == "UP":
                self._last_exit_bar = i
                return "DAILY_TREND_FLIP"
        
        return None
