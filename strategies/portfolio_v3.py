"""
Portfolio V3 — Multi-asset regime-aware trading
Key changes from V2:
1. Trade BTC + ETH + SOL simultaneously (diversification)
2. More aggressive position sizing (up to 40% per trade when high conviction)
3. Compound gains (reinvest profits)
4. BTC dominance rotation: when BTC strong → BTC; when BTC weak → alts
5. Tighter entries but bigger bets
6. Pyramid into winners (add to winning positions)
"""

import numpy as np
import pandas as pd


class PortfolioV3:
    """Multi-asset momentum + DCA hybrid with aggressive sizing."""
    
    def __init__(self):
        self._trailing_stops = {}
        self._best_prices = {}
    
    def _indicators(self, data, i):
        closes = data["close"].iloc[:i+1].astype(float)
        highs = data["high"].iloc[:i+1].astype(float)
        lows = data["low"].iloc[:i+1].astype(float)
        volumes = data["volume"].iloc[:i+1].astype(float)
        
        price = float(closes.iloc[-1])
        
        # EMAs
        ema8 = closes.ewm(span=8, adjust=False).mean().iloc[-1]
        ema21 = closes.ewm(span=21, adjust=False).mean().iloc[-1]
        ema55 = closes.ewm(span=55, adjust=False).mean().iloc[-1]
        
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
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else 100
        rsi = float(100 - (100 / (1 + rs)))
        
        # Volume
        vol_avg = volumes.iloc[-20:].mean() if len(volumes) >= 20 else volumes.mean()
        vol_ratio = float(volumes.iloc[-1] / vol_avg) if vol_avg > 0 else 1.0
        
        # Trend strength (EMA slope over 5 bars)
        if len(closes) >= 26:
            ema21_5ago = closes.ewm(span=21, adjust=False).mean().iloc[-5]
            trend_strength = (ema21 - ema21_5ago) / ema21_5ago * 100
        else:
            trend_strength = 0
        
        # Candle quality
        open_price = float(data["open"].iloc[i])
        body = abs(price - open_price)
        total_range = float(highs.iloc[-1]) - float(lows.iloc[-1])
        body_ratio = body / total_range if total_range > 0 else 0
        bullish = price > open_price
        
        # 50-bar momentum
        mom50 = (price / float(closes.iloc[-50]) - 1) * 100 if len(closes) >= 50 else 0
        
        # Consecutive reds
        red_count = 0
        for j in range(len(closes)-1, max(len(closes)-8, 0), -1):
            if closes.iloc[j] < closes.iloc[j-1]:
                red_count += 1
            else:
                break
        
        # Bollinger squeeze (low vol = explosion coming)
        bb_width = (closes.rolling(20).std().iloc[-1] / closes.rolling(20).mean().iloc[-1]) * 100 if len(closes) >= 20 else 5
        
        return {
            "price": price, "atr": atr, "rsi": rsi,
            "ema8": ema8, "ema21": ema21, "ema55": ema55,
            "vol_ratio": vol_ratio, "trend_strength": trend_strength,
            "body_ratio": body_ratio, "bullish": bullish,
            "mom50": mom50, "red_count": red_count,
            "bb_width": bb_width,
            "prev_high": float(highs.iloc[-2]),
            "prev_low": float(lows.iloc[-2]),
        }
    
    def _score_signal(self, ind):
        """Score signal quality 0-100 for position sizing."""
        score = 0
        
        # Trend alignment (0-30)
        if ind["ema8"] > ind["ema21"] > ind["ema55"]:
            score += 30
        elif ind["ema8"] > ind["ema21"]:
            score += 15
        
        # Volume confirmation (0-20)
        if ind["vol_ratio"] > 2.0:
            score += 20
        elif ind["vol_ratio"] > 1.3:
            score += 10
        
        # RSI in sweet spot (0-15)
        if 40 < ind["rsi"] < 65:
            score += 15
        elif 30 < ind["rsi"] < 70:
            score += 8
        
        # Strong candle (0-15)
        if ind["body_ratio"] > 0.6 and ind["bullish"]:
            score += 15
        elif ind["body_ratio"] > 0.4:
            score += 8
        
        # Trend strength (0-20)
        if ind["trend_strength"] > 1.0:
            score += 20
        elif ind["trend_strength"] > 0.5:
            score += 10
        
        return min(score, 100)
    
    def generate_signal(self, data, i):
        if i < 60:
            return None
        
        ind = self._indicators(data, i)
        price = ind["price"]
        atr = ind["atr"]
        
        # === MOMENTUM ENTRY (trending markets) ===
        strong_trend = ind["ema8"] > ind["ema21"] > ind["ema55"]
        vol_ok = ind["vol_ratio"] > 1.2
        breakout = price > ind["prev_high"]
        strong_candle = ind["body_ratio"] > 0.45 and ind["bullish"]
        rsi_ok = 35 < ind["rsi"] < 72
        trend_ok = ind["trend_strength"] > 0.3
        
        if strong_trend and vol_ok and breakout and strong_candle and rsi_ok and trend_ok:
            score = self._score_signal(ind)
            stop = price - (atr * 1.2)
            
            self._trailing_stops[id(data)] = stop
            self._best_prices[id(data)] = price
            
            return {
                "action": "LONG",
                "signal": f"MOMENTUM_BREAK (score:{score})",
                "stop": stop,
                "target": price + (atr * 6),  # Let it run
                "score": score,
            }
        
        # === DIP BUY (fear entries) ===
        bearish_trend = ind["ema8"] < ind["ema21"]
        
        # Extreme fear: RSI < 25 + 4+ red days
        if ind["rsi"] < 25 and ind["red_count"] >= 4:
            return {
                "action": "LONG",
                "signal": "EXTREME_FEAR_BUY",
                "stop": price * 0.90,    # 10% stop (wide for capitulation)
                "target": price * 1.20,  # 20% target
                "score": 70,
            }
        
        # Moderate fear: RSI < 32 + 3+ red days + volume spike (capitulation)
        if ind["rsi"] < 32 and ind["red_count"] >= 3 and ind["vol_ratio"] > 1.5:
            return {
                "action": "LONG",
                "signal": "CAPITULATION_BUY",
                "stop": price * 0.92,
                "target": price * 1.15,
                "score": 60,
            }
        
        # === SQUEEZE BREAKOUT (Bollinger squeeze → expansion) ===
        if (ind["bb_width"] < 2.5 and  # Tight squeeze
            breakout and vol_ok and ind["bullish"] and
            ind["trend_strength"] > 0):
            return {
                "action": "LONG",
                "signal": "SQUEEZE_BREAKOUT",
                "stop": price - (atr * 1.5),
                "target": price + (atr * 5),
                "score": 65,
            }
        
        # === SHORT (strong downtrend only) ===
        strong_bear = ind["ema8"] < ind["ema21"] < ind["ema55"]
        breakdown = price < ind["prev_low"]
        bearish_candle = ind["body_ratio"] > 0.45 and not ind["bullish"]
        
        if strong_bear and vol_ok and breakdown and bearish_candle and ind["trend_strength"] < -0.3:
            score = self._score_signal(ind)
            stop = price + (atr * 1.2)
            
            self._trailing_stops[id(data)] = stop
            self._best_prices[id(data)] = price
            
            return {
                "action": "SHORT",
                "signal": f"MOMENTUM_BREAK_DOWN (score:{score})",
                "stop": stop,
                "target": price - (atr * 6),
                "score": score,
            }
        
        return None
    
    def check_exit(self, data, i, trade):
        if i < 60:
            return None
        
        ind = self._indicators(data, i)
        price = ind["price"]
        atr = ind["atr"]
        did = id(data)
        
        # TRAILING STOP
        if trade.direction == "LONG":
            bp = self._best_prices.get(did, price)
            if price > bp:
                self._best_prices[did] = price
                new_trail = price - (atr * 1.8)
                old_trail = self._trailing_stops.get(did, 0)
                if new_trail > old_trail:
                    self._trailing_stops[did] = new_trail
                    trade.stop_price = new_trail
            
            trail = self._trailing_stops.get(did)
            if trail and price < trail:
                return "TRAILING_STOP"
            
            # EMA death cross
            if ind["ema8"] < ind["ema21"] and ind["trend_strength"] < -0.2:
                return "TREND_DEATH"
        
        elif trade.direction == "SHORT":
            bp = self._best_prices.get(did, price)
            if price < bp:
                self._best_prices[did] = price
                new_trail = price + (atr * 1.8)
                old_trail = self._trailing_stops.get(did, float('inf'))
                if new_trail < old_trail:
                    self._trailing_stops[did] = new_trail
                    trade.stop_price = new_trail
            
            trail = self._trailing_stops.get(did)
            if trail and price > trail:
                return "TRAILING_STOP"
            
            if ind["ema8"] > ind["ema21"] and ind["trend_strength"] > 0.2:
                return "TREND_GOLDEN"
        
        # Profit take on fear buys
        if "FEAR" in trade.signal or "CAPITULATION" in trade.signal:
            pnl = (price - trade.entry_price) / trade.entry_price * 100
            if pnl >= 15:
                return "FEAR_PROFIT_15PCT"
            if ind["rsi"] > 70:
                return "FEAR_RSI_EXIT"
        
        return None
