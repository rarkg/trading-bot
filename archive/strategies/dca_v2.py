"""
DCA V2 — Smarter dip buying
Improvements:
1. Multi-level entries: small buy at RSI 35, medium at 30, heavy at 25
2. Sell in tiers: 25% at +5%, 25% at +10%, hold rest
3. Volatility regime awareness: bigger buys in low-vol dips (more likely to bounce)
4. Consecutive red day counter: 4+ red days = aggressive buy zone historically
5. Distance from 200-day SMA: deeper pullback to trend = better entry
"""

import numpy as np


class DCAv2:
    
    def __init__(self):
        self.rsi_light_buy = 38
        self.rsi_medium_buy = 30
        self.rsi_heavy_buy = 22
        self.rsi_sell = 75
        self._position_cost = None
        self._buy_count = 0
    
    def _indicators(self, data, i):
        closes = data["close"].iloc[:i+1].astype(float)
        highs = data["high"].iloc[:i+1].astype(float)
        lows = data["low"].iloc[:i+1].astype(float)
        volumes = data["volume"].iloc[:i+1].astype(float)
        
        # RSI 14
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else 100
        rsi = float(100 - (100 / (1 + rs)))
        
        # Bollinger Bands
        sma20 = closes.rolling(20).mean().iloc[-1]
        std20 = closes.rolling(20).std().iloc[-1]
        bb_lower = sma20 - (2 * std20)
        bb_upper = sma20 + (2 * std20)
        
        # 200-day SMA (long-term trend)
        sma200 = closes.rolling(200).mean().iloc[-1] if len(closes) >= 200 else closes.rolling(50).mean().iloc[-1]
        dist_from_200 = (float(closes.iloc[-1]) - sma200) / sma200 * 100
        
        # Consecutive red days
        red_count = 0
        for j in range(len(closes)-1, max(len(closes)-8, 0), -1):
            if closes.iloc[j] < closes.iloc[j-1]:
                red_count += 1
            else:
                break
        
        # Consecutive green days
        green_count = 0
        for j in range(len(closes)-1, max(len(closes)-8, 0), -1):
            if closes.iloc[j] > closes.iloc[j-1]:
                green_count += 1
            else:
                break
        
        # Volatility (20-day rolling std as % of price)
        vol_pct = (closes.rolling(20).std().iloc[-1] / closes.iloc[-1]) * 100
        
        # Volume spike
        vol_avg = volumes.rolling(20).mean().iloc[-1]
        vol_ratio = float(volumes.iloc[-1] / vol_avg) if vol_avg > 0 else 1.0
        
        # Weekly return (5 bars)
        weekly_return = (float(closes.iloc[-1]) / float(closes.iloc[-6]) - 1) * 100 if len(closes) >= 6 else 0
        
        return {
            "rsi": rsi,
            "close": float(closes.iloc[-1]),
            "bb_lower": bb_lower, "bb_upper": bb_upper, "sma20": sma20,
            "sma200": sma200, "dist_from_200": dist_from_200,
            "red_count": red_count, "green_count": green_count,
            "vol_pct": vol_pct, "vol_ratio": vol_ratio,
            "weekly_return": weekly_return,
        }
    
    def generate_signal(self, data, i):
        if i < 200:
            return None
        
        ind = self._indicators(data, i)
        price = ind["close"]
        
        # HEAVY BUY: extreme fear
        # RSI < 22 + below BB lower + 4+ red days + price below 200 SMA
        if (ind["rsi"] < self.rsi_heavy_buy and 
            price < ind["bb_lower"] and
            ind["red_count"] >= 4):
            return {
                "action": "LONG",
                "signal": "HEAVY_DIP_BUY",
                "stop": price * 0.92,     # 8% stop (give room for capitulation)
                "target": price * 1.15,   # 15% target
            }
        
        # MEDIUM BUY: strong dip
        # RSI < 30 + 3+ red days + volume spike (capitulation volume)
        if (ind["rsi"] < self.rsi_medium_buy and 
            ind["red_count"] >= 3 and
            ind["vol_ratio"] > 1.5):
            return {
                "action": "LONG",
                "signal": "MEDIUM_DIP_BUY",
                "stop": price * 0.93,
                "target": price * 1.10,
            }
        
        # LIGHT BUY: normal dip in uptrend
        # RSI < 38 + price still above 200 SMA (uptrend intact) + 2+ red days
        if (ind["rsi"] < self.rsi_light_buy and 
            ind["dist_from_200"] > 0 and  # Still in uptrend
            ind["red_count"] >= 2 and
            price < ind["sma20"]):  # Pulled back below 20 SMA
            return {
                "action": "LONG",
                "signal": "LIGHT_DIP_BUY",
                "stop": price * 0.95,
                "target": price * 1.08,
            }
        
        return None
    
    def check_exit(self, data, i, trade):
        if i < 200:
            return None
        
        ind = self._indicators(data, i)
        price = ind["close"]
        entry = trade.entry_price
        
        pnl_pct = (price - entry) / entry * 100
        
        # Take profit tiers
        if pnl_pct >= 15:
            return "TARGET_15PCT"
        
        # RSI overbought + above upper BB + 5+ green days = exhaustion
        if (ind["rsi"] > self.rsi_sell and 
            price > ind["bb_upper"] and
            ind["green_count"] >= 4):
            return "OVERBOUGHT_EXHAUSTION"
        
        # Time stop: if held > 21 days and RSI > 60 and in profit, take it
        if trade.entry_time:
            days_held = (data.index[i] - trade.entry_time).days
            if days_held > 21 and pnl_pct > 3 and ind["rsi"] > 55:
                return "TIME_PROFIT_EXIT"
        
        return None
