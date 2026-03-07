"""
AI-Timed DCA Strategy
- Buys BTC/ETH on dips using RSI + volatility signals
- Sells partial at overbought levels
- Core holding maintained, trade around the edges
- Designed for spot-only exchanges (no leverage, no shorts)
"""

import numpy as np


class DCATiming:
    """Buy fear, sell greed. Hold core position."""
    
    def __init__(self, rsi_buy=35, rsi_sell=72, rsi_heavy_buy=25,
                 hold_pct=0.6, trade_pct=0.4):
        self.rsi_buy = rsi_buy          # RSI level to start buying
        self.rsi_sell = rsi_sell        # RSI level to start selling
        self.rsi_heavy_buy = rsi_heavy_buy  # RSI level for aggressive buys
        self.hold_pct = hold_pct        # % of capital always in position
        self.trade_pct = trade_pct      # % of capital for active trading
    
    def _calc_indicators(self, data, i):
        closes = data["close"].iloc[:i+1].astype(float)
        
        # RSI 14
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] > 0 else 100
        rsi = 100 - (100 / (1 + rs))
        
        # Bollinger Bands (20, 2)
        sma20 = closes.rolling(20).mean().iloc[-1]
        std20 = closes.rolling(20).std().iloc[-1]
        bb_lower = sma20 - (2 * std20)
        bb_upper = sma20 + (2 * std20)
        
        # Price vs 50-day SMA (trend)
        sma50 = closes.rolling(50).mean().iloc[-1]
        
        # Consecutive red/green candles
        recent = closes.iloc[-5:]
        red_count = sum(1 for j in range(1, len(recent)) if recent.iloc[j] < recent.iloc[j-1])
        green_count = 5 - red_count
        
        return {
            "rsi": rsi,
            "close": float(closes.iloc[-1]),
            "bb_lower": bb_lower,
            "bb_upper": bb_upper,
            "sma50": sma50,
            "red_count": red_count,
            "green_count": green_count,
        }
    
    def generate_signal(self, data, i):
        """Generate buy/sell signal. LONG only (spot, no shorting)."""
        if i < 50:
            return None
        
        ind = self._calc_indicators(data, i)
        price = ind["close"]
        
        # HEAVY BUY: RSI extremely oversold + price below Bollinger lower band
        if ind["rsi"] < self.rsi_heavy_buy and price < ind["bb_lower"]:
            return {
                "action": "LONG",
                "signal": "HEAVY_DIP_BUY",
                "stop": price * 0.95,    # 5% stop (wider for DCA)
                "target": price * 1.08,  # 8% target
            }
        
        # NORMAL BUY: RSI oversold + 3+ red candles (dip accumulation)
        if ind["rsi"] < self.rsi_buy and ind["red_count"] >= 3:
            return {
                "action": "LONG",
                "signal": "DIP_BUY",
                "stop": price * 0.93,    # 7% stop
                "target": price * 1.06,  # 6% target
            }
        
        return None
    
    def check_exit(self, data, i, trade):
        """Exit when overbought or Bollinger upper touched."""
        if i < 50:
            return None
        
        ind = self._calc_indicators(data, i)
        
        # Sell signal: RSI overbought + price above upper Bollinger
        if ind["rsi"] > self.rsi_sell and ind["close"] > ind["bb_upper"]:
            return "OVERBOUGHT_EXIT"
        
        # Time-based: if held > 14 days and in profit, take it
        if trade.entry_time and (data.index[i] - trade.entry_time).days > 14:
            if ind["close"] > trade.entry_price:
                return "TIME_PROFIT_EXIT"
        
        return None
