"""
OBV Momentum + Divergence Strategy.
On-Balance Volume (OBV) accumulation/distribution as leading indicator.

Strategy:
1. OBV trend alignment: OBV making higher highs while price also up = strong confirmation
2. OBV divergence: OBV makes new high before price does = leading bullish signal
3. OBV breakout: OBV breaks above its own moving average = institutional buying

This gives volume-based momentum signals uncorrelated with price oscillators.
"""

import numpy as np
import pandas as pd


class OBVMomentum:

    def __init__(self, obv_ema_period=21):
        self.obv_ema_period = obv_ema_period
        self._ind = None
        self._last_exit_bar = -10

    def _precompute(self, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)

        # On-Balance Volume
        price_change = closes.diff()
        obv_direction = pd.Series(0.0, index=closes.index)
        obv_direction[price_change > 0] = 1.0
        obv_direction[price_change < 0] = -1.0
        obv = (volumes * obv_direction).cumsum()

        # OBV EMA for trend
        obv_ema = obv.ewm(span=self.obv_ema_period, adjust=False).mean()

        # OBV slope (short-term)
        obv_slope_fast = obv.diff(5) / (obv.abs().rolling(5).mean().replace(0, 1) + 1)
        obv_slope_slow = obv.diff(20) / (obv.abs().rolling(20).mean().replace(0, 1) + 1)

        # OBV crossover above its own EMA (buying pressure)
        obv_above_ema = obv > obv_ema
        obv_cross_up = obv_above_ema & ~obv_above_ema.shift(1).fillna(False)
        obv_cross_down = ~obv_above_ema & obv_above_ema.shift(1).fillna(True)

        # Price momentum
        price_slope_fast = closes.diff(5) / closes.shift(5).replace(0, 1) * 100
        price_slope_slow = closes.diff(20) / closes.shift(20).replace(0, 1) * 100

        # OBV divergence: OBV sloping up but price sloping down (or vice versa)
        obv_bull_diverge = (obv_slope_fast > 0) & (price_slope_fast < 0)
        obv_bear_diverge = (obv_slope_fast < 0) & (price_slope_fast > 0)

        # Daily trend EMAs
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

        # Volume ratio
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "obv": obv, "obv_ema": obv_ema,
            "obv_slope_fast": obv_slope_fast,
            "obv_slope_slow": obv_slope_slow,
            "obv_cross_up": obv_cross_up,
            "obv_cross_down": obv_cross_down,
            "obv_bull_diverge": obv_bull_diverge,
            "obv_bear_diverge": obv_bear_diverge,
            "price_slope_fast": price_slope_fast,
            "atr": atr, "vol_ratio": vol_ratio, "rsi": rsi,
            "ema_d_fast": ema_d_fast,
            "ema_d_slow": ema_d_slow,
            "ema_d_trend": ema_d_trend,
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

        if i - self._last_exit_bar < 6:
            return None

        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])

        if pd.isna(atr) or atr <= 0:
            return None

        trend = self._daily_trend(i)

        # LONG: OBV crosses above its EMA (or bullish divergence)
        # Must have price momentum support and trend alignment
        if (trend in ("UP", "FLAT") and
            ind["rsi"] > 45 and ind["rsi"] < 75):

            # Signal 1: OBV fresh crossover above EMA + positive price slope
            if (ind["obv_cross_up"] and
                ind["price_slope_fast"] > 0 and
                ind["vol_ratio"] > 1.2):

                score = 55
                if trend == "UP":
                    score += 20
                if ind["obv_slope_slow"] > 0:
                    score += 10
                if ind["vol_ratio"] > 1.8:
                    score += 10

                lev = 1.5 + (score - 50) / 100 * 2.0
                lev = min(3.0, max(1.5, lev))

                return {
                    "action": "LONG",
                    "signal": f"OBV_L(s{score:.0f})",
                    "stop": price - atr * 2.5,
                    "target": price + atr * 7,
                    "leverage": lev,
                }

            # Signal 2: Bullish divergence + trend up
            if (ind["obv_bull_diverge"] and
                trend == "UP" and
                ind["vol_ratio"] > 1.5):

                return {
                    "action": "LONG",
                    "signal": "OBV_DIVBULL",
                    "stop": price - atr * 2.5,
                    "target": price + atr * 7,
                    "leverage": 2.0,
                }

        # SHORT: OBV crosses below EMA
        if (trend in ("DOWN", "FLAT") and
            ind["rsi"] < 55 and ind["rsi"] > 25):

            if (ind["obv_cross_down"] and
                ind["price_slope_fast"] < 0 and
                ind["vol_ratio"] > 1.2):

                score = 55
                if trend == "DOWN":
                    score += 20
                if ind["obv_slope_slow"] < 0:
                    score += 10
                if ind["vol_ratio"] > 1.8:
                    score += 10

                lev = 1.5 + (score - 50) / 100 * 2.0
                lev = min(3.0, max(1.5, lev))

                return {
                    "action": "SHORT",
                    "signal": f"OBV_S(s{score:.0f})",
                    "stop": price + atr * 2.5,
                    "target": price - atr * 7,
                    "leverage": lev,
                }

            if (ind["obv_bear_diverge"] and
                trend == "DOWN" and
                ind["vol_ratio"] > 1.5):

                return {
                    "action": "SHORT",
                    "signal": "OBV_DIVBEAR",
                    "stop": price + atr * 2.5,
                    "target": price - atr * 7,
                    "leverage": 2.0,
                }

        return None

    def check_exit(self, data, i, trade):
        if self._ind is None or i >= len(self._ind):
            return None

        ind = self._ind.iloc[i]

        # Exit when OBV trend reverses
        if trade.direction == "LONG" and ind["obv_cross_down"]:
            self._last_exit_bar = i
            return "OBV_REVERSE"

        if trade.direction == "SHORT" and ind["obv_cross_up"]:
            self._last_exit_bar = i
            return "OBV_REVERSE"

        # Trend flip
        trend = self._daily_trend(i)
        if trade.direction == "LONG" and trend == "DOWN":
            self._last_exit_bar = i
            return "TREND_FLIP"
        if trade.direction == "SHORT" and trend == "UP":
            self._last_exit_bar = i
            return "TREND_FLIP"

        return None
