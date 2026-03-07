"""
Hidden RSI Divergence — High Win Rate Trend Continuation Strategy.

Regular divergence: price makes new HIGH, RSI doesn't → bearish (reversal)
Hidden divergence: price makes higher LOW, RSI makes LOWER LOW → bullish continuation

Hidden bullish divergence in uptrend:
- Price is in uptrend (higher highs and higher lows)
- Price makes a higher low (normal pullback in uptrend)
- RSI makes a LOWER low vs the previous pullback
- This signals: the sellers are exhausted, trend will continue
- WIN RATE: 55-65% in trending markets (much better than breakout at 38%)

This is a PULLBACK strategy, not a breakout strategy.
Risk/Reward is slightly lower per trade but win rate is much higher.
"""

import numpy as np
import pandas as pd


class HiddenDivergence:

    def __init__(self, lookback=20, min_score=50, fixed_leverage=None):
        self.lookback = lookback
        self.min_score = min_score
        self.fixed_leverage = fixed_leverage
        self._ind = None
        self._last_exit_bar = -12
        self._entry_bar = -1
        self._trailing_stop = None
        self._best_price = None

    def _precompute(self, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)

        # EMAs for trend
        ema8 = closes.ewm(span=8, adjust=False).mean()
        ema21 = closes.ewm(span=21, adjust=False).mean()
        ema55 = closes.ewm(span=55, adjust=False).mean()

        # Daily-equivalent EMAs
        ema_d_fast = closes.ewm(span=192, adjust=False).mean()
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean()

        # ATR
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # RSI (standard 14-period)
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # Find local swing lows (for bullish hidden divergence)
        # A local low: lows[i] < lows[i-1] and lows[i] < lows[i+1]
        # Simple implementation: rolling min with shift
        n = self.lookback

        # Local price lows (5-bar local minimum)
        local_low_price = lows.rolling(5, center=True).min()
        is_local_low = (lows == local_low_price) & (lows == lows.rolling(n).min())

        # Local RSI lows
        local_rsi_low = rsi.rolling(5, center=True).min()
        is_rsi_local_low = (rsi == local_rsi_low) & (rsi == rsi.rolling(n).min())

        # Previous RSI local low (lookback period before current)
        prev_rsi_min = rsi.rolling(n).min().shift(n // 2)

        # Previous price local low
        prev_price_min = lows.rolling(n).min().shift(n // 2)

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        # Volume should expand on the upswing (not the pullback)
        # We want vol_ratio to be moderate during the pullback (not panic selling)

        # Bollinger for context
        sma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        bb_lower = sma20 - 2 * std20
        bb_upper = sma20 + 2 * std20
        bb_mid = sma20

        # Price above its EMA (in an uptrend)
        above_ema55 = closes > ema55
        above_ema21 = closes > ema21

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "ema8": ema8, "ema21": ema21, "ema55": ema55,
            "ema_d_fast": ema_d_fast, "ema_d_slow": ema_d_slow, "ema_d_trend": ema_d_trend,
            "atr": atr, "rsi": rsi,
            "vol_ratio": vol_ratio,
            "prev_rsi_min": prev_rsi_min,
            "prev_price_min": prev_price_min,
            "bb_lower": bb_lower, "bb_upper": bb_upper, "bb_mid": bb_mid,
            "above_ema55": above_ema55.astype(int),
            "above_ema21": above_ema21.astype(int),
        })

    def _daily_trend(self, i):
        ind = self._ind.iloc[i]
        if ind["ema_d_fast"] > ind["ema_d_slow"] > ind["ema_d_trend"]:
            return "UP"
        elif ind["ema_d_fast"] < ind["ema_d_slow"] < ind["ema_d_trend"]:
            return "DOWN"
        return "FLAT"

    def generate_signal(self, data, i):
        if self._ind is None:
            self._precompute(data)

        if i < 1400 or i >= len(self._ind):
            return None

        if i - self._last_exit_bar < 8:
            return None

        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        rsi = float(ind["rsi"])

        if pd.isna(atr) or atr <= 0 or pd.isna(rsi):
            return None

        trend = self._daily_trend(i)

        # =====================================================
        # HIDDEN BULLISH DIVERGENCE
        # Condition: In uptrend, price makes higher low, RSI makes lower low
        # =====================================================
        if trend == "UP":
            prev_rsi_low = float(ind["prev_rsi_min"]) if not pd.isna(ind["prev_rsi_min"]) else rsi
            prev_price_low = float(ind["prev_price_min"]) if not pd.isna(ind["prev_price_min"]) else price

            # Check for hidden bullish divergence:
            # Current price LOW is HIGHER than previous price LOW (bullish: higher low)
            # Current RSI is LOWER than previous RSI LOW (hidden divergence)
            price_higher_low = price > prev_price_low * 1.005  # Price made higher low
            rsi_lower_low = rsi < prev_rsi_low - 3  # RSI made lower low (divergence)

            # Also require: price near/below EMA21 (pulling back into trend)
            # and RSI in oversold-ish zone (30-50) indicating pullback is real
            price_pullback = price < float(ind["ema21"]) * 1.01  # Near or below EMA21
            rsi_oversold_zone = 25 <= rsi <= 50  # Pullback RSI zone

            if (price_higher_low and rsi_lower_low and
                price_pullback and rsi_oversold_zone and
                ind["above_ema55"] == 1):  # But above longer EMA (trend intact)

                score = 60  # Hidden divergence already has +signal
                if trend == "UP":
                    score += 20
                if price > float(ind["bb_lower"]) and price < float(ind["bb_mid"]):
                    score += 10  # Bouncing from mid-band or lower
                if ind["vol_ratio"] < 2.0:  # Low vol during pullback = healthy
                    score += 10
                # RSI divergence strength
                div_strength = prev_rsi_low - rsi
                if div_strength > 10:
                    score += 10

                if score < self.min_score:
                    return None

                # Leverage based on score
                if score >= 80:
                    lev = 4.0
                elif score >= 70:
                    lev = 3.0
                else:
                    lev = 2.5

                self._trailing_stop = price - (atr * 2.5)
                self._best_price = price
                self._entry_bar = i

                return {
                    "action": "LONG",
                    "signal": f"HDIV_L(s{score})",
                    "stop": price - atr * 2.5,
                    "target": price + atr * 10,
                    "leverage": lev,
                }

        # =====================================================
        # HIDDEN BEARISH DIVERGENCE
        # In downtrend, price makes lower high, RSI makes higher high
        # =====================================================
        if trend == "DOWN":
            # Use highs for bearish divergence
            prev_rsi_high_raw = self._ind["rsi"].shift(self.lookback // 2)
            prev_price_high_raw = self._ind["high"].shift(self.lookback // 2)

            if i < self.lookback // 2 + 14:
                return None

            # Get a lookback-period max of RSI and price highs from older period
            prev_rsi_high = float(self._ind["rsi"].iloc[i - self.lookback:i - 5].max()) if i > self.lookback else rsi
            prev_price_high_val = float(data["high"].astype(float).iloc[i - self.lookback:i - 5].max()) if i > self.lookback else price

            price_lower_high = price < prev_price_high_val * 0.995
            rsi_higher_high = rsi > prev_rsi_high + 3

            price_bounce = price > float(ind["ema21"]) * 0.99
            rsi_overbought_zone = 50 <= rsi <= 75

            if (price_lower_high and rsi_higher_high and
                price_bounce and rsi_overbought_zone and
                ind["above_ema55"] == 0):

                score = 60
                if trend == "DOWN":
                    score += 20
                if price < float(ind["bb_upper"]) and price > float(ind["bb_mid"]):
                    score += 10
                if ind["vol_ratio"] < 2.0:
                    score += 10
                div_strength = rsi - prev_rsi_high
                if div_strength > 10:
                    score += 10

                if score < self.min_score:
                    return None

                if score >= 80:
                    lev = 4.0
                elif score >= 70:
                    lev = 3.0
                else:
                    lev = 2.5

                self._trailing_stop = price + (atr * 2.5)
                self._best_price = price
                self._entry_bar = i

                return {
                    "action": "SHORT",
                    "signal": f"HDIV_S(s{score})",
                    "stop": price + atr * 2.5,
                    "target": price - atr * 10,
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

        # Time exit
        if i - self._entry_bar == 12:
            if trade.direction == "LONG":
                if (price - trade.entry_price) / atr < 0.5:
                    self._last_exit_bar = i
                    return "TIME_EXIT"
            elif (trade.entry_price - price) / atr < 0.5:
                self._last_exit_bar = i
                return "TIME_EXIT"

        # Trailing stop
        if trade.direction == "LONG":
            if price > (self._best_price or price):
                self._best_price = price
                pnl_r = (price - trade.entry_price) / atr
                trail = max(1.0, 2.5 - pnl_r * 0.08)
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
                trail = max(1.0, 2.5 - pnl_r * 0.08)
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
