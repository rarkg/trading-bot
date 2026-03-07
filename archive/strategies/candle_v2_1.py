"""
Candle V2.1 — Candlestick Pattern-First Trading Strategy.

V2.1.5: Best scoring from V2.1.3 + moderate R:R + leverage.
- Score-based entry: RSI + BB + volume + ADX confirmations
- R:R 2.0:2.5 (need ~44% WR to profit)
- 3x leverage on T1 patterns, 2x on T2
- ONLY best patterns from previous iterations
- No trailing stop (V15 finding: trailing hurts)
"""

import numpy as np
import pandas as pd
import talib


TIER_1 = {
    "CDLENGULFING", "CDLHAMMER", "CDLSHOOTINGSTAR",
    "CDLMORNINGSTAR", "CDLEVENINGSTAR",
    "CDL3WHITESOLDIERS", "CDL3BLACKCROWS",
    "CDLMARUBOZU", "CDLPIERCING", "CDLDARKCLOUDCOVER",
}

TIER_2 = {
    "CDLHARAMI", "CDLHARAMICROSS",
    "CDLDRAGONFLYDOJI", "CDLGRAVESTONEDOJI",
    "CDLHANGINGMAN", "CDLINVERTEDHAMMER",
    "CDL3INSIDE", "CDL3OUTSIDE",
    "CDLMORNINGDOJISTAR", "CDLEVENINGDOJISTAR",
    "CDLKICKING", "CDLKICKINGBYLENGTH",
}

ALL_CDL_FUNCTIONS = [
    "CDL2CROWS", "CDL3BLACKCROWS", "CDL3INSIDE", "CDL3LINESTRIKE",
    "CDL3OUTSIDE", "CDL3STARSINSOUTH", "CDL3WHITESOLDIERS",
    "CDLABANDONEDBABY", "CDLADVANCEBLOCK", "CDLBELTHOLD", "CDLBREAKAWAY",
    "CDLCLOSINGMARUBOZU", "CDLCONCEALBABYSWALL", "CDLCOUNTERATTACK",
    "CDLDARKCLOUDCOVER", "CDLDOJI", "CDLDOJISTAR", "CDLDRAGONFLYDOJI",
    "CDLENGULFING", "CDLEVENINGDOJISTAR", "CDLEVENINGSTAR",
    "CDLGAPSIDESIDEWHITE", "CDLGRAVESTONEDOJI", "CDLHAMMER",
    "CDLHANGINGMAN", "CDLHARAMI", "CDLHARAMICROSS", "CDLHIGHWAVE",
    "CDLHIKKAKE", "CDLHIKKAKEMOD", "CDLHOMINGPIGEON",
    "CDLIDENTICAL3CROWS", "CDLINNECK", "CDLINVERTEDHAMMER",
    "CDLKICKING", "CDLKICKINGBYLENGTH", "CDLLADDERBOTTOM",
    "CDLLONGLEGGEDDOJI", "CDLLONGLINE", "CDLMARUBOZU", "CDLMATCHINGLOW",
    "CDLMATHOLD", "CDLMORNINGDOJISTAR", "CDLMORNINGSTAR", "CDLONNECK",
    "CDLPIERCING", "CDLRICKSHAWMAN", "CDLRISEFALL3METHODS",
    "CDLSEPARATINGLINES", "CDLSHOOTINGSTAR", "CDLSHORTLINE",
    "CDLSPINNINGTOP", "CDLSTALLEDPATTERN", "CDLSTICKSANDWICH",
    "CDLTAKURI", "CDLTASUKIGAP", "CDLTHRUSTING", "CDLTRISTAR",
    "CDLUNIQUE3RIVER", "CDLUPSIDEGAP2CROWS", "CDLXSIDEGAP3METHODS",
]

