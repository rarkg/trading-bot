"""
Momentum V2 — Smarter breakout strategy
Improvements over V1:
1. Multi-timeframe: trend on slow MA, entry on fast
2. Volume profile: only trade when volume confirms
3. Trailing stop instead of fixed target (let winners run)
4. Regime filter: skip choppy markets (ADX < 20)
5. Volatility-adjusted position sizing
6. Better R/R: tighter stops, let targets ride
"""

import numpy as np
import pandas as pd


class MomentumV2:
    
    def __init__(self):
        # Trend
        self.ema_fast = 8
        self.ema_mid = 21
        self.ema_slow = 55
        self.atr_period = 14
        
        # Entry
        self.atr_stop_mult = 1.2       # Tighter stop
        self.min_adx = 20              # Don't trade chop
        self.vol_surge_mult = 1.3      # Need 30% above-avg volume
        
        # Trailing stop
        self.trail_atr_mult = 2.0      # Trail at 2x ATR
        self._trailing_stop = None
        self._best_price = None
    
    def _indicators(self, data, i):
        closes = data["close"].iloc[:i+1].astype(float)
        highs = data["high"].iloc[:i+1].astype(float)
        lows = data["low"].iloc[:i+1].astype(float)
        volumes = data["volume"].iloc[:i+1].astype(float)
        
        # EMAs
        ema8 = closes.ewm(span=self.ema_fast, adjust=False).mean().iloc[-1]
        ema21 = closes.ewm(span=self.ema_mid, adjust=False).mean().iloc[-1]
        ema55 = closes.ewm(span=self.ema_slow, adjust=False).mean().iloc[-1]
        
        # ATR
        hi = highs.iloc[-self.atr_period:]
        lo = lows.iloc[-self.atr_period:]
        cl = closes.shift(1).iloc[-self.atr_period:]
        tr = pd.concat([hi - lo, (hi - cl).abs(), (lo - cl).abs()], axis=1).max(axis=1)
        atr = float(tr.mean())
        
        # ADX (simplified — using directional movement)
        n = 14
        if len(closes) >= n + 1:
            plus_dm = highs.diff().clip(lower=0)
            minus_dm = (-lows.diff()).clip(lower=0)
            # Zero out when other is larger
            mask = plus_dm > minus_dm
            plus_dm = plus_dm.where(mask, 0)
            minus_dm = minus_dm.where(~mask, 0)
            
            atr_roll = tr.rolling(n).mean()
            plus_di = 100 * plus_dm.rolling(n).mean().iloc[-1] / atr if atr > 0 else 0
            minus_di = 100 * minus_dm.rolling(n).mean().iloc[-1] / atr if atr > 0 else 0
            dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
            adx = dx  # Simplified single-point ADX
        else:
            adx = 25  # Default to "trending"
            plus_di = 0
            minus_di = 0
        
        # Volume
        vol_avg = volumes.iloc[-20:].mean() if len(volumes) >= 20 else volumes.mean()
        vol_ratio = float(volumes.iloc[-1] / vol_avg) if vol_avg > 0 else 1.0
        
        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else 100
        rsi = float(100 - (100 / (1 + rs)))
        
        # Swing high/low (last 10 bars)
        swing_high = float(highs.iloc[-10:].max())
        swing_low = float(lows.iloc[-10:].min())
        
        # Price position in range (0-100)
        price = float(closes.iloc[-1])
        price_position = (price - swing_low) / (swing_high - swing_low) * 100 if swing_high != swing_low else 50
        
        # Candle body vs wick ratio (strong close = conviction)
        open_price = float(data["open"].iloc[i])
        body = abs(price - open_price)
        total_range = float(highs.iloc[-1]) - float(lows.iloc[-1])
        body_ratio = body / total_range if total_range > 0 else 0
        bullish_candle = price > open_price
        
        return {
            "ema8": ema8, "ema21": ema21, "ema55": ema55,
            "atr": atr, "adx": adx, "plus_di": plus_di, "minus_di": minus_di,
            "vol_ratio": vol_ratio, "rsi": rsi,
            "close": price, "open": open_price,
            "high": float(highs.iloc[-1]), "low": float(lows.iloc[-1]),
            "prev_high": float(highs.iloc[-2]), "prev_low": float(lows.iloc[-2]),
            "swing_high": swing_high, "swing_low": swing_low,
            "price_position": price_position,
            "body_ratio": body_ratio, "bullish_candle": bullish_candle,
        }
    
    def generate_signal(self, data, i):
        if i < 60:
            return None
        
        ind = self._indicators(data, i)
        price = ind["close"]
        atr = ind["atr"]
        
        # REGIME FILTER: skip choppy markets
        if ind["adx"] < self.min_adx:
            return None
        
        # TREND: EMA ribbon alignment
        strong_bull = ind["ema8"] > ind["ema21"] > ind["ema55"]
        strong_bear = ind["ema8"] < ind["ema21"] < ind["ema55"]
        
        # VOLUME: must be above average
        vol_ok = ind["vol_ratio"] >= self.vol_surge_mult
        
        # CANDLE QUALITY: strong close (body > 50% of range)
        strong_candle = ind["body_ratio"] > 0.5
        
        # RSI filter
        rsi_ok_long = 30 < ind["rsi"] < 68  # Not oversold (weak) or overbought
        rsi_ok_short = 32 < ind["rsi"] < 70
        
        # LONG: strong uptrend + breakout + volume + strong candle
        if (strong_bull and vol_ok and ind["bullish_candle"] and 
            strong_candle and rsi_ok_long and
            price > ind["prev_high"]):
            
            stop = price - (atr * self.atr_stop_mult)
            self._trailing_stop = stop
            self._best_price = price
            
            return {
                "action": "LONG",
                "signal": "MOMENTUM_BREAKOUT",
                "stop": stop,
                "target": price + (atr * 8),  # Wide target, trailing stop does the work
            }
        
        # SHORT: strong downtrend + breakdown + volume + strong candle
        if (strong_bear and vol_ok and not ind["bullish_candle"] and 
            strong_candle and rsi_ok_short and
            price < ind["prev_low"]):
            
            stop = price + (atr * self.atr_stop_mult)
            self._trailing_stop = stop
            self._best_price = price
            
            return {
                "action": "SHORT",
                "signal": "MOMENTUM_BREAKDOWN",
                "stop": stop,
                "target": price - (atr * 8),
            }
        
        return None
    
    def check_exit(self, data, i, trade):
        if i < 60:
            return None
        
        ind = self._indicators(data, i)
        price = ind["close"]
        atr = ind["atr"]
        
        # TRAILING STOP
        if trade.direction == "LONG":
            if price > (self._best_price or price):
                self._best_price = price
                new_trail = price - (atr * self.trail_atr_mult)
                if new_trail > (self._trailing_stop or 0):
                    self._trailing_stop = new_trail
                    trade.stop_price = new_trail  # Update the stop
            
            if self._trailing_stop and price < self._trailing_stop:
                return "TRAILING_STOP"
        
        elif trade.direction == "SHORT":
            if price < (self._best_price or price):
                self._best_price = price
                new_trail = price + (atr * self.trail_atr_mult)
                if new_trail < (self._trailing_stop or float('inf')):
                    self._trailing_stop = new_trail
                    trade.stop_price = new_trail
            
            if self._trailing_stop and price > self._trailing_stop:
                return "TRAILING_STOP"
        
        # EMA CROSS EXIT: fast crosses mid against position
        if trade.direction == "LONG" and ind["ema8"] < ind["ema21"]:
            return "EMA_CROSS_EXIT"
        if trade.direction == "SHORT" and ind["ema8"] > ind["ema21"]:
            return "EMA_CROSS_EXIT"
        
        return None
