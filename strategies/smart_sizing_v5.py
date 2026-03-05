"""
Smart Sizing V5 — Dynamic leverage based on signal confidence
Core idea: Kelly Criterion meets multi-factor scoring
- Score each signal 0-100 based on factor alignment
- Map score to leverage: 50→1x, 70→2x, 85→3x, 95→5x
- Position size scales with score AND leverage
- This is THE edge: bet big when everything aligns, small when it doesn't

Optimized for hourly data: pre-compute indicators vectorized,
only do per-bar signal logic.
"""

import numpy as np
import pandas as pd


class SmartSizingV5:
    
    def __init__(self):
        self._precomputed = False
        self._ind = None
        self._trailing_stop = None
        self._best_price = None
        self._last_exit_bar = -10
    
    def _precompute(self, data):
        """Vectorized indicator computation — runs once, O(n) not O(n²)."""
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)
        opens = data["open"].astype(float)
        
        # EMAs
        ema8 = closes.ewm(span=8, adjust=False).mean()
        ema13 = closes.ewm(span=13, adjust=False).mean()
        ema21 = closes.ewm(span=21, adjust=False).mean()
        ema55 = closes.ewm(span=55, adjust=False).mean()
        
        # ATR (14-period)
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        
        # RSI (14-period)
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))
        
        # Volume ratio (vs 20-period avg)
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)
        
        # EMA21 slope (5-bar)
        ema21_slope = (ema21 - ema21.shift(5)) / ema21.shift(5).replace(0, 1) * 100
        
        # Rate of change
        roc_5 = (closes / closes.shift(5).replace(0, 1) - 1) * 100
        roc_10 = (closes / closes.shift(10).replace(0, 1) - 1) * 100
        roc_accel = roc_5 - (roc_10 / 2)
        
        # Bollinger Bands
        sma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        bb_upper = sma20 + 2 * std20
        bb_lower = sma20 - 2 * std20
        bb_width = (std20 / sma20.replace(0, 1)) * 100
        
        # Candle properties
        body = (closes - opens).abs()
        candle_range = (highs - lows).replace(0, 1)
        body_ratio = body / candle_range
        bullish = closes > opens
        
        # Swing high/low (20-bar rolling)
        swing_high = highs.rolling(20).max()
        swing_low = lows.rolling(20).min()
        
        # Consecutive red/green
        is_red = closes < closes.shift(1)
        red_count = pd.Series(0, index=data.index, dtype=float)
        for idx in range(1, len(is_red)):
            if is_red.iloc[idx]:
                red_count.iloc[idx] = red_count.iloc[idx-1] + 1
            else:
                red_count.iloc[idx] = 0
        
        # Near EMA21 (within 0.5%)
        near_ema21 = ((closes - ema21).abs() / ema21.replace(0, 1)) < 0.005
        
        self._ind = pd.DataFrame({
            "close": closes, "open": opens, "high": highs, "low": lows,
            "volume": volumes,
            "ema8": ema8, "ema13": ema13, "ema21": ema21, "ema55": ema55,
            "atr": atr, "rsi": rsi, "vol_ratio": vol_ratio,
            "ema21_slope": ema21_slope,
            "roc_5": roc_5, "roc_accel": roc_accel,
            "bb_upper": bb_upper, "bb_lower": bb_lower, "bb_width": bb_width,
            "body_ratio": body_ratio, "bullish": bullish,
            "swing_high": swing_high, "swing_low": swing_low,
            "red_count": red_count, "near_ema21": near_ema21,
            "prev_high": highs.shift(1), "prev_low": lows.shift(1),
        })
        self._precomputed = True
    
    def _score_signal(self, i, direction="LONG"):
        """Score signal quality 0-100 for dynamic sizing."""
        ind = self._ind.iloc[i]
        score = 0
        
        if direction == "LONG":
            # Trend alignment (0-25)
            if ind["ema8"] > ind["ema21"] > ind["ema55"]:
                score += 25
            elif ind["ema8"] > ind["ema21"]:
                score += 12
            
            # Volume confirmation (0-15)
            if ind["vol_ratio"] > 2.0:
                score += 15
            elif ind["vol_ratio"] > 1.3:
                score += 8
            
            # RSI sweet spot (0-10)
            if 40 < ind["rsi"] < 60:
                score += 10
            elif 30 < ind["rsi"] < 70:
                score += 5
            
            # Strong bullish candle (0-10)
            if ind["body_ratio"] > 0.6 and ind["bullish"]:
                score += 10
            elif ind["body_ratio"] > 0.4 and ind["bullish"]:
                score += 5
            
            # Momentum accelerating (0-15)
            if ind["roc_accel"] > 2.0:
                score += 15
            elif ind["roc_accel"] > 1.0:
                score += 8
            
            # Trend strength (0-15)
            if ind["ema21_slope"] > 1.0:
                score += 15
            elif ind["ema21_slope"] > 0.3:
                score += 8
            
            # Breakout confirmation (0-10)
            if ind["close"] > ind["swing_high"]:
                score += 10
        
        else:  # SHORT
            if ind["ema8"] < ind["ema21"] < ind["ema55"]:
                score += 25
            elif ind["ema8"] < ind["ema21"]:
                score += 12
            
            if ind["vol_ratio"] > 2.0:
                score += 15
            elif ind["vol_ratio"] > 1.3:
                score += 8
            
            if 40 < ind["rsi"] < 60:
                score += 10
            elif 30 < ind["rsi"] < 70:
                score += 5
            
            if ind["body_ratio"] > 0.6 and not ind["bullish"]:
                score += 10
            elif ind["body_ratio"] > 0.4 and not ind["bullish"]:
                score += 5
            
            if ind["roc_accel"] < -2.0:
                score += 15
            elif ind["roc_accel"] < -1.0:
                score += 8
            
            if ind["ema21_slope"] < -1.0:
                score += 15
            elif ind["ema21_slope"] < -0.3:
                score += 8
            
            if ind["close"] < ind["swing_low"]:
                score += 10
        
        return min(score, 100)
    
    def _score_to_leverage(self, score):
        """Map confidence score to leverage multiplier."""
        if score >= 90:
            return 4.0
        elif score >= 80:
            return 3.0
        elif score >= 70:
            return 2.5
        elif score >= 60:
            return 2.0
        elif score >= 50:
            return 1.5
        else:
            return 1.0
    
    def _score_to_size_pct(self, score):
        """Map confidence to position size as % of capital."""
        if score >= 90:
            return 0.40   # 40% of capital
        elif score >= 80:
            return 0.30
        elif score >= 70:
            return 0.25
        elif score >= 60:
            return 0.20
        elif score >= 50:
            return 0.15
        else:
            return 0.10
    
    def generate_signal(self, data, i):
        if not self._precomputed:
            self._precompute(data)
        
        if i < 60 or i >= len(self._ind):
            return None
        
        # Cooldown
        if i - self._last_exit_bar < 3:
            return None
        
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        
        if pd.isna(atr) or atr <= 0:
            return None
        
        bull_trend = ind["ema8"] > ind["ema21"] > ind["ema55"]
        bear_trend = ind["ema8"] < ind["ema21"] < ind["ema55"]
        mild_bull = ind["ema8"] > ind["ema21"]
        mild_bear = ind["ema8"] < ind["ema21"]
        
        # ============ LONG SIGNALS ============
        
        # 1. Breakout: new swing high + trend + volume
        if (price > ind["swing_high"] and bull_trend and 
            ind["vol_ratio"] > 1.2 and ind["rsi"] < 75):
            score = self._score_signal(i, "LONG")
            if score >= 50:
                return self._make_long(f"BREAKOUT(s{score})", price, atr, 1.2, score)
        
        # 2. EMA pullback: price touches EMA21 in uptrend, bounces
        if (bull_trend and ind["near_ema21"] and ind["bullish"] and
            ind["body_ratio"] > 0.4):
            score = self._score_signal(i, "LONG")
            if score >= 45:
                return self._make_long(f"PULLBACK(s{score})", price, atr, 1.5, score)
        
        # 3. Momentum acceleration
        if (mild_bull and ind["roc_accel"] > 1.5 and ind["vol_ratio"] > 1.0 and
            ind["bullish"] and ind["rsi"] < 70):
            score = self._score_signal(i, "LONG")
            if score >= 50:
                return self._make_long(f"ACCEL(s{score})", price, atr, 1.3, score)
        
        # 4. Fear buy: extreme oversold
        if ind["rsi"] < 22 and ind["red_count"] >= 5:
            score = 75  # High conviction reversal
            return self._make_long(f"EXTREME_FEAR(s{score})", price, atr, 2.0, score)
        
        # 5. Capitulation: oversold + volume spike
        if ind["rsi"] < 30 and ind["red_count"] >= 3 and ind["vol_ratio"] > 2.0:
            score = 65
            return self._make_long(f"CAPITULATION(s{score})", price, atr, 1.5, score)
        
        # 6. Squeeze breakout
        if (ind["bb_width"] < 2.0 and price > ind["prev_high"] and
            ind["vol_ratio"] > 1.3 and ind["bullish"] and mild_bull):
            score = self._score_signal(i, "LONG")
            if score >= 50:
                return self._make_long(f"SQUEEZE(s{score})", price, atr, 1.3, score)
        
        # ============ SHORT SIGNALS ============
        
        # 7. Breakdown
        if (price < ind["swing_low"] and bear_trend and
            ind["vol_ratio"] > 1.2 and ind["rsi"] > 25):
            score = self._score_signal(i, "SHORT")
            if score >= 50:
                return self._make_short(f"BREAKDOWN(s{score})", price, atr, 1.2, score)
        
        # 8. EMA rejection in downtrend
        if (bear_trend and ind["near_ema21"] and not ind["bullish"] and
            ind["body_ratio"] > 0.4):
            score = self._score_signal(i, "SHORT")
            if score >= 45:
                return self._make_short(f"REJECTION(s{score})", price, atr, 1.5, score)
        
        # 9. Momentum dump
        if (mild_bear and ind["roc_accel"] < -1.5 and ind["vol_ratio"] > 1.0 and
            not ind["bullish"] and ind["rsi"] > 30):
            score = self._score_signal(i, "SHORT")
            if score >= 50:
                return self._make_short(f"DUMP(s{score})", price, atr, 1.3, score)
        
        # 10. Overbought fade
        if (ind["rsi"] > 82 and price > ind["bb_upper"] and 
            not ind["bullish"] and ind["body_ratio"] > 0.3):
            score = 60
            return self._make_short(f"OB_FADE(s{score})", price, atr, 1.5, score)
        
        return None
    
    def _make_long(self, name, price, atr, stop_mult, score):
        stop = price - (atr * stop_mult)
        self._trailing_stop = stop
        self._best_price = price
        lev = self._score_to_leverage(score)
        return {
            "action": "LONG", "signal": name,
            "stop": stop,
            "target": price + (atr * 6),
            "score": score, "leverage": lev,
        }
    
    def _make_short(self, name, price, atr, stop_mult, score):
        stop = price + (atr * stop_mult)
        self._trailing_stop = stop
        self._best_price = price
        lev = self._score_to_leverage(score)
        return {
            "action": "SHORT", "signal": name,
            "stop": stop,
            "target": price - (atr * 6),
            "score": score, "leverage": lev,
        }
    
    def check_exit(self, data, i, trade):
        if not self._precomputed or i >= len(self._ind):
            return None
        
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        
        if pd.isna(atr) or atr <= 0:
            return None
        
        # Adaptive trailing stop
        if trade.direction == "LONG":
            if price > (self._best_price or price):
                self._best_price = price
                pnl_r = (price - trade.entry_price) / atr
                trail_mult = max(1.0, 1.8 - (pnl_r * 0.08))
                new_trail = price - (atr * trail_mult)
                if new_trail > (self._trailing_stop or 0):
                    self._trailing_stop = new_trail
                    trade.stop_price = new_trail
            
            if self._trailing_stop and price < self._trailing_stop:
                self._last_exit_bar = i
                return "TRAILING_STOP"
            
            if ind["ema8"] < ind["ema21"] and ind["ema21_slope"] < -0.2:
                self._last_exit_bar = i
                return "TREND_DEATH"
        
        elif trade.direction == "SHORT":
            if price < (self._best_price or price):
                self._best_price = price
                pnl_r = (trade.entry_price - price) / atr
                trail_mult = max(1.0, 1.8 - (pnl_r * 0.08))
                new_trail = price + (atr * trail_mult)
                if new_trail < (self._trailing_stop or float('inf')):
                    self._trailing_stop = new_trail
                    trade.stop_price = new_trail
            
            if self._trailing_stop and price > self._trailing_stop:
                self._last_exit_bar = i
                return "TRAILING_STOP"
            
            if ind["ema8"] > ind["ema21"] and ind["ema21_slope"] > 0.2:
                self._last_exit_bar = i
                return "TREND_GOLDEN"
        
        # Fear buy profit taking
        if "FEAR" in trade.signal or "CAPITULATION" in trade.signal:
            pnl = (price - trade.entry_price) / trade.entry_price * 100
            if pnl >= 10:
                self._last_exit_bar = i
                return "FEAR_PROFIT"
        
        return None