# Best patterns from V2.1.2 (>36% WR with volume filter + confirmations)
BEST_PATTERNS = {
    "CDLMARUBOZU",          # T1: 45.6% WR, $+292, THE winner
    "CDLBELTHOLD",          # T3: 37.3% WR, stable across assets
    "CDLCLOSINGMARUBOZU",   # T3: 39.6% WR
    "CDLHIKKAKE",           # T3: 36.2% WR, many trades
    "CDLHIGHWAVE",          # T3: 36.7% WR
    "CDLADVANCEBLOCK",      # T3: 36.5% WR (shorts only)
    "CDLSPINNINGTOP",       # T3: 38.9% WR
    "CDLENGULFING",         # T1: 36.4% WR, high volume
    "CDLSHORTLINE",         # T3: 41.0% WR
}


def get_tier(pattern_name):
    if pattern_name in TIER_1:
        return 1
    elif pattern_name in TIER_2:
        return 2
    return 3


class CandleV2_1:
    """Candlestick pattern-first strategy."""

    def __init__(self, asset_name="BTC", enabled_patterns=None, min_tier=3,
                 require_confirmations=None, cooldown=8,
                 stop_atr=2.0, target_atr=2.5, time_exit_bars=36,
                 base_leverage=3.0):
        self.asset_name = asset_name
        self.min_tier = min_tier
        self.cooldown = cooldown
        self.stop_atr = stop_atr
        self.target_atr = target_atr
        self.time_exit_bars = time_exit_bars
        self.base_leverage = base_leverage

        if enabled_patterns is not None:
            self.enabled_patterns = enabled_patterns
        else:
            self.enabled_patterns = BEST_PATTERNS.copy()

        self.confirmations_required = require_confirmations or {1: 1, 2: 2, 3: 2}

        self._indicators = {}
        self._last_data_id = None
        self._entry_pattern = None
        self._entry_bar = None
        self._entry_tier = None
        self._bars_in_trade = 0
        self._last_exit_bar = -100
        self.pattern_stats = {}

    def _compute_indicators(self, data, i):
        data_id = id(data)
        if data_id == self._last_data_id:
            return
        self._last_data_id = data_id

        o = data["open"].values.astype(float)
        h = data["high"].values.astype(float)
        l = data["low"].values.astype(float)
        c = data["close"].values.astype(float)
        v = data["volume"].values.astype(float)

        self._indicators["rsi"] = talib.RSI(c, timeperiod=14)
        self._indicators["ema21"] = talib.EMA(c, timeperiod=21)
        self._indicators["ema50"] = talib.EMA(c, timeperiod=50)
        self._indicators["atr"] = talib.ATR(h, l, c, timeperiod=14)
        self._indicators["bb_upper"], self._indicators["bb_mid"], self._indicators["bb_lower"] = \
            talib.BBANDS(c, timeperiod=20, nbdevup=2, nbdevdn=2)
        self._indicators["vol_sma"] = talib.SMA(v, timeperiod=20)
        self._indicators["adx"] = talib.ADX(h, l, c, timeperiod=14)

        self._indicators["patterns"] = {}
        for pat_name in ALL_CDL_FUNCTIONS:
            func = getattr(talib, pat_name)
            self._indicators["patterns"][pat_name] = func(o, h, l, c)

    def _score_setup(self, data, i, direction):
        """Score the quality of the setup (0-5). Higher = more confirmations."""
        score = 0
        ind = self._indicators

        rsi = ind["rsi"][i]
        ema21 = ind["ema21"][i]
        ema50 = ind["ema50"][i]
        vol = float(data.iloc[i]["volume"])
        vol_avg = ind["vol_sma"][i]
        close = float(data.iloc[i]["close"])
        bb_upper = ind["bb_upper"][i]
        bb_lower = ind["bb_lower"][i]
        adx = ind["adx"][i]

        has_volume = (not np.isnan(vol_avg) and vol_avg > 0 and vol > vol_avg * 1.1)
        if has_volume:
            score += 1

        if direction == "LONG":
            if not np.isnan(rsi) and rsi < 35:
                score += 1
            if not np.isnan(rsi) and rsi < 25:
                score += 1
            if not np.isnan(bb_lower) and close <= bb_lower * 1.01:
                score += 1
            # Trend with: EMA alignment helps for continuation
            if not np.isnan(ema21) and not np.isnan(ema50) and ema21 > ema50:
                score += 0.5
        elif direction == "SHORT":
            if not np.isnan(rsi) and rsi > 65:
                score += 1
            if not np.isnan(rsi) and rsi > 75:
                score += 1
            if not np.isnan(bb_upper) and close >= bb_upper * 0.99:
                score += 1
            if not np.isnan(ema21) and not np.isnan(ema50) and ema21 < ema50:
                score += 0.5

        # Low ADX = good for mean reversion patterns
        if not np.isnan(adx) and adx < 25:
            score += 0.5

        return score

    def generate_signal(self, data, i):
        self._compute_indicators(data, i)

        if i < 200:
            return None

        if i - self._last_exit_bar < self.cooldown:
            return None

        atr = self._indicators["atr"][i]
        if np.isnan(atr) or atr <= 0:
            return None

        price = float(data.iloc[i]["close"])

        # Skip very strong trends — patterns work better in ranges
        adx = self._indicators["adx"][i]
        if not np.isnan(adx) and adx > 40:
            return None

        best_signal = None
        best_tier = 4
        best_pattern = None
        best_score = 0

        for pat_name in self.enabled_patterns:
            tier = get_tier(pat_name)
            if tier > self.min_tier:
                continue

            val = self._indicators["patterns"][pat_name][i]
            if val == 0:
                continue

            direction = "LONG" if val > 0 else "SHORT"

            score = self._score_setup(data, i, direction)
            needed = self.confirmations_required.get(tier, 2)

            if score < needed:
                continue

            if score > best_score or (score == best_score and tier < best_tier):
                best_tier = tier
                best_pattern = pat_name
                best_score = score
                best_signal = direction

        if best_signal is None:
            return None

        if best_signal == "LONG":
            stop = price - atr * self.stop_atr
            target = price + atr * self.target_atr
        else:
            stop = price + atr * self.stop_atr
            target = price - atr * self.target_atr

        # Leverage by tier
        tier_lev = {1: self.base_leverage, 2: self.base_leverage * 0.66, 3: self.base_leverage * 0.5}
        lev = tier_lev.get(best_tier, 1.0)
        if best_score >= 3:
            lev *= 1.2

        self._entry_pattern = best_pattern
        self._entry_bar = i
        self._entry_tier = best_tier
        self._bars_in_trade = 0

        return {
            "action": best_signal,
            "stop": stop,
            "target": target,
            "signal": f"{best_pattern}(T{best_tier})",
            "leverage": lev,
            "market_regime": f"TIER_{best_tier}",
            "rsi_at_entry": float(self._indicators["rsi"][i]) if not np.isnan(self._indicators["rsi"][i]) else None,
            "atr_at_entry": float(atr),
        }

    def check_exit(self, data, i, open_trade):
        self._compute_indicators(data, i)
        self._bars_in_trade += 1

        # Time exit
        if self._bars_in_trade >= self.time_exit_bars:
            self._last_exit_bar = i
            return "TIME_EXIT"

        # NO trailing stop — V15 finding: trailing hurts trades
        # Let stop/target/time handle exits

        return None

    def record_trade_result(self, pattern, direction, won):
        if pattern not in self.pattern_stats:
            self.pattern_stats[pattern] = {
                "long": {"wins": 0, "losses": 0},
                "short": {"wins": 0, "losses": 0},
            }
        key = direction.lower()
        if won:
            self.pattern_stats[pattern][key]["wins"] += 1
        else:
            self.pattern_stats[pattern][key]["losses"] += 1
