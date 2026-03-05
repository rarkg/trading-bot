"""
ADX + Directional Index (DI) System.
Classic Wilder trend strength indicator.

Rules:
- ADX > 20: Market is trending (vs choppy)
- DI+ > DI-: Bullish direction
- DI- > DI+: Bearish direction
- Entry: DI crossover when ADX is rising and > threshold
- This identifies when a trend is STARTING to gain strength

Different from squeeze: squeeze detects volatility compression.
ADX detects directional momentum AFTER it starts.
"""

import numpy as np
import pandas as pd


class ADXSystem:

    def __init__(self, adx_period=14, adx_min=22):
        self.adx_period = adx_period
        self.adx_min = adx_min
        self._ind = None
        self._last_exit_bar = -10

    def _precompute(self, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)
        n = self.adx_period

        # Directional Movement
        high_diff = highs - highs.shift(1)
        low_diff = lows.shift(1) - lows

        dm_plus = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0.0)
        dm_minus = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0.0)

        # True Range
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)

        # Wilder smoothing (EWM with alpha = 1/n)
        atr_w = tr.ewm(alpha=1/n, adjust=False).mean()
        dm_plus_w = dm_plus.ewm(alpha=1/n, adjust=False).mean()
        dm_minus_w = dm_minus.ewm(alpha=1/n, adjust=False).mean()

        di_plus = 100 * dm_plus_w / atr_w.replace(0, 1e-10)
        di_minus = 100 * dm_minus_w / atr_w.replace(0, 1e-10)

        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, 1e-10)
        adx = dx.ewm(alpha=1/n, adjust=False).mean()

        # ADX slope (is ADX rising?)
        adx_slope = adx - adx.shift(3)

        # DI crossover detection
        di_cross_up = (di_plus > di_minus) & (di_plus.shift(1) <= di_minus.shift(1))
        di_cross_down = (di_minus > di_plus) & (di_minus.shift(1) <= di_plus.shift(1))

        # ATR for stops
        atr14 = tr.rolling(14).mean()

        # Daily trend EMAs
        ema_d_fast = closes.ewm(span=192, adjust=False).mean()
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean()

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "adx": adx, "di_plus": di_plus, "di_minus": di_minus,
            "adx_slope": adx_slope,
            "di_cross_up": di_cross_up, "di_cross_down": di_cross_down,
            "atr": atr14, "vol_ratio": vol_ratio,
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

        if i - self._last_exit_bar < 8:
            return None

        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])

        if pd.isna(atr) or atr <= 0 or pd.isna(ind["adx"]):
            return None

        trend = self._daily_trend(i)
        adx = float(ind["adx"])
        adx_slope = float(ind["adx_slope"])

        # Need ADX above threshold and rising (trend strengthening)
        if adx < self.adx_min or adx_slope < 0:
            return None

        # LONG: DI+ crosses above DI- with trend confirmation
        if (ind["di_cross_up"] and
            ind["di_plus"] > ind["di_minus"] and
            trend in ("UP", "FLAT")):

            score = 50
            if trend == "UP":
                score += 20
            if adx > 30:
                score += 15
            elif adx > 25:
                score += 8
            if adx_slope > 2:
                score += 10
            if ind["vol_ratio"] > 1.5:
                score += 5

            lev = 1.5 + (score - 50) / 100 * 2.0
            lev = min(3.5, max(1.5, lev))

            return {
                "action": "LONG",
                "signal": f"ADX_L(adx{adx:.0f},s{score:.0f})",
                "stop": price - atr * 2.5,
                "target": price + atr * 8,
                "leverage": lev,
            }

        # SHORT: DI- crosses above DI+
        if (ind["di_cross_down"] and
            ind["di_minus"] > ind["di_plus"] and
            trend in ("DOWN", "FLAT")):

            score = 50
            if trend == "DOWN":
                score += 20
            if adx > 30:
                score += 15
            elif adx > 25:
                score += 8
            if adx_slope > 2:
                score += 10
            if ind["vol_ratio"] > 1.5:
                score += 5

            lev = 1.5 + (score - 50) / 100 * 2.0
            lev = min(3.5, max(1.5, lev))

            return {
                "action": "SHORT",
                "signal": f"ADX_S(adx{adx:.0f},s{score:.0f})",
                "stop": price + atr * 2.5,
                "target": price - atr * 8,
                "leverage": lev,
            }

        return None

    def check_exit(self, data, i, trade):
        if self._ind is None or i >= len(self._ind):
            return None

        ind = self._ind.iloc[i]
        atr = float(ind["atr"]) if not pd.isna(ind["atr"]) else 0

        # Exit when DI crosses back
        if trade.direction == "LONG" and ind["di_minus"] > ind["di_plus"]:
            self._last_exit_bar = i
            return "DI_CROSS_EXIT"

        if trade.direction == "SHORT" and ind["di_plus"] > ind["di_minus"]:
            self._last_exit_bar = i
            return "DI_CROSS_EXIT"

        # Trend flip
        trend = self._daily_trend(i)
        if trade.direction == "LONG" and trend == "DOWN":
            self._last_exit_bar = i
            return "TREND_FLIP"
        if trade.direction == "SHORT" and trend == "UP":
            self._last_exit_bar = i
            return "TREND_FLIP"

        return None
