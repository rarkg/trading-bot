"""
Volatility-Targeted Squeeze (V9b).
Key insight: adjust position size INVERSELY to realized volatility.
- When vol is low: market is calm → squeeze is more reliable → bigger size
- When vol is high: crash mode → stop gets hit more → smaller size
- This keeps daily P&L volatility roughly constant regardless of market regime
- Combined with squeeze signal: best of both worlds

Volatility targeting formula:
  position_size = (target_daily_vol / realized_vol_20d) * capital
  Capped at 4x leverage max

This is similar to risk parity but applied to a single signal.
"""

import numpy as np
import pandas as pd


class VolTargetSqueeze:

    def __init__(self, target_daily_vol_pct=1.5, max_leverage=5.0, min_leverage=1.0):
        """
        Args:
            target_daily_vol_pct: Target daily portfolio volatility as % (1.5 = 1.5%/day)
            max_leverage: Maximum leverage cap
            min_leverage: Minimum leverage floor
        """
        self.target_daily_vol = target_daily_vol_pct / 100
        self.max_leverage = max_leverage
        self.min_leverage = min_leverage
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

        ema8 = closes.ewm(span=8, adjust=False).mean()
        ema21 = closes.ewm(span=21, adjust=False).mean()
        ema55 = closes.ewm(span=55, adjust=False).mean()

        ema_d_fast = closes.ewm(span=192, adjust=False).mean()
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean()

        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # Realized hourly volatility (annualized daily)
        returns = closes.pct_change()
        # Rolling 20-bar realized vol (hourly)
        realized_vol_hourly = returns.rolling(20).std()
        # Convert to daily equivalent (sqrt(24))
        realized_vol_daily = realized_vol_hourly * np.sqrt(24)

        # Vol-based leverage
        vol_leverage = self.target_daily_vol / realized_vol_daily.replace(0, self.target_daily_vol)
        vol_leverage = vol_leverage.clip(self.min_leverage, self.max_leverage)

        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        # Bollinger squeeze
        sma20 = closes.rolling(20).mean()
        std20 = closes.rolling(20).std()
        bb_width = (std20 / sma20.replace(0, 1)) * 100
        bb_width_avg = bb_width.rolling(120).mean()
        is_squeeze = bb_width < (bb_width_avg * 0.70)

        # Candle
        body_ratio = (closes - opens).abs() / (highs - lows).replace(0, 1)
        bullish_int = (closes > opens).astype(int)

        d_slope = (ema_d_fast - ema_d_fast.shift(24)) / ema_d_fast.shift(24).replace(0, 1) * 100

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "ema8": ema8, "ema21": ema21, "ema55": ema55,
            "ema_d_fast": ema_d_fast, "ema_d_slow": ema_d_slow, "ema_d_trend": ema_d_trend,
            "atr": atr,
            "realized_vol_daily": realized_vol_daily,
            "vol_leverage": vol_leverage,
            "rsi": rsi, "vol_ratio": vol_ratio,
            "bb_width": bb_width, "bb_width_avg": bb_width_avg, "is_squeeze": is_squeeze,
            "body_ratio": body_ratio, "bullish": bullish_int,
            "d_slope": d_slope,
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

        if direction == "LONG" and trend == "UP":
            score += 30
        elif direction == "LONG" and trend == "FLAT":
            score += 10
        elif direction == "SHORT" and trend == "DOWN":
            score += 30
        elif direction == "SHORT" and trend == "FLAT":
            score += 10
        else:
            return 0

        if direction == "LONG" and ind["ema8"] > ind["ema21"] > ind["ema55"]:
            score += 20
        elif direction == "LONG" and ind["ema8"] > ind["ema21"]:
            score += 10
        elif direction == "SHORT" and ind["ema8"] < ind["ema21"] < ind["ema55"]:
            score += 20
        elif direction == "SHORT" and ind["ema8"] < ind["ema21"]:
            score += 10

        if ind["vol_ratio"] > 2.5:
            score += 20
        elif ind["vol_ratio"] > 1.8:
            score += 15
        elif ind["vol_ratio"] > 1.3:
            score += 10

        if ind["body_ratio"] > 0.7:
            score += 15
        elif ind["body_ratio"] > 0.5:
            score += 10

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

        if pd.isna(atr) or atr <= 0:
            return None

        if not ind["is_squeeze"]:
            return None

        # Get volatility-adjusted leverage
        vol_lev = float(ind["vol_leverage"])

        if (price > ind["prev_high"] and
            ind["bullish"] == 1 and
            ind["body_ratio"] > 0.4 and
            ind["vol_ratio"] > 1.2 and
            ind["rsi"] < 75):

            score = self._confidence(i, "LONG")
            if score < 50:
                return None

            # Scale leverage by score AND volatility targeting
            score_lev = 1.5 + (score - 50) / 100 * 2.5  # 1.5-4.0x
            combined_lev = min(self.max_leverage, score_lev * (vol_lev / 3.0))

            self._trailing_stop = price - (atr * 2.5)
            self._best_price = price
            self._entry_bar = i

            return {
                "action": "LONG",
                "signal": f"VTS_L(s{score},v{vol_lev:.1f})",
                "stop": price - (atr * 2.5),
                "target": price + (atr * 10),
                "leverage": round(combined_lev, 1),
            }

        if (price < ind["prev_low"] and
            ind["bullish"] == 0 and
            ind["body_ratio"] > 0.4 and
            ind["vol_ratio"] > 1.2 and
            ind["rsi"] > 25):

            score = self._confidence(i, "SHORT")
            if score < 50:
                return None

            score_lev = 1.5 + (score - 50) / 100 * 2.5
            combined_lev = min(self.max_leverage, score_lev * (vol_lev / 3.0))

            self._trailing_stop = price + (atr * 2.5)
            self._best_price = price
            self._entry_bar = i

            return {
                "action": "SHORT",
                "signal": f"VTS_S(s{score},v{vol_lev:.1f})",
                "stop": price + (atr * 2.5),
                "target": price - (atr * 10),
                "leverage": round(combined_lev, 1),
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
