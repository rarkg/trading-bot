"""
V7 — SOL Squeeze Only
The data speaks: only ONE signal type is consistently profitable.
SOL SQUEEZE_LONG made $864 from 166 trades.
Everything else loses money on hourly data.

Strategy: ONLY trade Bollinger squeeze breakouts on SOL.
- Full $1K on SOL
- Only enter when Bollinger width compresses below 70% of average
- Breakout confirmed by volume + candle + daily trend
- Dynamic leverage 2-3.5x based on confidence
- Adaptive trailing stop
"""

import numpy as np
import pandas as pd


class SqueezeOnlyV7:
    
    def __init__(self):
        self._ind = None
        self._trailing_stop = None
        self._best_price = None
        self._last_exit_bar = -12
    
    def _precompute(self, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)
        opens = data["open"].astype(float)
        
        ema8 = closes.ewm(span=8, adjust=False).mean()
        ema21 = closes.ewm(span=21, adjust=False).mean()
        ema55 = closes.ewm(span=55, adjust=False).mean()
        
        # Daily-equivalent EMAs
        ema_d_fast = closes.ewm(span=192, adjust=False).mean()   # 8*24
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()   # 21*24
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean() # 55*24
        
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))
        
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)
        
        sma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20
        bb_width = (std20 / sma20.replace(0, 1)) * 100
        bb_width_avg = bb_width.rolling(120).mean()  # 5-day avg of width
        is_squeeze = bb_width < (bb_width_avg * 0.65)  # Tighter threshold
        
        body_ratio = (closes - opens).abs() / (highs - lows).replace(0, 1)
        bullish = closes > opens
        
        ema21_slope = (ema21 - ema21.shift(5)) / ema21.shift(5).replace(0, 1) * 100
        d_slope = (ema_d_fast - ema_d_fast.shift(24)) / ema_d_fast.shift(24).replace(0, 1) * 100
        
        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "ema8": ema8, "ema21": ema21, "ema55": ema55,
            "ema_d_fast": ema_d_fast, "ema_d_slow": ema_d_slow, "ema_d_trend": ema_d_trend,
            "atr": atr, "rsi": rsi, "vol_ratio": vol_ratio,
            "bb_width": bb_width, "is_squeeze": is_squeeze,
            "body_ratio": body_ratio, "bullish": bullish,
            "ema21_slope": ema21_slope, "d_slope": d_slope,
            "prev_high": highs.shift(1), "prev_low": lows.shift(1),
        })
    
    def _daily_trend(self, i):
        ind = self._ind.iloc[i]
        if ind["ema_d_fast"] > ind["ema_d_slow"] > ind["ema_d_trend"]:
            return "UP"
        elif ind["ema_d_fast"] < ind["ema_d_slow"] < ind["ema_d_trend"]:
            return "DOWN"
        return "FLAT"
    
    def _confidence(self, i, direction):
        ind = self._ind.iloc[i]
        score = 0
        trend = self._daily_trend(i)
        
        # Daily trend alignment (0-30)
        if direction == "LONG" and trend == "UP":
            score += 30
        elif direction == "LONG" and trend == "FLAT":
            score += 10
        elif direction == "SHORT" and trend == "DOWN":
            score += 30
        elif direction == "SHORT" and trend == "FLAT":
            score += 10
        else:
            return 0  # Counter-trend = no trade
        
        # Hourly EMA alignment (0-20)
        if direction == "LONG" and ind["ema8"] > ind["ema21"] > ind["ema55"]:
            score += 20
        elif direction == "LONG" and ind["ema8"] > ind["ema21"]:
            score += 10
        elif direction == "SHORT" and ind["ema8"] < ind["ema21"] < ind["ema55"]:
            score += 20
        elif direction == "SHORT" and ind["ema8"] < ind["ema21"]:
            score += 10
        
        # Volume (0-20)
        if ind["vol_ratio"] > 2.5:
            score += 20
        elif ind["vol_ratio"] > 1.8:
            score += 15
        elif ind["vol_ratio"] > 1.3:
            score += 10
        
        # Candle strength (0-15)
        if ind["body_ratio"] > 0.7:
            score += 15
        elif ind["body_ratio"] > 0.5:
            score += 10
        
        # Daily slope (0-15)
        if direction == "LONG" and ind["d_slope"] > 0.5:
            score += 15
        elif direction == "LONG" and ind["d_slope"] > 0.2:
            score += 8
        elif direction == "SHORT" and ind["d_slope"] < -0.5:
            score += 15
        elif direction == "SHORT" and ind["d_slope"] < -0.2:
            score += 8
        
        return min(score, 100)
    
    def generate_signal(self, data, i):
        if self._ind is None:
            self._precompute(data)
        
        if i < 1400 or i >= len(self._ind):  # Need daily EMA warmup
            return None
        
        if i - self._last_exit_bar < 8:  # 8-hour cooldown
            return None
        
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        
        if pd.isna(atr) or atr <= 0:
            return None
        
        # ONLY trade squeeze breakouts
        if not ind["is_squeeze"]:
            return None
        
        # LONG: squeeze + break above prev high + bullish candle + volume
        if (price > ind["prev_high"] and ind["bullish"] and
            ind["body_ratio"] > 0.4 and ind["vol_ratio"] > 1.2 and
            ind["rsi"] < 75):
            
            score = self._confidence(i, "LONG")
            if score < 50:
                return None
            
            if score >= 80:
                lev = 3.0
            elif score >= 65:
                lev = 2.5
            else:
                lev = 2.0
            
            self._trailing_stop = price - (atr * 2.5)
            self._best_price = price
            
            return {
                "action": "LONG", "signal": f"SQUEEZE_L(s{score})",
                "stop": price - (atr * 2.5),
                "target": price + (atr * 10),  # Wide target
                "leverage": lev,
            }
        
        # SHORT: squeeze + break below prev low + bearish candle + volume
        if (price < ind["prev_low"] and not ind["bullish"] and
            ind["body_ratio"] > 0.4 and ind["vol_ratio"] > 1.2 and
            ind["rsi"] > 25):
            
            score = self._confidence(i, "SHORT")
            if score < 50:
                return None
            
            if score >= 80:
                lev = 3.0
            elif score >= 65:
                lev = 2.5
            else:
                lev = 2.0
            
            self._trailing_stop = price + (atr * 2.5)
            self._best_price = price
            
            return {
                "action": "SHORT", "signal": f"SQUEEZE_S(s{score})",
                "stop": price + (atr * 2.5),
                "target": price - (atr * 10),
                "leverage": lev,
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
        
        if trade.direction == "LONG":
            if price > (self._best_price or price):
                self._best_price = price
                pnl_r = (price - trade.entry_price) / atr
                trail = max(1.0, 2.5 - (pnl_r * 0.08))
                new_trail = price - (atr * trail)
                if new_trail > (self._trailing_stop or 0):
                    self._trailing_stop = new_trail
                    trade.stop_price = new_trail
            
            if self._trailing_stop and price < self._trailing_stop:
                self._last_exit_bar = i
                return "TRAIL"
            
            if self._daily_trend(i) == "DOWN":
                self._last_exit_bar = i
                return "TREND_FLIP"
        
        elif trade.direction == "SHORT":
            if price < (self._best_price or price):
                self._best_price = price
                pnl_r = (trade.entry_price - price) / atr
                trail = max(1.0, 2.5 - (pnl_r * 0.08))
                new_trail = price + (atr * trail)
                if new_trail < (self._trailing_stop or float('inf')):
                    self._trailing_stop = new_trail
                    trade.stop_price = new_trail
            
            if self._trailing_stop and price > self._trailing_stop:
                self._last_exit_bar = i
                return "TRAIL"
            
            if self._daily_trend(i) == "UP":
                self._last_exit_bar = i
                return "TREND_FLIP"
        
        return None
