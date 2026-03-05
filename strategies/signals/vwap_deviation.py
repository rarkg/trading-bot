"""
VWAP Deviation + Trend Following.
VWAP = institutional price anchor. Deviations signal overextension or momentum.

Strategy:
- Calculate rolling VWAP (24-hour window, re-anchored daily)
- When price is significantly ABOVE VWAP AND trend is up = momentum continuation
- When price is significantly BELOW VWAP AND trend is down = momentum continuation
- NOT mean reversion (mean reversion loses in crypto per prior testing)
- AVOID: trading when price is between VWAP bands (choppy zone)

Key insight: VWAP breakout with trend = institutions chasing price
"""

import numpy as np
import pandas as pd


class VWAPMomentum:

    def __init__(self, vwap_period=24, dev_mult=1.5):
        self.vwap_period = vwap_period  # Hours for VWAP window
        self.dev_mult = dev_mult  # Deviation multiplier
        self._ind = None
        self._last_exit_bar = -10

    def _precompute(self, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)

        # Typical price
        typical_price = (highs + lows + closes) / 3

        # Rolling VWAP (not anchored to day start, but rolling window)
        tp_vol = typical_price * volumes
        rolling_tp_vol = tp_vol.rolling(self.vwap_period).sum()
        rolling_vol = volumes.rolling(self.vwap_period).sum()
        vwap = rolling_tp_vol / rolling_vol.replace(0, 1)

        # VWAP standard deviation bands
        vwap_sq_diff = ((typical_price - vwap) ** 2 * volumes).rolling(self.vwap_period).sum()
        vwap_std = (vwap_sq_diff / rolling_vol.replace(0, 1)).pow(0.5)

        vwap_upper = vwap + self.dev_mult * vwap_std
        vwap_lower = vwap - self.dev_mult * vwap_std

        # Distance from VWAP in standard deviations
        vwap_dist = (closes - vwap) / vwap_std.replace(0, 1)

        # Daily trend EMAs
        ema_d_fast = closes.ewm(span=192, adjust=False).mean()
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean()

        # Short-term EMA for momentum direction
        ema8 = closes.ewm(span=8, adjust=False).mean()
        ema21 = closes.ewm(span=21, adjust=False).mean()

        # ATR
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # Prev bar VWAP relationship
        prev_above = closes.shift(1) > vwap.shift(1)
        prev_below = closes.shift(1) < vwap.shift(1)

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "vwap": vwap, "vwap_upper": vwap_upper, "vwap_lower": vwap_lower,
            "vwap_dist": vwap_dist,
            "prev_above": prev_above, "prev_below": prev_below,
            "ema8": ema8, "ema21": ema21,
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

        if pd.isna(atr) or atr <= 0 or pd.isna(ind["vwap"]):
            return None

        trend = self._daily_trend(i)
        vwap_dist = float(ind["vwap_dist"])

        # LONG: Price breaks above VWAP upper band (momentum above VWAP)
        # Fresh break: was below upper, now above
        if (price > ind["vwap_upper"] and
            not ind["prev_above"] and  # Fresh breakout
            ind["ema8"] > ind["ema21"] and  # Short-term momentum up
            ind["vol_ratio"] > 1.3 and
            ind["rsi"] > 50 and ind["rsi"] < 80 and
            trend in ("UP", "FLAT")):

            score = 50
            if trend == "UP":
                score += 25
            if vwap_dist > 2.5:
                score += 10
            if ind["vol_ratio"] > 2.0:
                score += 10
            if ind["rsi"] > 60:
                score += 5

            lev = 1.5 + (score - 50) / 100 * 2.0
            lev = min(3.5, max(1.5, lev))

            return {
                "action": "LONG",
                "signal": f"VWAP_L(d{vwap_dist:.1f},s{score:.0f})",
                "stop": float(ind["vwap"]) - atr * 0.5,  # VWAP is support
                "target": price + atr * 7,
                "leverage": lev,
            }

        # SHORT: Price breaks below VWAP lower band
        if (price < ind["vwap_lower"] and
            not ind["prev_below"] and
            ind["ema8"] < ind["ema21"] and
            ind["vol_ratio"] > 1.3 and
            ind["rsi"] < 50 and ind["rsi"] > 20 and
            trend in ("DOWN", "FLAT")):

            score = 50
            if trend == "DOWN":
                score += 25
            if vwap_dist < -2.5:
                score += 10
            if ind["vol_ratio"] > 2.0:
                score += 10
            if ind["rsi"] < 40:
                score += 5

            lev = 1.5 + (score - 50) / 100 * 2.0
            lev = min(3.5, max(1.5, lev))

            return {
                "action": "SHORT",
                "signal": f"VWAP_S(d{vwap_dist:.1f},s{score:.0f})",
                "stop": float(ind["vwap"]) + atr * 0.5,
                "target": price - atr * 7,
                "leverage": lev,
            }

        return None

    def check_exit(self, data, i, trade):
        if self._ind is None or i >= len(self._ind):
            return None

        ind = self._ind.iloc[i]
        price = float(ind["close"])

        if pd.isna(ind["vwap"]):
            return None

        # Exit when price returns to VWAP (momentum exhausted)
        if trade.direction == "LONG" and price < ind["vwap"]:
            self._last_exit_bar = i
            return "VWAP_RETURN"

        if trade.direction == "SHORT" and price > ind["vwap"]:
            self._last_exit_bar = i
            return "VWAP_RETURN"

        # Trend flip
        trend = self._daily_trend(i)
        if trade.direction == "LONG" and trend == "DOWN":
            self._last_exit_bar = i
            return "TREND_FLIP"
        if trade.direction == "SHORT" and trend == "UP":
            self._last_exit_bar = i
            return "TREND_FLIP"

        return None
