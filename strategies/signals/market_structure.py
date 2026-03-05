"""
Market Structure Break (MSB).
Identifies swing highs and lows, then trades the break of these levels.

Logic:
- A swing high = local high with lower highs on both sides (lookback N bars)
- A swing low = local low with higher lows on both sides
- Break above swing high = bullish structural shift = LONG
- Break below swing low = bearish structural shift = SHORT

This is pure price action, uncorrelated with oscillators or volume indicators.
"""

import numpy as np
import pandas as pd


class MarketStructureBreak:

    def __init__(self, swing_lookback=10, min_bars_between=5):
        self.swing_lookback = swing_lookback
        self.min_bars_between = min_bars_between
        self._ind = None
        self._last_exit_bar = -10

    def _precompute(self, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)
        n = self.swing_lookback

        # Swing high: high[i] > max of surrounding n bars on each side
        rolling_high_left = highs.shift(1).rolling(n).max()
        rolling_high_right = highs.shift(-1).rolling(n).max().shift(n)
        swing_high = (highs > rolling_high_left) & (highs > rolling_high_right)

        # Swing low: low[i] < min of surrounding n bars on each side
        rolling_low_left = lows.shift(1).rolling(n).min()
        rolling_low_right = lows.shift(-1).rolling(n).min().shift(n)
        swing_low = (lows < rolling_low_left) & (lows < rolling_low_right)

        # Most recent swing high/low level (for breakout detection)
        # Track last swing high price and last swing low price
        last_swing_high = pd.Series(index=data.index, dtype=float)
        last_swing_low = pd.Series(index=data.index, dtype=float)

        cur_sh = np.nan
        cur_sl = np.nan
        sh_vals = []
        sl_vals = []

        for k in range(len(data)):
            if swing_high.iloc[k]:
                cur_sh = float(highs.iloc[k])
            if swing_low.iloc[k]:
                cur_sl = float(lows.iloc[k])
            sh_vals.append(cur_sh)
            sl_vals.append(cur_sl)

        last_swing_high = pd.Series(sh_vals, index=data.index)
        last_swing_low = pd.Series(sl_vals, index=data.index)

        # ATR
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # Daily trend EMAs
        ema_d_fast = closes.ewm(span=192, adjust=False).mean()
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean()

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # Previous close (for fresh break detection)
        prev_close_shifted = closes.shift(1)

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "last_swing_high": last_swing_high,
            "last_swing_low": last_swing_low,
            "atr": atr, "vol_ratio": vol_ratio, "rsi": rsi,
            "prev_close": prev_close_shifted,
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

        if pd.isna(atr) or atr <= 0:
            return None
        if pd.isna(ind["last_swing_high"]) or pd.isna(ind["last_swing_low"]):
            return None

        trend = self._daily_trend(i)
        swing_high = float(ind["last_swing_high"])
        swing_low = float(ind["last_swing_low"])
        prev_price = float(ind["prev_close"])

        # LONG: fresh break above swing high (price was below, now above)
        if (price > swing_high and
            prev_price <= swing_high and
            ind["vol_ratio"] > 1.2 and
            ind["rsi"] > 45 and ind["rsi"] < 78 and
            trend in ("UP", "FLAT")):

            score = 50
            if trend == "UP":
                score += 25
            if ind["vol_ratio"] > 2.0:
                score += 15
            elif ind["vol_ratio"] > 1.5:
                score += 8
            if ind["rsi"] > 55:
                score += 10

            lev = 1.5 + (score - 50) / 100 * 2.0
            lev = min(3.5, max(1.5, lev))

            return {
                "action": "LONG",
                "signal": f"MSB_L(s{score:.0f})",
                "stop": price - atr * 2.0,
                "target": price + atr * 7,
                "leverage": lev,
            }

        # SHORT: fresh break below swing low
        if (price < swing_low and
            prev_price >= swing_low and
            ind["vol_ratio"] > 1.2 and
            ind["rsi"] < 55 and ind["rsi"] > 22 and
            trend in ("DOWN", "FLAT")):

            score = 50
            if trend == "DOWN":
                score += 25
            if ind["vol_ratio"] > 2.0:
                score += 15
            elif ind["vol_ratio"] > 1.5:
                score += 8
            if ind["rsi"] < 45:
                score += 10

            lev = 1.5 + (score - 50) / 100 * 2.0
            lev = min(3.5, max(1.5, lev))

            return {
                "action": "SHORT",
                "signal": f"MSB_S(s{score:.0f})",
                "stop": price + atr * 2.0,
                "target": price - atr * 7,
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

        # Trend flip
        trend = self._daily_trend(i)
        if trade.direction == "LONG" and trend == "DOWN":
            self._last_exit_bar = i
            return "TREND_FLIP"
        if trade.direction == "SHORT" and trend == "UP":
            self._last_exit_bar = i
            return "TREND_FLIP"

        return None
