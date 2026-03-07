"""
V10 — Optimized Squeeze with Data-Driven Filters.

Based on trade analysis of V7:
- RSI 45-65 at entry: best win rate and pnl
- Vol ratio 1.0-1.35 OR >2.4: best performers (skip the 1.35-2.4 "noise" zone)
- Score 55-80: optimal (>80 = overextended entries)
- ATR < 2.5%: low-volatility entries more reliable
- Score >80 but daily trend is STRONG UP/DOWN still OK for shorts/longs

Key improvements:
1. Tighter RSI gate: 45-68 for longs, 32-55 for shorts
2. Volume filter: skip vol_ratio 1.35-2.3 (unclear momentum)
3. Score cap: 80 max (treat score 80+ same as 80)
4. Bear market awareness: when below 200d EMA, only trade shorts
5. BTC crash guard: if BTC dropped >10% in 24h, no new longs
"""

import numpy as np
import pandas as pd


class SqueezeV10:

    def __init__(self, fixed_leverage=None, btc_data=None):
        """
        Args:
            fixed_leverage: Override dynamic leverage
            btc_data: BTC hourly data for crash guard filter
        """
        self.fixed_leverage = fixed_leverage
        self._ind = None
        self._btc_roc = None  # BTC 24h rate of change for crash guard
        self._trailing_stop = None
        self._best_price = None
        self._last_exit_bar = -12
        self._entry_bar = -1

    def set_btc_data(self, btc_data):
        """Set BTC data for crash guard."""
        btc_closes = btc_data["close"].astype(float)
        self._btc_roc = btc_closes.pct_change(24) * 100  # 24h % change
        self._btc_roc.index = btc_data.index

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

        # Daily-equivalent EMAs
        ema_d_fast = closes.ewm(span=192, adjust=False).mean()
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean()
        ema_200d = closes.ewm(span=4800, adjust=False).mean()

        # ATR
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()
        atr_pct = atr / closes * 100

        # ADX
        plus_dm = highs.diff()
        minus_dm = -lows.diff()
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)
        atr14_sum = tr.rolling(14).sum()
        plus_di = 100 * plus_dm.rolling(14).sum() / atr14_sum.replace(0, 1)
        minus_di = 100 * minus_dm.rolling(14).sum() / atr14_sum.replace(0, 1)
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        adx = dx.rolling(14).mean()

        # Range position (50-bar high/low)
        range_high = highs.rolling(50).max()
        range_low = lows.rolling(50).min()
        range_pct = (closes - range_low) / (range_high - range_low).replace(0, 1)

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
        is_squeeze = bb_width < (bb_width_avg * 0.65)  # Original V7 threshold

        # Candle
        body_ratio = (closes - opens).abs() / (highs - lows).replace(0, 1)
        bullish_int = (closes > opens).astype(int)

        d_slope = (ema_d_fast - ema_d_fast.shift(24)) / ema_d_fast.shift(24).replace(0, 1) * 100
        ema21_slope = (ema21 - ema21.shift(5)) / ema21.shift(5).replace(0, 1) * 100

        bear_market = (closes < ema_200d).astype(int)

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "ema8": ema8, "ema21": ema21, "ema55": ema55,
            "ema_d_fast": ema_d_fast, "ema_d_slow": ema_d_slow,
            "ema_d_trend": ema_d_trend, "ema_200d": ema_200d,
            "atr": atr, "atr_pct": atr_pct, "rsi": rsi,
            "vol_ratio": vol_ratio,
            "bb_width": bb_width, "bb_width_avg": bb_width_avg, "is_squeeze": is_squeeze,
            "body_ratio": body_ratio, "bullish": bullish_int,
            "d_slope": d_slope, "ema21_slope": ema21_slope,
            "bear_market": bear_market,
            "adx": adx, "range_pct": range_pct,
            "prev_high": highs.shift(1), "prev_low": lows.shift(1),
        }, index=data.index)

    def _market_regime(self, i):
        ind = self._ind.iloc[i]
        close = float(ind["close"])
        ema_200d = float(ind["ema_200d"])
        adx_val = float(ind["adx"]) if not pd.isna(ind["adx"]) else 0
        if close > ema_200d and adx_val > 25:
            return "bull"
        elif close < ema_200d and adx_val > 25:
            return "bear"
        return "sideways"

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
            return 0  # Counter-trend = no trade

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

    def _btc_crashed(self, timestamp):
        """Check if BTC dropped >10% in the last 24 hours."""
        if self._btc_roc is None:
            return False
        try:
            # Find nearest timestamp in BTC data
            idx = self._btc_roc.index.get_indexer([timestamp], method="nearest")[0]
            if idx < 0 or idx >= len(self._btc_roc):
                return False
            val = float(self._btc_roc.iloc[idx])
            return val < -10.0  # BTC dropped >10% in 24h
        except Exception:
            return False

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
        atr_pct = float(ind["atr_pct"])
        rsi = float(ind["rsi"])
        vol_ratio = float(ind["vol_ratio"])

        if pd.isna(atr) or atr <= 0:
            return None

        if not ind["is_squeeze"]:
            return None

        # V10 filter: skip very high ATR entries (>3% = chaotic, unreliable)
        if atr_pct > 3.5:
            return None

        # V10 filter: optimal volume zones (skip medium-vol noise)
        # Best: 1.0-1.35x (quiet breakout) OR >2.3x (clear momentum)
        # Worst: 1.35-2.3x (ambiguous)
        in_good_vol_zone = (vol_ratio <= 1.35) or (vol_ratio >= 2.3)
        if not in_good_vol_zone:
            return None

        # Bear market mode: when below 200d EMA, only allow shorts
        is_bear = ind["bear_market"] == 1

        # LONG signal
        if (price > ind["prev_high"] and
            ind["bullish"] == 1 and
            ind["body_ratio"] > 0.4 and
            45 <= rsi <= 68 and  # V10 tighter RSI gate
            not is_bear):  # No longs in bear market

            # BTC crash guard
            if self._btc_crashed(data.index[i]):
                return None

            score = self._confidence(i, "LONG")
            if score < 50:
                return None

            # Cap score at 80 (higher = overextended)
            score = min(score, 80)

            if self.fixed_leverage:
                lev = self.fixed_leverage
            else:
                if score >= 75:
                    lev = 3.5
                elif score >= 65:
                    lev = 3.0
                elif score >= 55:
                    lev = 2.5
                else:
                    lev = 2.0

            self._trailing_stop = price - (atr * 2.5)
            self._best_price = price
            self._entry_bar = i

            return {
                "action": "LONG",
                "signal": f"V10_L(s{score})",
                "stop": price - (atr * 2.5),
                "target": price + (atr * 12),  # Slightly wider target to capture more big moves
                "leverage": lev,
                "confidence_score": score,
                "rsi_at_entry": rsi,
                "atr_at_entry": atr,
                "atr_pct_at_entry": atr_pct,
                "vol_ratio_at_entry": vol_ratio,
                "bb_width_at_entry": float(ind["bb_width"]),
                "ema_trend_at_entry": self._daily_trend(i),
                "range_position_at_entry": float(ind["range_pct"]) if not pd.isna(ind["range_pct"]) else None,
                "adx_at_entry": float(ind["adx"]) if not pd.isna(ind["adx"]) else None,
                "market_regime": self._market_regime(i),
            }

        # SHORT signal
        if (price < ind["prev_low"] and
            ind["bullish"] == 0 and
            ind["body_ratio"] > 0.4 and
            32 <= rsi <= 55 and  # V10 tighter RSI gate for shorts
            vol_ratio > 1.2):

            score = self._confidence(i, "SHORT")
            if score < 50:
                return None

            score = min(score, 80)

            if self.fixed_leverage:
                lev = self.fixed_leverage
            else:
                if score >= 75:
                    lev = 3.5
                elif score >= 65:
                    lev = 3.0
                elif score >= 55:
                    lev = 2.5
                else:
                    lev = 2.0

            self._trailing_stop = price + (atr * 2.5)
            self._best_price = price
            self._entry_bar = i

            return {
                "action": "SHORT",
                "signal": f"V10_S(s{score})",
                "stop": price + (atr * 2.5),
                "target": price - (atr * 12),
                "leverage": lev,
                "confidence_score": score,
                "rsi_at_entry": rsi,
                "atr_at_entry": atr,
                "atr_pct_at_entry": atr_pct,
                "vol_ratio_at_entry": vol_ratio,
                "bb_width_at_entry": float(ind["bb_width"]),
                "ema_trend_at_entry": self._daily_trend(i),
                "range_position_at_entry": float(ind["range_pct"]) if not pd.isna(ind["range_pct"]) else None,
                "adx_at_entry": float(ind["adx"]) if not pd.isna(ind["adx"]) else None,
                "market_regime": self._market_regime(i),
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

        # Time exit: cut if no progress in 10 bars
        if i - self._entry_bar == 10:
            if trade.direction == "LONG":
                if (price - trade.entry_price) / atr < 0.5:
                    self._last_exit_bar = i
                    return "TIME_EXIT"
            elif (trade.entry_price - price) / atr < 0.5:
                self._last_exit_bar = i
                return "TIME_EXIT"

        # Trailing stop with tightening
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
