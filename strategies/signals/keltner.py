"""
Keltner Channel Breakout.
Uses ATR-based channels (not std dev like Bollinger).
Keltner breakout = persistent trend, not just volatility expansion.

Rules:
- Entry: Close breaks above/below Keltner channel (EMA20 ± 2*ATR)
- This is DIFFERENT from Bollinger: Keltner uses ATR (true range) not std dev
- When price > upper Keltner = genuine momentum/trend
- Combine with RSI momentum + daily trend
"""

import numpy as np
import pandas as pd


class KeltnerBreakout:

    def __init__(self, ema_period=20, atr_mult=2.0, atr_period=14):
        self.ema_period = ema_period
        self.atr_mult = atr_mult
        self.atr_period = atr_period
        self._ind = None
        self._last_exit_bar = -10

    def _precompute(self, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)

        # EMA for Keltner center
        ema = closes.ewm(span=self.ema_period, adjust=False).mean()

        # ATR
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(self.atr_period).mean()

        # Keltner channels
        kc_upper = ema + self.atr_mult * atr
        kc_lower = ema - self.atr_mult * atr

        # Daily trend EMAs
        ema_d_fast = closes.ewm(span=192, adjust=False).mean()
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean()

        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        # Distance from channel as % of ATR (shows how far outside)
        dist_from_upper = (closes - kc_upper) / atr.replace(0, 1)
        dist_from_lower = (kc_lower - closes) / atr.replace(0, 1)

        # Price was inside channel previous bar (fresh breakout)
        prev_in_upper = closes.shift(1) < kc_upper.shift(1)
        prev_in_lower = closes.shift(1) > kc_lower.shift(1)

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "ema": ema, "atr": atr,
            "kc_upper": kc_upper, "kc_lower": kc_lower,
            "dist_from_upper": dist_from_upper,
            "dist_from_lower": dist_from_lower,
            "prev_in_upper": prev_in_upper,
            "prev_in_lower": prev_in_lower,
            "vol_ratio": vol_ratio, "rsi": rsi,
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

        # LONG: price breaks above upper Keltner channel (fresh breakout)
        if (price > ind["kc_upper"] and
            ind["prev_in_upper"] and  # Was inside channel before
            ind["vol_ratio"] > 1.3 and
            ind["rsi"] > 50 and ind["rsi"] < 80 and
            trend in ("UP", "FLAT")):

            score = 50
            if trend == "UP":
                score += 25
            if ind["vol_ratio"] > 2.0:
                score += 15
            elif ind["vol_ratio"] > 1.5:
                score += 8
            if ind["rsi"] > 60:
                score += 10

            lev = 1.5 + (score - 50) / 100 * 2.0
            lev = min(3.5, max(1.5, lev))

            return {
                "action": "LONG",
                "signal": f"KELT_L(s{score:.0f})",
                "stop": price - atr * 2.5,
                "target": price + atr * 8,
                "leverage": lev,
            }

        # SHORT: price breaks below lower Keltner channel
        if (price < ind["kc_lower"] and
            ind["prev_in_lower"] and
            ind["vol_ratio"] > 1.3 and
            ind["rsi"] < 50 and ind["rsi"] > 20 and
            trend in ("DOWN", "FLAT")):

            score = 50
            if trend == "DOWN":
                score += 25
            if ind["vol_ratio"] > 2.0:
                score += 15
            elif ind["vol_ratio"] > 1.5:
                score += 8
            if ind["rsi"] < 40:
                score += 10

            lev = 1.5 + (score - 50) / 100 * 2.0
            lev = min(3.5, max(1.5, lev))

            return {
                "action": "SHORT",
                "signal": f"KELT_S(s{score:.0f})",
                "stop": price + atr * 2.5,
                "target": price - atr * 8,
                "leverage": lev,
            }

        return None

    def check_exit(self, data, i, trade):
        if self._ind is None or i >= len(self._ind):
            return None

        ind = self._ind.iloc[i]
        price = float(ind["close"])

        # Exit when price returns inside the channel (mean revert)
        if trade.direction == "LONG" and price < ind["kc_upper"]:
            self._last_exit_bar = i
            return "KC_RETURN"

        if trade.direction == "SHORT" and price > ind["kc_lower"]:
            self._last_exit_bar = i
            return "KC_RETURN"

        # Trend flip
        trend = self._daily_trend(i)
        if trade.direction == "LONG" and trend == "DOWN":
            self._last_exit_bar = i
            return "TREND_FLIP"
        if trade.direction == "SHORT" and trend == "UP":
            self._last_exit_bar = i
            return "TREND_FLIP"

        return None
