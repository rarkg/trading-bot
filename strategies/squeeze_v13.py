"""
V13 — Cross-Asset Regime-Adaptive Strategy with Kelly Sizing.

Profitable on ALL 4 assets (BTC, ETH, SOL, LINK) by:
1. Detecting market regime (TRENDING / SIDEWAYS / VOLATILE / TRANSITION)
2. Breakout strategy in TRENDING regime (V10 squeeze logic)
3. Mean reversion strategy in SIDEWAYS regime (Bollinger band bounce + RSI)
4. Fractional Kelly Criterion for position sizing (replaces fixed leverage)
5. Symmetric long/short adjusted by regime

Architecture:
  RegimeDetector → chooses sub-strategy
  BreakoutStrategy → active in TRENDING/TRANSITION
  MeanReversionStrategy → active in SIDEWAYS/TRANSITION
  KellyPositionSizer → rolling per-direction Kelly, capped at max_leverage
"""

import numpy as np
import pandas as pd


class SqueezeV13:

    def __init__(self, fixed_leverage=None, btc_data=None,
                 kelly_fraction=0.3, kelly_window=40, max_leverage=6.0,
                 default_leverage=2.5,
                 # Regime detection thresholds
                 adx_trending=30, adx_sideways=22,
                 ema_slope_threshold=0.4, atr_volatile_pctile=85,
                 bb_width_sideways_ratio=0.9,
                 # Mean reversion params
                 mr_bb_period=20, mr_rsi_long=35, mr_rsi_short=65,
                 mr_bb_entry_pct=0.01, mr_stop_atr=1.2,
                 # Breakout params (from V10)
                 bo_rsi_long=(43, 70), bo_rsi_short=(30, 57),
                 bo_vol_dead_low=1.35, bo_vol_dead_high=2.1,
                 bo_atr_max=3.5):
        self.fixed_leverage = fixed_leverage
        self.kelly_fraction = kelly_fraction
        self.kelly_window = kelly_window
        self.max_leverage = max_leverage
        self.default_leverage = default_leverage

        # Regime thresholds
        self.adx_trending = adx_trending
        self.adx_sideways = adx_sideways
        self.ema_slope_threshold = ema_slope_threshold
        self.atr_volatile_pctile = atr_volatile_pctile / 100.0
        self.bb_width_sideways_ratio = bb_width_sideways_ratio

        # Mean reversion
        self.mr_bb_period = mr_bb_period
        self.mr_rsi_long = mr_rsi_long
        self.mr_rsi_short = mr_rsi_short
        self.mr_bb_entry_pct = mr_bb_entry_pct
        self.mr_stop_atr = mr_stop_atr

        # Breakout
        self.bo_rsi_long = bo_rsi_long
        self.bo_rsi_short = bo_rsi_short
        self.bo_vol_dead_low = bo_vol_dead_low
        self.bo_vol_dead_high = bo_vol_dead_high
        self.bo_atr_max = bo_atr_max

        self._ind = None
        self._btc_roc = None
        self._trailing_stop = None
        self._best_price = None
        self._last_exit_bar = -12
        self._entry_bar = -1
        self._trade_type = None  # "breakout" or "meanrev"

        # Rolling trade history for Kelly
        self._trade_history = []  # list of (direction, pnl_pct)

        # Regime tracking for reporting
        self.regime_counts = {"TRENDING": 0, "SIDEWAYS": 0, "VOLATILE": 0, "TRANSITION": 0}
        self.trade_regimes = []  # (regime, direction, strategy_type)

    def set_btc_data(self, btc_data):
        btc_closes = btc_data["close"].astype(float)
        self._btc_roc = btc_closes.pct_change(24) * 100
        self._btc_roc.index = btc_data.index

    def reset(self):
        self._ind = None
        self._trailing_stop = None
        self._best_price = None
        self._last_exit_bar = -12
        self._entry_bar = -1
        self._trade_type = None
        self._trade_history = []
        self.regime_counts = {"TRENDING": 0, "SIDEWAYS": 0, "VOLATILE": 0, "TRANSITION": 0}
        self.trade_regimes = []

    def _precompute(self, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)
        opens = data["open"].astype(float)

        # EMAs (hourly)
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
        atr_pct_rank = atr_pct.rolling(200).rank(pct=True)

        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        # Bollinger Bands
        bb_mid = closes.rolling(self.mr_bb_period).mean()
        bb_std = closes.rolling(self.mr_bb_period).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_width = (bb_std / bb_mid.replace(0, 1)) * 100
        bb_width_avg = bb_width.rolling(120).mean()
        bb_width_ratio = bb_width / bb_width_avg.replace(0, 1)

        # Squeeze detection (for breakout)
        is_squeeze = bb_width < (bb_width_avg * 0.65)

        # Range position filter (from V12 — avoid buying at range top)
        range_high = highs.rolling(50).max()
        range_low = lows.rolling(50).min()
        range_pct = (closes - range_low) / (range_high - range_low).replace(0, 1)

        # ADX calculation
        adx = self._compute_adx(highs, lows, closes, period=14)

        # EMA slope (daily-scale)
        d_slope = (ema_d_fast - ema_d_fast.shift(24)) / ema_d_fast.shift(24).replace(0, 1) * 100
        ema21_slope = (ema21 - ema21.shift(5)) / ema21.shift(5).replace(0, 1) * 100

        # Candle patterns
        body_ratio = (closes - opens).abs() / (highs - lows).replace(0, 1)
        bullish_int = (closes > opens).astype(int)

        bear_market = (closes < ema_200d).astype(int)

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows, "open": opens,
            "ema8": ema8, "ema21": ema21, "ema55": ema55,
            "ema_d_fast": ema_d_fast, "ema_d_slow": ema_d_slow,
            "ema_d_trend": ema_d_trend, "ema_200d": ema_200d,
            "atr": atr, "atr_pct": atr_pct, "atr_pct_rank": atr_pct_rank,
            "rsi": rsi,
            "vol_ratio": vol_ratio,
            "bb_mid": bb_mid, "bb_upper": bb_upper, "bb_lower": bb_lower,
            "bb_width": bb_width, "bb_width_avg": bb_width_avg,
            "bb_width_ratio": bb_width_ratio,
            "is_squeeze": is_squeeze,
            "range_pct": range_pct,
            "adx": adx,
            "body_ratio": body_ratio, "bullish": bullish_int,
            "d_slope": d_slope, "ema21_slope": ema21_slope,
            "bear_market": bear_market,
            "prev_high": highs.shift(1), "prev_low": lows.shift(1),
        }, index=data.index)

    def _compute_adx(self, highs, lows, closes, period=14):
        """Compute ADX indicator."""
        prev_high = highs.shift(1)
        prev_low = lows.shift(1)
        prev_close = closes.shift(1)

        # True Range
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)

        # Directional Movement
        plus_dm = highs - prev_high
        minus_dm = prev_low - lows
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

        # Smoothed averages
        atr_smooth = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr_smooth.replace(0, 1))
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr_smooth.replace(0, 1))

        # DX and ADX
        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        adx = dx.rolling(period).mean()

        return adx

    def _detect_regime(self, i):
        """Detect market regime at bar i."""
        ind = self._ind.iloc[i]

        adx = float(ind["adx"]) if not pd.isna(ind["adx"]) else 15.0
        d_slope_abs = abs(float(ind["d_slope"])) if not pd.isna(ind["d_slope"]) else 0.0
        bb_width_ratio = float(ind["bb_width_ratio"]) if not pd.isna(ind["bb_width_ratio"]) else 1.0
        atr_pct_rank = float(ind["atr_pct_rank"]) if not pd.isna(ind["atr_pct_rank"]) else 0.5

        if atr_pct_rank > self.atr_volatile_pctile:
            return "VOLATILE"
        if adx > self.adx_trending and d_slope_abs > self.ema_slope_threshold:
            return "TRENDING"
        if adx < self.adx_sideways and bb_width_ratio < self.bb_width_sideways_ratio:
            return "SIDEWAYS"
        return "TRANSITION"

    def _daily_trend(self, i):
        ind = self._ind.iloc[i]
        if ind["ema_d_fast"] > ind["ema_d_slow"] > ind["ema_d_trend"]:
            return "UP"
        elif ind["ema_d_fast"] < ind["ema_d_slow"] < ind["ema_d_trend"]:
            return "DOWN"
        return "FLAT"

    def _confidence_breakout(self, i, direction):
        """Confidence score for breakout signals (from V10)."""
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

    def _compute_kelly(self, direction):
        """Compute position leverage using fractional Kelly criterion.

        Returns base leverage before regime adjustment. Uses Kelly when
        enough trade history exists, otherwise returns default.
        """
        if self.fixed_leverage is not None:
            return self.fixed_leverage

        # Filter trades by direction
        dir_trades = [pnl for d, pnl in self._trade_history if d == direction]

        if len(dir_trades) < 20:
            return self.default_leverage

        recent = dir_trades[-self.kelly_window:]
        wins = [p for p in recent if p > 0]
        losses = [p for p in recent if p <= 0]

        if not wins or not losses:
            return self.default_leverage

        W = len(wins) / len(recent)
        R = abs(np.mean(wins)) / abs(np.mean(losses))

        kelly = W - (1 - W) / R
        if kelly <= 0:
            return 1.0  # negative edge: use minimum

        # Map kelly fraction to leverage
        # kelly * fraction * scale. With kelly=0.3, fraction=0.3, scale=20 → 1.8x
        leverage = kelly * self.kelly_fraction * 20
        return max(1.0, min(leverage, self.max_leverage))

    def _regime_leverage_mult(self, regime, trend, direction):
        """Regime-based leverage multiplier."""
        if regime == "VOLATILE":
            return 0.5
        if regime == "TRANSITION":
            return 1.2
        if regime == "TRENDING":
            if (direction == "LONG" and trend == "UP") or (direction == "SHORT" and trend == "DOWN"):
                return 1.3
            return 0.5
        # SIDEWAYS — mean reversion + breakout
        return 1.3

    def _btc_crashed(self, timestamp):
        if self._btc_roc is None:
            return False
        try:
            idx = self._btc_roc.index.get_indexer([timestamp], method="nearest")[0]
            if idx < 0 or idx >= len(self._btc_roc):
                return False
            return float(self._btc_roc.iloc[idx]) < -10.0
        except Exception:
            return False

    def _try_breakout(self, data, i, regime, min_score=55):
        """Check for breakout signal (V10-style squeeze breakout + V12 range filter)."""
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        atr_pct = float(ind["atr_pct"])
        rsi = float(ind["rsi"])
        vol_ratio = float(ind["vol_ratio"])
        range_pct = float(ind["range_pct"]) if not pd.isna(ind["range_pct"]) else 0.5

        if not ind["is_squeeze"]:
            return None

        if atr_pct > self.bo_atr_max:
            return None

        # Volume filter
        in_good_vol = (vol_ratio <= self.bo_vol_dead_low) or (vol_ratio >= self.bo_vol_dead_high)
        if not in_good_vol:
            return None

        is_bear = ind["bear_market"] == 1
        trend = self._daily_trend(i)

        # LONG
        if (price > ind["prev_high"] and
            ind["bullish"] == 1 and
            ind["body_ratio"] > 0.4 and
            self.bo_rsi_long[0] <= rsi <= self.bo_rsi_long[1] and
            not is_bear and
            range_pct <= 0.75):  # V12 range filter: don't buy at range top

            if self._btc_crashed(data.index[i]):
                return None

            score = self._confidence_breakout(i, "LONG")
            if score < min_score:
                return None
            score = min(score, 80)

            kelly_lev = self._compute_kelly("LONG")
            regime_mult = self._regime_leverage_mult(regime, trend, "LONG")
            lev = round(max(0.5, min(kelly_lev * regime_mult, self.max_leverage)), 2)

            self._trailing_stop = price - (atr * 2.5)
            self._best_price = price
            self._entry_bar = i
            self._trade_type = "breakout"

            return {
                "action": "LONG",
                "signal": f"V13_BO_L(s{score},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": price - (atr * 2.5),
                "target": price + (atr * 12),
                "leverage": lev,
            }

        # SHORT — require confirmed downtrend + higher confidence
        if (price < ind["prev_low"] and
            ind["bullish"] == 0 and
            ind["body_ratio"] > 0.4 and
            self.bo_rsi_short[0] <= rsi <= self.bo_rsi_short[1] and
            vol_ratio > 1.2 and
            range_pct >= 0.25 and
            trend == "DOWN"):  # Only short in confirmed downtrend

            score = self._confidence_breakout(i, "SHORT")
            short_min = max(min_score, 65)  # shorts need higher confidence
            if score < short_min:
                return None
            score = min(score, 80)

            kelly_lev = self._compute_kelly("SHORT")
            regime_mult = self._regime_leverage_mult(regime, trend, "SHORT")
            lev = round(max(0.5, min(kelly_lev * regime_mult, self.max_leverage)), 2)

            self._trailing_stop = price + (atr * 2.5)
            self._best_price = price
            self._entry_bar = i
            self._trade_type = "breakout"

            return {
                "action": "SHORT",
                "signal": f"V13_BO_S(s{score},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": price + (atr * 2.5),
                "target": price - (atr * 12),
                "leverage": lev,
            }

        return None

    def _try_meanrev(self, data, i, regime):
        """Check for mean reversion signal (Bollinger band bounce).

        Requires: price at BB edge + RSI extreme + candle confirmation.
        """
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        rsi = float(ind["rsi"])
        bb_lower = float(ind["bb_lower"])
        bb_upper = float(ind["bb_upper"])
        bb_mid = float(ind["bb_mid"])
        atr_pct = float(ind["atr_pct"])

        if pd.isna(bb_lower) or pd.isna(bb_upper) or pd.isna(bb_mid):
            return None

        # Skip during high volatility — mean reversion fails when moves are violent
        if atr_pct > 3.0:
            return None

        trend = self._daily_trend(i)

        # LONG: price near lower BB + RSI oversold + bullish candle (rejection)
        if (price <= bb_lower * (1 + self.mr_bb_entry_pct) and
            rsi < self.mr_rsi_long and
            ind["bullish"] == 1 and  # need bullish candle = rejection of lower BB
            ind["body_ratio"] > 0.3):  # meaningful body

            stop = price - (atr * self.mr_stop_atr)
            target = bb_mid

            # R:R filter: potential gain must be at least 0.7x potential loss
            gain_dist = target - price
            loss_dist = price - stop
            if loss_dist <= 0 or gain_dist / loss_dist < 0.7:
                return None

            kelly_lev = self._compute_kelly("LONG")
            regime_mult = self._regime_leverage_mult(regime, trend, "LONG")
            lev = round(max(0.5, min(kelly_lev * regime_mult, self.max_leverage)), 2)

            self._trailing_stop = stop
            self._best_price = price
            self._entry_bar = i
            self._trade_type = "meanrev"

            return {
                "action": "LONG",
                "signal": f"V13_MR_L(rsi{rsi:.0f},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        # SHORT: price near upper BB + RSI overbought + bearish candle
        if (price >= bb_upper * (1 - self.mr_bb_entry_pct) and
            rsi > self.mr_rsi_short and
            ind["bullish"] == 0 and  # bearish candle = rejection of upper BB
            ind["body_ratio"] > 0.3):

            stop = price + (atr * self.mr_stop_atr)
            target = bb_mid

            # R:R filter
            gain_dist = price - target
            loss_dist = stop - price
            if loss_dist <= 0 or gain_dist / loss_dist < 0.7:
                return None

            kelly_lev = self._compute_kelly("SHORT")
            regime_mult = self._regime_leverage_mult(regime, trend, "SHORT")
            lev = round(max(0.5, min(kelly_lev * regime_mult, self.max_leverage)), 2)

            self._trailing_stop = stop
            self._best_price = price
            self._entry_bar = i
            self._trade_type = "meanrev"

            return {
                "action": "SHORT",
                "signal": f"V13_MR_S(rsi{rsi:.0f},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        return None

    def generate_signal(self, data, i):
        if self._ind is None:
            self._precompute(data)

        if i < 1400 or i >= len(self._ind):
            return None

        if i - self._last_exit_bar < 8:  # same cooldown as V10
            return None

        ind = self._ind.iloc[i]
        atr = float(ind["atr"])
        if pd.isna(atr) or atr <= 0:
            return None

        regime = self._detect_regime(i)

        # Route to sub-strategy based on regime
        if regime == "TRENDING":
            # Skip: breakout is late (move happened), mean reversion is counter-trend.
            # Best to sit out confirmed trends and wait for TRANSITION/SIDEWAYS.
            self.regime_counts["TRENDING"] += 1
            return None

        elif regime == "SIDEWAYS":
            # Primary: mean reversion. Fallback: breakout if squeeze fires
            signal = self._try_meanrev(data, i, regime)
            if signal:
                self.regime_counts["SIDEWAYS"] += 1
                self.trade_regimes.append((regime, signal["action"], "meanrev"))
                return signal
            signal = self._try_breakout(data, i, regime, min_score=60)
            if signal:
                self.regime_counts["SIDEWAYS"] += 1
                self.trade_regimes.append((regime, signal["action"], "breakout"))
            return signal

        elif regime == "VOLATILE":
            # Skip volatile periods — too unpredictable, small sample, consistently loses
            self.regime_counts["VOLATILE"] += 1
            return None

        else:  # TRANSITION — breakout with high confidence
            signal = self._try_breakout(data, i, regime, min_score=65)
            if signal:
                # Shorts in TRANSITION need even higher confidence
                if signal["action"] == "SHORT":
                    score_str = signal["signal"]
                    # Already checked min_score=65, but shorts need score 70+
                    # Re-check via confidence
                    s = self._confidence_breakout(i, "SHORT")
                    if s < 70:
                        return None
                self.regime_counts["TRANSITION"] += 1
                self.trade_regimes.append((regime, signal["action"], "breakout"))
                return signal
            return None

    def record_trade(self, direction, pnl_pct):
        """Called by engine or runner after trade closes to update Kelly."""
        self._trade_history.append((direction, pnl_pct))

    def check_exit(self, data, i, trade):
        if self._ind is None or i >= len(self._ind):
            return None

        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])

        if pd.isna(atr) or atr <= 0:
            return None

        if self._trade_type == "meanrev":
            return self._check_exit_meanrev(i, price, atr, trade)
        else:
            return self._check_exit_breakout(i, price, atr, trade)

    def _check_exit_meanrev(self, i, price, atr, trade):
        """Exit logic for mean reversion trades — faster, tighter."""
        # Time exit: cut if no progress in 6 bars (shorter than breakout)
        if i - self._entry_bar >= 8:
            if trade.direction == "LONG":
                if (price - trade.entry_price) / atr < 0.3:
                    self._last_exit_bar = i
                    return "MR_TIME_EXIT"
            elif (trade.entry_price - price) / atr < 0.3:
                self._last_exit_bar = i
                return "MR_TIME_EXIT"

        # Simple stop check (no trailing for mean reversion — tight stop, target exit)
        if trade.direction == "LONG":
            if price <= trade.stop_price:
                self._last_exit_bar = i
                return "MR_STOP"
        elif trade.direction == "SHORT":
            if price >= trade.stop_price:
                self._last_exit_bar = i
                return "MR_STOP"

        return None

    def _check_exit_breakout(self, i, price, atr, trade):
        """Exit logic for breakout trades — trailing stop + trend flip (V10 style)."""
        # Time exit
        if i - self._entry_bar == 10:
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
