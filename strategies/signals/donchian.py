"""
Donchian Channel Breakout — Turtle Trading style.
Buy when price breaks above N-bar high, sell short when breaks below N-bar low.
Classic trend-following that works in momentum regimes.

Rules:
- Entry: Close breaks above 20-bar high (LONG) or below 20-bar low (SHORT)
- Exit: 10-bar channel stop (half-period trailing)
- Daily trend filter: only trade in trend direction
- Volume confirmation: vol > 1.3x avg
"""

import numpy as np
import pandas as pd


class DonchianBreakout:

    def __init__(self, entry_period=20, exit_period=10, atr_mult_stop=2.0):
        self.entry_period = entry_period
        self.exit_period = exit_period
        self.atr_mult_stop = atr_mult_stop
        self._ind = None
        self._last_exit_bar = -10

    def _precompute(self, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)

        # Donchian channels
        dc_high = highs.rolling(self.entry_period).max().shift(1)
        dc_low = lows.rolling(self.entry_period).min().shift(1)
        dc_exit_high = highs.rolling(self.exit_period).max().shift(1)
        dc_exit_low = lows.rolling(self.exit_period).min().shift(1)

        # ATR for stops
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # Daily-equivalent trend EMAs
        ema_d_fast = closes.ewm(span=192, adjust=False).mean()
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean()

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        # RSI to avoid overbought/oversold entries
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "dc_high": dc_high, "dc_low": dc_low,
            "dc_exit_high": dc_exit_high, "dc_exit_low": dc_exit_low,
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

        if pd.isna(atr) or atr <= 0 or pd.isna(ind["dc_high"]):
            return None

        trend = self._daily_trend(i)

        # LONG: break above Donchian high with volume + trend
        if (price > ind["dc_high"] and
            ind["vol_ratio"] > 1.3 and
            ind["rsi"] < 75 and
            trend in ("UP", "FLAT")):

            # Confidence-based leverage
            score = 50
            if trend == "UP":
                score += 20
            if ind["vol_ratio"] > 2.0:
                score += 15
            elif ind["vol_ratio"] > 1.5:
                score += 8
            if ind["rsi"] < 60:
                score += 15

            lev = 1.5 + (score - 50) / 100 * 2.0  # 1.5x to 3.5x
            lev = min(3.5, max(1.5, lev))

            return {
                "action": "LONG",
                "signal": f"DONCH_L(s{score:.0f})",
                "stop": price - atr * self.atr_mult_stop,
                "target": price + atr * 8,
                "leverage": lev,
            }

        # SHORT: break below Donchian low with volume + trend
        if (price < ind["dc_low"] and
            ind["vol_ratio"] > 1.3 and
            ind["rsi"] > 25 and
            trend in ("DOWN", "FLAT")):

            score = 50
            if trend == "DOWN":
                score += 20
            if ind["vol_ratio"] > 2.0:
                score += 15
            elif ind["vol_ratio"] > 1.5:
                score += 8
            if ind["rsi"] > 40:
                score += 15

            lev = 1.5 + (score - 50) / 100 * 2.0
            lev = min(3.5, max(1.5, lev))

            return {
                "action": "SHORT",
                "signal": f"DONCH_S(s{score:.0f})",
                "stop": price + atr * self.atr_mult_stop,
                "target": price - atr * 8,
                "leverage": lev,
            }

        return None

    def check_exit(self, data, i, trade):
        if self._ind is None or i >= len(self._ind):
            return None

        ind = self._ind.iloc[i]
        price = float(ind["close"])

        if pd.isna(ind["dc_exit_low"]) or pd.isna(ind["dc_exit_high"]):
            return None

        # Exit on Donchian exit channel (half-period)
        if trade.direction == "LONG" and price < ind["dc_exit_low"]:
            self._last_exit_bar = i
            return "DC_EXIT"

        if trade.direction == "SHORT" and price > ind["dc_exit_high"]:
            self._last_exit_bar = i
            return "DC_EXIT"

        # Trend flip exit
        trend = self._daily_trend(i)
        if trade.direction == "LONG" and trend == "DOWN":
            self._last_exit_bar = i
            return "TREND_FLIP"
        if trade.direction == "SHORT" and trend == "UP":
            self._last_exit_bar = i
            return "TREND_FLIP"

        return None
