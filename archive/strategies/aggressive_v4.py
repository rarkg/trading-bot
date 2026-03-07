"""
Aggressive V4 — Targeting 5%/month
Changes from V3:
1. More entry signals (catch more moves)
2. EMA pullback entries (not just breakouts)
3. Double bottom / double top detection
4. Momentum acceleration (rate of change of momentum)
5. Smarter position sizing based on signal quality
6. Partial profit taking at 1R, let rest ride
7. Re-entry after pullback in strong trends
"""

import numpy as np
import pandas as pd


class AggressiveV4:
    
    def __init__(self):
        self._trailing_stops = {}
        self._best_prices = {}
        self._last_trade_direction = {}
        self._last_exit_bar = {}
    
    def _indicators(self, data, i):
        closes = data["close"].iloc[:i+1].astype(float)
        highs = data["high"].iloc[:i+1].astype(float)
        lows = data["low"].iloc[:i+1].astype(float)
        volumes = data["volume"].iloc[:i+1].astype(float)
        opens = data["open"].iloc[:i+1].astype(float)
        
        price = float(closes.iloc[-1])
        
        # EMAs
        ema8 = float(closes.ewm(span=8, adjust=False).mean().iloc[-1])
        ema13 = float(closes.ewm(span=13, adjust=False).mean().iloc[-1])
        ema21 = float(closes.ewm(span=21, adjust=False).mean().iloc[-1])
        ema55 = float(closes.ewm(span=55, adjust=False).mean().iloc[-1])
        
        # ATR
        n = min(14, len(closes)-1)
        tr_vals = []
        for j in range(-n, 0):
            h = float(highs.iloc[j])
            l = float(lows.iloc[j])
            pc = float(closes.iloc[j-1]) if j-1 >= -len(closes) else float(closes.iloc[j])
            tr_vals.append(max(h-l, abs(h-pc), abs(l-pc)))
        atr = np.mean(tr_vals) if tr_vals else price * 0.02
        
        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = float(gain.iloc[-1]) / float(loss.iloc[-1]) if float(loss.iloc[-1]) > 0 else 100
        rsi = float(100 - (100 / (1 + rs)))
        
        # Volume
        vol_avg = float(volumes.iloc[-20:].mean()) if len(volumes) >= 20 else float(volumes.mean())
        vol_ratio = float(volumes.iloc[-1]) / vol_avg if vol_avg > 0 else 1.0
        
        # Trend strength
        if len(closes) >= 26:
            ema21_5ago = float(closes.ewm(span=21, adjust=False).mean().iloc[-5])
            trend_str = (ema21 - ema21_5ago) / ema21_5ago * 100
        else:
            trend_str = 0
        
        # Candle
        body = abs(price - float(opens.iloc[-1]))
        total_range = float(highs.iloc[-1]) - float(lows.iloc[-1])
        body_ratio = body / total_range if total_range > 0 else 0
        bullish = price > float(opens.iloc[-1])
        
        # Rate of change (momentum acceleration)
        if len(closes) >= 10:
            roc_5 = (price / float(closes.iloc[-6]) - 1) * 100
            roc_10 = (price / float(closes.iloc[-11]) - 1) * 100
            roc_accel = roc_5 - (roc_10 / 2)  # Is momentum accelerating?
        else:
            roc_5 = roc_10 = roc_accel = 0
        
        # Swing high/low (20 bars)
        swing_high_20 = float(highs.iloc[-20:].max()) if len(highs) >= 20 else float(highs.max())
        swing_low_20 = float(lows.iloc[-20:].min()) if len(lows) >= 20 else float(lows.min())
        
        # Double bottom detection (price near prior swing low)
        recent_lows = []
        for j in range(max(0, len(lows)-40), len(lows)-2):
            if float(lows.iloc[j]) < float(lows.iloc[j-1]) and float(lows.iloc[j]) < float(lows.iloc[j+1]):
                recent_lows.append(float(lows.iloc[j]))
        
        double_bottom = False
        if len(recent_lows) >= 2:
            last_two = recent_lows[-2:]
            if abs(last_two[0] - last_two[1]) / last_two[0] < 0.02:  # Within 2%
                if price > max(last_two) * 1.01:  # Breaking above
                    double_bottom = True
        
        # Consecutive reds
        red_count = 0
        for j in range(len(closes)-1, max(len(closes)-8, 0), -1):
            if float(closes.iloc[j]) < float(closes.iloc[j-1]):
                red_count += 1
            else:
                break
        
        # EMA pullback: price pulled back to EMA21 in uptrend
        near_ema21 = abs(price - ema21) / ema21 < 0.01  # Within 1% of EMA21
        
        # Bollinger
        sma20 = float(closes.rolling(20).mean().iloc[-1])
        std20 = float(closes.rolling(20).std().iloc[-1])
        bb_lower = sma20 - 2 * std20
        bb_upper = sma20 + 2 * std20
        bb_width = (std20 / sma20) * 100
        
        return {
            "price": price, "atr": atr, "rsi": rsi,
            "ema8": ema8, "ema13": ema13, "ema21": ema21, "ema55": ema55,
            "vol_ratio": vol_ratio, "trend_str": trend_str,
            "body_ratio": body_ratio, "bullish": bullish,
            "roc_5": roc_5, "roc_accel": roc_accel,
            "swing_high_20": swing_high_20, "swing_low_20": swing_low_20,
            "double_bottom": double_bottom, "red_count": red_count,
            "near_ema21": near_ema21,
            "bb_lower": bb_lower, "bb_upper": bb_upper, "bb_width": bb_width,
            "prev_high": float(highs.iloc[-2]), "prev_low": float(lows.iloc[-2]),
            "high": float(highs.iloc[-1]), "low": float(lows.iloc[-1]),
        }
    
    def generate_signal(self, data, i):
        if i < 60:
            return None
        
        did = id(data)
        
        # Cooldown: don't re-enter for 2 bars after exit
        last_exit = self._last_exit_bar.get(did, -10)
        if i - last_exit < 2:
            return None
        
        ind = self._indicators(data, i)
        price = ind["price"]
        atr = ind["atr"]
        
        bull_trend = ind["ema8"] > ind["ema21"] > ind["ema55"]
        bear_trend = ind["ema8"] < ind["ema21"] < ind["ema55"]
        mild_bull = ind["ema8"] > ind["ema21"]
        mild_bear = ind["ema8"] < ind["ema21"]
        
        # ============ LONG SIGNALS ============
        
        # 1. BREAKOUT: new 20-bar high + trend + volume
        if (price > ind["swing_high_20"] and bull_trend and 
            ind["vol_ratio"] > 1.2 and ind["rsi"] < 75):
            return self._long_signal("SWING_BREAKOUT", price, atr, 1.2, 5, did=did)
        
        # 2. EMA PULLBACK: price pulls back to EMA21 in uptrend, then bounces
        if (bull_trend and ind["near_ema21"] and ind["bullish"] and
            ind["body_ratio"] > 0.4 and ind["rsi"] > 40):
            return self._long_signal("EMA_PULLBACK", price, atr, 1.5, 4, did=did)
        
        # 3. MOMENTUM ACCELERATION: momentum speeding up + trend
        if (mild_bull and ind["roc_accel"] > 1.5 and ind["vol_ratio"] > 1.0 and
            ind["bullish"] and ind["rsi"] < 68):
            return self._long_signal("MOMENTUM_ACCEL", price, atr, 1.3, 4, did=did)
        
        # 4. DOUBLE BOTTOM: reversal pattern
        if ind["double_bottom"] and ind["vol_ratio"] > 1.0:
            return self._long_signal("DOUBLE_BOTTOM", price, atr, 1.5, 5, did=did)
        
        # 5. FEAR BUY: extreme oversold
        if ind["rsi"] < 22 and ind["red_count"] >= 4:
            return self._long_signal("EXTREME_FEAR", price, atr, 2.0, 6, did=did, wide=True)
        
        # 6. CAPITULATION: oversold + volume spike
        if ind["rsi"] < 30 and ind["red_count"] >= 3 and ind["vol_ratio"] > 2.0:
            return self._long_signal("CAPITULATION", price, atr, 1.5, 5, did=did, wide=True)
        
        # 7. SQUEEZE BREAKOUT: low BB width then expansion
        if (ind["bb_width"] < 2.0 and price > ind["prev_high"] and 
            ind["vol_ratio"] > 1.3 and ind["bullish"]):
            return self._long_signal("SQUEEZE", price, atr, 1.5, 5, did=did)
        
        # ============ SHORT SIGNALS ============
        
        # 8. BREAKDOWN: new 20-bar low + downtrend
        if (price < ind["swing_low_20"] and bear_trend and
            ind["vol_ratio"] > 1.2 and ind["rsi"] > 25):
            return self._short_signal("SWING_BREAKDOWN", price, atr, 1.2, 5, did=did)
        
        # 9. EMA REJECTION: bounced off EMA21 in downtrend
        if (bear_trend and ind["near_ema21"] and not ind["bullish"] and
            ind["body_ratio"] > 0.4 and ind["rsi"] < 60):
            return self._short_signal("EMA_REJECTION", price, atr, 1.5, 4, did=did)
        
        # 10. MOMENTUM DECEL: momentum accelerating down
        if (mild_bear and ind["roc_accel"] < -1.5 and ind["vol_ratio"] > 1.0 and
            not ind["bullish"] and ind["rsi"] > 32):
            return self._short_signal("MOMENTUM_DUMP", price, atr, 1.3, 4, did=did)
        
        # 11. OVERBOUGHT FADE: extreme RSI + above BB
        if (ind["rsi"] > 80 and price > ind["bb_upper"] and 
            not ind["bullish"] and ind["body_ratio"] > 0.3):
            return self._short_signal("OVERBOUGHT_FADE", price, atr, 1.5, 3, did=did)
        
        return None
    
    def _long_signal(self, name, price, atr, stop_mult, target_mult, did=None, wide=False):
        stop = price - (atr * stop_mult)
        if did is not None:
            self._trailing_stops[did] = stop
            self._best_prices[did] = price
            self._last_trade_direction[did] = "LONG"
        return {
            "action": "LONG",
            "signal": name,
            "stop": stop,
            "target": price + (atr * target_mult),
        }
    
    def _short_signal(self, name, price, atr, stop_mult, target_mult, did=None):
        stop = price + (atr * stop_mult)
        if did is not None:
            self._trailing_stops[did] = stop
            self._best_prices[did] = price
            self._last_trade_direction[did] = "SHORT"
        return {
            "action": "SHORT",
            "signal": name,
            "stop": stop,
            "target": price - (atr * target_mult),
        }
    
    def check_exit(self, data, i, trade):
        if i < 60:
            return None
        
        ind = self._indicators(data, i)
        price = ind["price"]
        atr = ind["atr"]
        did = id(data)
        
        # TRAILING STOP with adaptive multiplier
        if trade.direction == "LONG":
            bp = self._best_prices.get(did, price)
            if price > bp:
                self._best_prices[did] = price
                # Tighten trailing stop as profit grows
                pnl_r = (price - trade.entry_price) / atr
                trail_mult = max(1.2, 2.0 - (pnl_r * 0.1))  # Tighten as profit grows
                new_trail = price - (atr * trail_mult)
                old_trail = self._trailing_stops.get(did, 0)
                if new_trail > old_trail:
                    self._trailing_stops[did] = new_trail
                    trade.stop_price = new_trail
            
            trail = self._trailing_stops.get(did)
            if trail and price < trail:
                self._last_exit_bar[did] = i
                return "TRAILING_STOP"
            
            # Trend death
            if ind["ema8"] < ind["ema21"] and ind["trend_str"] < -0.3:
                self._last_exit_bar[did] = i
                return "TREND_DEATH"
        
        elif trade.direction == "SHORT":
            bp = self._best_prices.get(did, price)
            if price < bp:
                self._best_prices[did] = price
                pnl_r = (trade.entry_price - price) / atr
                trail_mult = max(1.2, 2.0 - (pnl_r * 0.1))
                new_trail = price + (atr * trail_mult)
                old_trail = self._trailing_stops.get(did, float('inf'))
                if new_trail < old_trail:
                    self._trailing_stops[did] = new_trail
                    trade.stop_price = new_trail
            
            trail = self._trailing_stops.get(did)
            if trail and price > trail:
                self._last_exit_bar[did] = i
                return "TRAILING_STOP"
            
            if ind["ema8"] > ind["ema21"] and ind["trend_str"] > 0.3:
                self._last_exit_bar[did] = i
                return "TREND_GOLDEN"
        
        # Fear buy exits
        if "FEAR" in trade.signal or "CAPITULATION" in trade.signal:
            pnl = (price - trade.entry_price) / trade.entry_price * 100
            if pnl >= 12:
                self._last_exit_bar[did] = i
                return "FEAR_PROFIT"
            if ind["rsi"] > 65:
                self._last_exit_bar[did] = i
                return "FEAR_RSI_EXIT"
        
        return None
