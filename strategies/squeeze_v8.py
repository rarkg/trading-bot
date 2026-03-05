"""
V8 — Refined Squeeze + Multi-Asset Support + Adaptive Risk Management

Key improvements over V7:
1. Squeeze percentile threshold (bottom 20th percentile, not fixed 65%)
2. Volume ACCELERATION (3-bar increasing volume) not just volume spike
3. 2-candle confirmation: require momentum over 2 bars, not just current
4. Adaptive stops: tighter in high-vol regimes, wider in low-vol
5. Time-based exit: if no progress in 8 bars, cut losses
6. Bear market mode: reduce leverage in severe bear trends
7. Works as a component in multi-asset portfolio (keeps state per instance)
"""

import numpy as np
import pandas as pd


class SqueezeV8:

    def __init__(self, fixed_leverage=None, min_score=55):
        """
        Args:
            fixed_leverage: If set, override dynamic leverage with this value
            min_score: Minimum confidence score to enter (default 55)
        """
        self.fixed_leverage = fixed_leverage
        self.min_score = min_score
        self._ind = None
        self._trailing_stop = None
        self._best_price = None
        self._last_exit_bar = -12
        self._entry_bar = -1

    def _precompute(self, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)
        opens = data["open"].astype(float)

        # EMAs
        ema8 = closes.ewm(span=8, adjust=False).mean()
        ema21 = closes.ewm(span=21, adjust=False).mean()
        ema55 = closes.ewm(span=55, adjust=False).mean()

        # Daily-equivalent EMAs for trend
        ema_d_fast = closes.ewm(span=192, adjust=False).mean()   # 8d
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()   # 21d
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean() # 55d
        ema_200d = closes.ewm(span=4800, adjust=False).mean()    # 200d (bear market filter)

        # ATR
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_pct = atr / closes * 100  # ATR as % of price (volatility regime)

        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)
        # Volume acceleration: current vs 3-bar average
        vol_accel = volumes / volumes.rolling(3).mean().shift(1).replace(0, 1)

        # Bollinger Bands
        sma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        bb_width = (std20 / sma20.replace(0, 1)) * 100

        # PERCENTILE-BASED squeeze detection (adaptive to market conditions)
        bb_width_pct20 = bb_width.rolling(500).quantile(0.20)  # 20th percentile
        bb_width_pct50 = bb_width.rolling(500).quantile(0.50)  # median
        is_squeeze = bb_width < bb_width_pct20
        is_mild_squeeze = bb_width < bb_width_pct50

        # Candle properties
        body = closes - opens
        body_ratio = body.abs() / (highs - lows).replace(0, 1)
        bullish = closes > opens

        # 2-bar momentum confirmation
        bullish_int = bullish.astype(int)
        two_bar_bull = (bullish_int == 1) & (bullish_int.shift(1) == 1)
        two_bar_bear = (bullish_int == 0) & (bullish_int.shift(1) == 0)

        # Price momentum
        ema21_slope = (ema21 - ema21.shift(5)) / ema21.shift(5).replace(0, 1) * 100
        d_slope = (ema_d_fast - ema_d_fast.shift(24)) / ema_d_fast.shift(24).replace(0, 1) * 100

        # Bear market detection: price below 200d EMA
        bear_market = closes < ema_200d

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows, "open": opens,
            "ema8": ema8, "ema21": ema21, "ema55": ema55,
            "ema_d_fast": ema_d_fast, "ema_d_slow": ema_d_slow,
            "ema_d_trend": ema_d_trend, "ema_200d": ema_200d,
            "atr": atr, "atr_pct": atr_pct, "rsi": rsi,
            "vol_ratio": vol_ratio, "vol_accel": vol_accel,
            "bb_width": bb_width, "bb_width_pct20": bb_width_pct20,
            "is_squeeze": is_squeeze, "is_mild_squeeze": is_mild_squeeze,
            "body_ratio": body_ratio, "bullish": bullish,
            "two_bar_bull": two_bar_bull, "two_bar_bear": two_bar_bear,
            "ema21_slope": ema21_slope, "d_slope": d_slope,
            "bear_market": bear_market,
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

        # Daily trend alignment (0-30 points)
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

        # Bear market penalty: reduce long trades in bear market
        if direction == "LONG" and ind["bear_market"]:
            score -= 15  # Can still trade longs but need higher score

        # Hourly EMA alignment (0-20)
        if direction == "LONG" and ind["ema8"] > ind["ema21"] > ind["ema55"]:
            score += 20
        elif direction == "LONG" and ind["ema8"] > ind["ema21"]:
            score += 10
        elif direction == "SHORT" and ind["ema8"] < ind["ema21"] < ind["ema55"]:
            score += 20
        elif direction == "SHORT" and ind["ema8"] < ind["ema21"]:
            score += 10

        # Volume acceleration (0-20)
        if ind["vol_accel"] > 2.0 and ind["vol_ratio"] > 1.8:
            score += 20
        elif ind["vol_accel"] > 1.5 and ind["vol_ratio"] > 1.3:
            score += 12
        elif ind["vol_ratio"] > 1.3:
            score += 6

        # 2-bar confirmation (0-15)
        if direction == "LONG" and ind["two_bar_bull"]:
            score += 15
        elif direction == "SHORT" and ind["two_bar_bear"]:
            score += 15
        elif ind["body_ratio"] > 0.6:
            score += 8

        # Daily slope momentum (0-15)
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

        if i < 1400 or i >= len(self._ind):
            return None

        if i - self._last_exit_bar < 8:
            return None

        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])

        if pd.isna(atr) or atr <= 0 or pd.isna(ind["bb_width_pct20"]):
            return None

        # Require TIGHT squeeze (percentile-based, adaptive)
        if not ind["is_squeeze"]:
            return None

        # LONG: break above prev high + volume + bullish candle
        if (price > ind["prev_high"] and
            ind["bullish"] and
            ind["body_ratio"] > 0.35 and
            ind["vol_ratio"] > 1.2 and
            ind["rsi"] < 78):

            score = self._confidence(i, "LONG")
            if score < self.min_score:
                return None

            # Adaptive stop: tighter in high-vol regimes
            atr_pct = float(ind["atr_pct"])
            if atr_pct > 5:  # Very high volatility
                stop_mult = 1.5  # Tighter stop
            elif atr_pct > 3:
                stop_mult = 2.0
            else:
                stop_mult = 2.5  # Normal volatility

            # Leverage based on score
            if self.fixed_leverage:
                lev = self.fixed_leverage
            else:
                if score >= 85:
                    lev = 4.0
                elif score >= 75:
                    lev = 3.0
                elif score >= 65:
                    lev = 2.5
                else:
                    lev = 2.0

            self._trailing_stop = price - (atr * stop_mult)
            self._best_price = price
            self._entry_bar = i

            return {
                "action": "LONG",
                "signal": f"SQZ8_L(s{score})",
                "stop": price - (atr * stop_mult),
                "target": price + (atr * 10),
                "leverage": lev,
            }

        # SHORT: break below prev low
        if (price < ind["prev_low"] and
            not ind["bullish"] and
            ind["body_ratio"] > 0.35 and
            ind["vol_ratio"] > 1.2 and
            ind["rsi"] > 22):

            score = self._confidence(i, "SHORT")
            if score < self.min_score:
                return None

            atr_pct = float(ind["atr_pct"])
            if atr_pct > 5:
                stop_mult = 1.5
            elif atr_pct > 3:
                stop_mult = 2.0
            else:
                stop_mult = 2.5

            if self.fixed_leverage:
                lev = self.fixed_leverage
            else:
                if score >= 85:
                    lev = 4.0
                elif score >= 75:
                    lev = 3.0
                elif score >= 65:
                    lev = 2.5
                else:
                    lev = 2.0

            self._trailing_stop = price + (atr * stop_mult)
            self._best_price = price
            self._entry_bar = i

            return {
                "action": "SHORT",
                "signal": f"SQZ8_S(s{score})",
                "stop": price + (atr * stop_mult),
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

        # Time-based exit: if no progress in 12 bars, cut
        bars_in_trade = i - self._entry_bar
        if bars_in_trade > 0 and bars_in_trade == 12:
            if trade.direction == "LONG":
                progress = (price - trade.entry_price) / atr
                if progress < 0.5:  # Less than 0.5 ATR progress
                    self._last_exit_bar = i
                    return "TIME_EXIT"
            elif trade.direction == "SHORT":
                progress = (trade.entry_price - price) / atr
                if progress < 0.5:
                    self._last_exit_bar = i
                    return "TIME_EXIT"

        # Trailing stop with tightening as profit grows
        if trade.direction == "LONG":
            if price > (self._best_price or price):
                self._best_price = price
                pnl_r = (price - trade.entry_price) / atr
                # Tighten trail as profit grows
                if pnl_r > 5:
                    trail = 1.0
                elif pnl_r > 3:
                    trail = 1.5
                elif pnl_r > 1:
                    trail = 2.0
                else:
                    trail = 2.5
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
                if pnl_r > 5:
                    trail = 1.0
                elif pnl_r > 3:
                    trail = 1.5
                elif pnl_r > 1:
                    trail = 2.0
                else:
                    trail = 2.5
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
