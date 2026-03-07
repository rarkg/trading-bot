"""
Candle V2.3 — Push WR to 65%+ with generic params.

Based on V2.2 (60.5% avg WR). New features:
  1. Multi-timeframe candle confirmation (4H + daily)
  2. Candle sequence patterns (2-3 bar)
  3. Body/wick ratio quality filters
  4. Volatility regime filter (BB width percentile)
  5. Time-of-day filter
  6. Stricter score thresholds
"""

import numpy as np
import pandas as pd
import talib


TOP_PATTERNS = {
    "CDLMARUBOZU",
    "CDLSPINNINGTOP",
    "CDLSHORTLINE",
    "CDLCLOSINGMARUBOZU",
    "CDLBELTHOLD",
}

# All TA-Lib patterns for multi-timeframe scanning
ALL_CDL_PATTERNS = [
    "CDLMARUBOZU", "CDLCLOSINGMARUBOZU", "CDLBELTHOLD",
    "CDLENGULFING", "CDLHAMMER", "CDLSHOOTINGSTAR",
    "CDLHANGINGMAN", "CDLINVERTEDHAMMER", "CDLMORNINGSTAR",
    "CDLEVENINGSTAR", "CDL3WHITESOLDIERS", "CDL3BLACKCROWS",
    "CDLPIERCING", "CDLDARKCLOUDCOVER", "CDLHARAMI",
    "CDLDOJI", "CDLDRAGONFLYDOJI", "CDLGRAVESTONEDOJI",
    "CDLSPINNINGTOP", "CDLSHORTLINE", "CDLLONGLINE",
]


class CandleV2_4:
    """V2.3: Multi-timeframe + quality filters for 65%+ WR."""

    def __init__(self,
                 # === V2.2 inherited params ===
                 use_rsi=True,
                 use_stoch_rsi=False,
                 use_williams_r=False,
                 use_macd=False,
                 use_cci=False,
                 use_ema_alignment=False,
                 use_adx=True,
                 use_bb=True,
                 use_atr_percentile=False,
                 use_keltner=False,
                 use_volume=True,
                 use_mfi=False,
                 use_obv_slope=False,
                 use_range_position=False,
                 use_hh_ll=False,
                 pattern_set="top5",
                 hard_filter=False,
                 min_score=2.0,
                 stop_atr=2.0,
                 target_atr=3.0,
                 time_exit_bars=144,
                 cooldown=12,
                 base_leverage=2.0,
                 adx_max=40,
                 direction_filter="both",
                 rsi_period=14,
                 rsi_oversold=30,
                 rsi_overbought=70,
                 bb_period=20,
                 bb_std=2.0,
                 vol_period=20,
                 vol_threshold=1.2,
                 stoch_rsi_period=14,
                 stoch_rsi_os=20,
                 stoch_rsi_ob=80,
                 willr_period=14,
                 willr_os=-80,
                 willr_ob=-20,
                 macd_fast=12,
                 macd_slow=26,
                 macd_signal=9,
                 cci_period=20,
                 cci_os=-100,
                 cci_ob=100,
                 ema_fast=8,
                 ema_mid=21,
                 ema_slow=50,
                 atr_lookback=100,
                 atr_low_pct=25,
                 atr_high_pct=75,
                 kc_period=20,
                 kc_mult=1.5,
                 mfi_period=14,
                 mfi_os=20,
                 mfi_ob=80,
                 obv_slope_period=10,
                 range_period=50,
                 hh_ll_period=20,
                 # === V2.3 NEW params ===
                 # Multi-timeframe
                 use_mtf=False,
                 mtf_require="any",  # "any" = 4H OR daily, "both" = 4H AND daily
                 # Candle sequence
                 use_sequences=False,
                 seq_bonus=1.0,       # Score bonus for matching sequence
                 # Body/wick quality filter
                 use_quality_filter=False,
                 min_body_ratio=0.0,   # Min body/range ratio (0=disabled)
                 min_wick_ratio=0.0,   # Min rejection wick / body ratio
                 vol_on_pattern=1.0,   # Min volume ratio on pattern candle (1.0=disabled)
                 # Volatility regime
                 use_vol_regime=False,
                 bb_width_max_pct=100, # Max BB width percentile (100=disabled)
                 atr_max_pct=100,      # Max ATR percentile (100=disabled)
                 vol_regime_lookback=100,
                 # Time-of-day filter
                 use_tod_filter=False,
                 good_hours=None,      # List of hours to trade (None=all)
                 # Previous candle confirmation
                 use_prev_candle=False,
                 prev_candle_same_dir=False,  # Require prev candle same direction
                 # === V2.4 NEW params ===
                 # Trailing stop
                 use_trailing_stop=True,
                 trail_activation_atr=1.5,   # Start trailing after 1.5 ATR profit
                 trail_distance_atr=0.5,     # Trail 1.0 ATR behind best price
                 # Score-based position sizing
                 use_score_sizing=False,
                 score_size_tiers=None,       # [(min_score, multiplier), ...]
                 # Correlation guard (checked in runner/run_live, not here)
                 use_correlation_guard=False,
                 max_same_direction=3,
                 # Partial profit taking
                 use_partial_tp=False,
                 partial_tp_atr=2.0,          # Take partial at 2 ATR profit
                 partial_tp_pct=0.5,          # Close 50% of position
                 ):
        # Pattern set
        if pattern_set == "marubozu":
            self.patterns = {"CDLMARUBOZU"}
        elif pattern_set == "marubozu_plus":
            self.patterns = {"CDLMARUBOZU", "CDLCLOSINGMARUBOZU"}
        elif pattern_set == "all":
            self.patterns = set(ALL_CDL_PATTERNS)
        else:
            self.patterns = TOP_PATTERNS

        self.hard_filter = hard_filter
        self.direction_filter = direction_filter

        # V2.2 params
        self.use_rsi = use_rsi
        self.use_stoch_rsi = use_stoch_rsi
        self.use_williams_r = use_williams_r
        self.use_macd = use_macd
        self.use_cci = use_cci
        self.use_ema_alignment = use_ema_alignment
        self.use_adx = use_adx
        self.use_bb = use_bb
        self.use_atr_percentile = use_atr_percentile
        self.use_keltner = use_keltner
        self.use_volume = use_volume
        self.use_mfi = use_mfi
        self.use_obv_slope = use_obv_slope
        self.use_range_position = use_range_position
        self.use_hh_ll = use_hh_ll

        self.min_score = min_score
        self.stop_atr = stop_atr
        self.target_atr = target_atr
        self.time_exit_bars = time_exit_bars
        self.cooldown = cooldown
        self.base_leverage = base_leverage
        self.adx_max = adx_max

        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.bb_period = bb_period
        self.bb_std = bb_std
        self.vol_period = vol_period
        self.vol_threshold = vol_threshold
        self.stoch_rsi_period = stoch_rsi_period
        self.stoch_rsi_os = stoch_rsi_os
        self.stoch_rsi_ob = stoch_rsi_ob
        self.willr_period = willr_period
        self.willr_os = willr_os
        self.willr_ob = willr_ob
        self.macd_fast = macd_fast
        self.macd_slow = macd_slow
        self.macd_signal = macd_signal
        self.cci_period = cci_period
        self.cci_os = cci_os
        self.cci_ob = cci_ob
        self.ema_fast = ema_fast
        self.ema_mid = ema_mid
        self.ema_slow = ema_slow
        self.atr_lookback = atr_lookback
        self.atr_low_pct = atr_low_pct
        self.atr_high_pct = atr_high_pct
        self.kc_period = kc_period
        self.kc_mult = kc_mult
        self.mfi_period = mfi_period
        self.mfi_os = mfi_os
        self.mfi_ob = mfi_ob
        self.obv_slope_period = obv_slope_period
        self.range_period = range_period
        self.hh_ll_period = hh_ll_period

        # V2.3 params
        self.use_mtf = use_mtf
        self.mtf_require = mtf_require
        self.use_sequences = use_sequences
        self.seq_bonus = seq_bonus
        self.use_quality_filter = use_quality_filter
        self.min_body_ratio = min_body_ratio
        self.min_wick_ratio = min_wick_ratio
        self.vol_on_pattern = vol_on_pattern
        self.use_vol_regime = use_vol_regime
        self.bb_width_max_pct = bb_width_max_pct
        self.atr_max_pct = atr_max_pct
        self.vol_regime_lookback = vol_regime_lookback
        self.use_tod_filter = use_tod_filter
        self.good_hours = good_hours if good_hours is not None else list(range(24))
        self.use_prev_candle = use_prev_candle
        self.prev_candle_same_dir = prev_candle_same_dir

        # V2.4 params
        self.use_trailing_stop = use_trailing_stop
        self.trail_activation_atr = trail_activation_atr
        self.trail_distance_atr = trail_distance_atr
        self.use_score_sizing = use_score_sizing
        self.score_size_tiers = score_size_tiers if score_size_tiers is not None else [(2, 1.0), (4, 1.5), (6, 2.0)]
        self.use_correlation_guard = use_correlation_guard
        self.max_same_direction = max_same_direction
        self.use_partial_tp = use_partial_tp
        self.partial_tp_atr = partial_tp_atr
        self.partial_tp_pct = partial_tp_pct

        self._indicators = {}
        self._last_data_id = None
        self._bars_in_trade = 0
        self._last_exit_bar = -100

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
        ind = self._indicators

        # ATR for stops/targets
        ind["atr"] = talib.ATR(h, l, c, timeperiod=14)
        ind["adx"] = talib.ADX(h, l, c, timeperiod=14)

        # Candle patterns on hourly
        ind["patterns"] = {}
        for pat_name in self.patterns:
            func = getattr(talib, pat_name)
            ind["patterns"][pat_name] = func(o, h, l, c)

        # Store raw OHLCV for quality filters
        ind["open"] = o
        ind["high"] = h
        ind["low"] = l
        ind["close"] = c
        ind["volume"] = v

        # === Multi-timeframe patterns ===
        if self.use_mtf:
            self._compute_mtf_patterns(data)

        # === Volatility regime ===
        if self.use_vol_regime:
            bb_u, bb_m, bb_l = talib.BBANDS(c, timeperiod=self.bb_period,
                                              nbdevup=self.bb_std, nbdevdn=self.bb_std)
            ind["bb_width"] = (bb_u - bb_l) / bb_m  # Normalized BB width

        # === Standard V2.2 indicators ===
        if self.use_rsi:
            ind["rsi"] = talib.RSI(c, timeperiod=self.rsi_period)

        if self.use_stoch_rsi:
            rsi_vals = talib.RSI(c, timeperiod=self.stoch_rsi_period)
            ind["stoch_rsi_k"], ind["stoch_rsi_d"] = talib.STOCH(
                rsi_vals, rsi_vals, rsi_vals,
                fastk_period=self.stoch_rsi_period,
                slowk_period=3, slowk_matype=0,
                slowd_period=3, slowd_matype=0,
            )

        if self.use_williams_r:
            ind["willr"] = talib.WILLR(h, l, c, timeperiod=self.willr_period)

        if self.use_macd:
            ind["macd"], ind["macd_signal"], ind["macd_hist"] = talib.MACD(
                c, fastperiod=self.macd_fast, slowperiod=self.macd_slow,
                signalperiod=self.macd_signal,
            )

        if self.use_cci:
            ind["cci"] = talib.CCI(h, l, c, timeperiod=self.cci_period)

        if self.use_ema_alignment:
            ind["ema_fast"] = talib.EMA(c, timeperiod=self.ema_fast)
            ind["ema_mid"] = talib.EMA(c, timeperiod=self.ema_mid)
            ind["ema_slow"] = talib.EMA(c, timeperiod=self.ema_slow)

        if self.use_bb:
            ind["bb_upper"], ind["bb_mid"], ind["bb_lower"] = talib.BBANDS(
                c, timeperiod=self.bb_period, nbdevup=self.bb_std, nbdevdn=self.bb_std,
            )

        if self.use_atr_percentile:
            ind["atr_raw"] = talib.ATR(h, l, c, timeperiod=14)

        if self.use_keltner:
            ind["kc_mid"] = talib.EMA(c, timeperiod=self.kc_period)
            kc_atr = talib.ATR(h, l, c, timeperiod=self.kc_period)
            ind["kc_upper"] = ind["kc_mid"] + self.kc_mult * kc_atr
            ind["kc_lower"] = ind["kc_mid"] - self.kc_mult * kc_atr

        if self.use_volume:
            ind["vol_sma"] = talib.SMA(v, timeperiod=self.vol_period)

        if self.use_mfi:
            ind["mfi"] = talib.MFI(h, l, c, v, timeperiod=self.mfi_period)

        if self.use_obv_slope:
            obv = talib.OBV(c, v)
            ind["obv"] = obv
            ind["obv_sma"] = talib.SMA(obv, timeperiod=self.obv_slope_period)

        if self.use_range_position:
            ind["range_high"] = pd.Series(h).rolling(self.range_period).max().values
            ind["range_low"] = pd.Series(l).rolling(self.range_period).min().values

        if self.use_hh_ll:
            ind["rolling_high"] = pd.Series(h).rolling(self.hh_ll_period).max().values
            ind["rolling_low"] = pd.Series(l).rolling(self.hh_ll_period).min().values
            ind["prev_high"] = pd.Series(h).shift(self.hh_ll_period).rolling(self.hh_ll_period).max().values
            ind["prev_low"] = pd.Series(l).shift(self.hh_ll_period).rolling(self.hh_ll_period).min().values

    def _compute_mtf_patterns(self, data):
        """Compute candle patterns on 4H and daily timeframes."""
        ind = self._indicators

        # Resample to 4H
        df_4h = data.resample("4h").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum"
        }).dropna()

        # Resample to daily
        df_d = data.resample("1D").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum"
        }).dropna()

        # Run ALL patterns on higher TFs (broader net for alignment)
        mtf_patterns_to_check = ALL_CDL_PATTERNS

        ind["mtf_4h"] = {}
        if len(df_4h) > 10:
            o4 = df_4h["open"].values.astype(float)
            h4 = df_4h["high"].values.astype(float)
            l4 = df_4h["low"].values.astype(float)
            c4 = df_4h["close"].values.astype(float)
            for pat in mtf_patterns_to_check:
                try:
                    func = getattr(talib, pat)
                    vals = func(o4, h4, l4, c4)
                    # Map 4H index back to hourly index
                    mapped = pd.Series(vals, index=df_4h.index).reindex(data.index, method="ffill")
                    ind["mtf_4h"][pat] = mapped.values
                except Exception:
                    pass

        ind["mtf_daily"] = {}
        if len(df_d) > 10:
            od = df_d["open"].values.astype(float)
            hd = df_d["high"].values.astype(float)
            ld = df_d["low"].values.astype(float)
            cd = df_d["close"].values.astype(float)
            for pat in mtf_patterns_to_check:
                try:
                    func = getattr(talib, pat)
                    vals = func(od, hd, ld, cd)
                    mapped = pd.Series(vals, index=df_d.index).reindex(data.index, method="ffill")
                    ind["mtf_daily"][pat] = mapped.values
                except Exception:
                    pass

    def _check_mtf_alignment(self, i, direction):
        """Check if higher timeframe patterns align with direction."""
        ind = self._indicators
        sign = 1 if direction == "LONG" else -1

        has_4h = False
        for pat, vals in ind.get("mtf_4h", {}).items():
            if i < len(vals) and not np.isnan(vals[i]):
                if vals[i] * sign > 0:
                    has_4h = True
                    break

        has_daily = False
        for pat, vals in ind.get("mtf_daily", {}).items():
            if i < len(vals) and not np.isnan(vals[i]):
                if vals[i] * sign > 0:
                    has_daily = True
                    break

        if self.mtf_require == "both":
            return has_4h and has_daily
        else:  # "any"
            return has_4h or has_daily

    def _check_sequence(self, i, direction):
        """Check for bullish/bearish candle sequences (2-3 bar patterns)."""
        ind = self._indicators
        if i < 3:
            return 0.0

        o = ind["open"]
        h = ind["high"]
        l = ind["low"]
        c = ind["close"]

        bonus = 0.0

        # Current candle properties
        body_0 = c[i] - o[i]
        range_0 = h[i] - l[i] if h[i] > l[i] else 0.001

        # Previous candles
        body_1 = c[i-1] - o[i-1]
        range_1 = h[i-1] - l[i-1] if h[i-1] > l[i-1] else 0.001
        body_2 = c[i-2] - o[i-2]

        if direction == "LONG":
            # Doji → bullish marubozu = indecision → conviction
            if abs(body_1) / range_1 < 0.1 and body_0 / range_0 > 0.6:
                bonus += 0.5

            # 3 declining red candles → bullish = exhaustion reversal
            if body_2 < 0 and body_1 < 0 and body_1 < body_2 and body_0 > 0:
                bonus += 0.5

            # Inside bar → breakout (prev range contained in bar before)
            if h[i-1] <= h[i-2] and l[i-1] >= l[i-2] and c[i] > h[i-1]:
                bonus += 0.5

        elif direction == "SHORT":
            # Doji → bearish marubozu
            if abs(body_1) / range_1 < 0.1 and body_0 / range_0 < -0.6:
                bonus += 0.5

            # 3 rising green candles → bearish = exhaustion
            if body_2 > 0 and body_1 > 0 and body_1 > body_2 and body_0 < 0:
                bonus += 0.5

            # Inside bar → breakdown
            if h[i-1] <= h[i-2] and l[i-1] >= l[i-2] and c[i] < l[i-1]:
                bonus += 0.5

        return min(bonus, self.seq_bonus)  # Cap at seq_bonus

    def _check_quality(self, i, direction):
        """Check candle quality: body/wick ratios and volume."""
        ind = self._indicators
        o = ind["open"][i]
        h = ind["high"][i]
        l = ind["low"][i]
        c = ind["close"][i]
        v = ind["volume"][i]

        total_range = h - l
        if total_range <= 0:
            return False

        body = abs(c - o)
        body_ratio = body / total_range

        # Body ratio filter
        if self.min_body_ratio > 0 and body_ratio < self.min_body_ratio:
            return False

        # Wick rejection filter (for reversal candles)
        if self.min_wick_ratio > 0 and body > 0:
            if direction == "LONG":
                lower_wick = min(o, c) - l
                if lower_wick / body < self.min_wick_ratio:
                    return False
            elif direction == "SHORT":
                upper_wick = h - max(o, c)
                if upper_wick / body < self.min_wick_ratio:
                    return False

        # Volume on pattern candle
        if self.vol_on_pattern > 1.0:
            vol_sma = ind.get("vol_sma")
            if vol_sma is not None and not np.isnan(vol_sma[i]) and vol_sma[i] > 0:
                if v / vol_sma[i] < self.vol_on_pattern:
                    return False

        return True

    def _check_vol_regime(self, i):
        """Check if current volatility is in acceptable regime."""
        ind = self._indicators

        if "bb_width" in ind:
            bw = ind["bb_width"]
            if i >= self.vol_regime_lookback and not np.isnan(bw[i]):
                window = bw[max(0, i - self.vol_regime_lookback):i]
                window = window[~np.isnan(window)]
                if len(window) > 10:
                    pct = np.sum(window <= bw[i]) / len(window) * 100
                    if pct > self.bb_width_max_pct:
                        return False

        if self.atr_max_pct < 100:
            atr_arr = ind["atr"]
            if i >= self.vol_regime_lookback and not np.isnan(atr_arr[i]):
                window = atr_arr[max(0, i - self.vol_regime_lookback):i]
                window = window[~np.isnan(window)]
                if len(window) > 10:
                    pct = np.sum(window <= atr_arr[i]) / len(window) * 100
                    if pct > self.atr_max_pct:
                        return False

        return True

    def _check_prev_candle(self, i, direction):
        """Check if previous candle supports direction."""
        if i < 1:
            return False
        ind = self._indicators
        body_prev = ind["close"][i-1] - ind["open"][i-1]
        if self.prev_candle_same_dir:
            # Require prev candle same direction (momentum)
            if direction == "LONG" and body_prev <= 0:
                return False
            if direction == "SHORT" and body_prev >= 0:
                return False
        else:
            # Require prev candle OPPOSITE direction (reversal setup)
            if direction == "LONG" and body_prev > 0:
                return False
            if direction == "SHORT" and body_prev < 0:
                return False
        return True

    def _score_setup(self, data, i, direction):
        """Score the quality of the setup (same as V2.2 + V2.3 bonuses)."""
        score = 0.0
        ind = self._indicators
        close = float(data.iloc[i]["close"])
        vol = float(data.iloc[i]["volume"])

        # === RSI ===
        if self.use_rsi:
            rsi = ind["rsi"][i]
            if not np.isnan(rsi):
                if direction == "LONG" and rsi < self.rsi_oversold:
                    score += 1.0
                elif direction == "SHORT" and rsi > self.rsi_overbought:
                    score += 1.0

        # === Stochastic RSI ===
        if self.use_stoch_rsi:
            sk = ind["stoch_rsi_k"][i]
            if not np.isnan(sk):
                if direction == "LONG" and sk < self.stoch_rsi_os:
                    score += 1.0
                elif direction == "SHORT" and sk > self.stoch_rsi_ob:
                    score += 1.0

        # === Williams %R ===
        if self.use_williams_r:
            wr = ind["willr"][i]
            if not np.isnan(wr):
                if direction == "LONG" and wr < self.willr_os:
                    score += 1.0
                elif direction == "SHORT" and wr > self.willr_ob:
                    score += 1.0

        # === MACD ===
        if self.use_macd:
            macd_hist = ind["macd_hist"][i]
            macd_hist_prev = ind["macd_hist"][i - 1] if i > 0 else np.nan
            if not np.isnan(macd_hist) and not np.isnan(macd_hist_prev):
                if direction == "LONG" and macd_hist > macd_hist_prev and macd_hist < 0:
                    score += 1.0
                elif direction == "SHORT" and macd_hist < macd_hist_prev and macd_hist > 0:
                    score += 1.0

        # === CCI ===
        if self.use_cci:
            cci = ind["cci"][i]
            if not np.isnan(cci):
                if direction == "LONG" and cci < self.cci_os:
                    score += 1.0
                elif direction == "SHORT" and cci > self.cci_ob:
                    score += 1.0

        # === EMA Alignment ===
        if self.use_ema_alignment:
            ef = ind["ema_fast"][i]
            em = ind["ema_mid"][i]
            es = ind["ema_slow"][i]
            if not any(np.isnan(x) for x in [ef, em, es]):
                if direction == "LONG" and ef > em > es:
                    score += 1.0
                elif direction == "SHORT" and ef < em < es:
                    score += 1.0

        # === ADX ===
        if self.use_adx:
            adx = ind["adx"][i]
            if not np.isnan(adx) and adx < 25:
                score += 0.5

        # === Bollinger Bands ===
        if self.use_bb:
            bb_u = ind["bb_upper"][i]
            bb_l = ind["bb_lower"][i]
            if not np.isnan(bb_u) and not np.isnan(bb_l):
                if direction == "LONG" and close <= bb_l * 1.01:
                    score += 1.0
                elif direction == "SHORT" and close >= bb_u * 0.99:
                    score += 1.0

        # === ATR Percentile ===
        if self.use_atr_percentile:
            atr_arr = ind["atr_raw"]
            if i >= self.atr_lookback:
                window = atr_arr[i - self.atr_lookback:i]
                window = window[~np.isnan(window)]
                if len(window) > 10:
                    pct = np.percentile(window, [self.atr_low_pct, self.atr_high_pct])
                    cur_atr = atr_arr[i]
                    if not np.isnan(cur_atr) and cur_atr < pct[0]:
                        score += 0.5

        # === Keltner Channels ===
        if self.use_keltner:
            kc_u = ind["kc_upper"][i]
            kc_l = ind["kc_lower"][i]
            if not np.isnan(kc_u) and not np.isnan(kc_l):
                if direction == "LONG" and close <= kc_l:
                    score += 1.0
                elif direction == "SHORT" and close >= kc_u:
                    score += 1.0

        # === Volume ===
        if self.use_volume:
            vol_avg = ind["vol_sma"][i]
            if not np.isnan(vol_avg) and vol_avg > 0 and vol > vol_avg * self.vol_threshold:
                score += 1.0

        # === MFI ===
        if self.use_mfi:
            mfi = ind["mfi"][i]
            if not np.isnan(mfi):
                if direction == "LONG" and mfi < self.mfi_os:
                    score += 1.0
                elif direction == "SHORT" and mfi > self.mfi_ob:
                    score += 1.0

        # === OBV Slope ===
        if self.use_obv_slope:
            obv_now = ind["obv"][i]
            obv_sma = ind["obv_sma"][i]
            if not np.isnan(obv_now) and not np.isnan(obv_sma):
                if direction == "LONG" and obv_now > obv_sma:
                    score += 0.5
                elif direction == "SHORT" and obv_now < obv_sma:
                    score += 0.5

        # === Range Position ===
        if self.use_range_position:
            rh = ind["range_high"][i]
            rl = ind["range_low"][i]
            if not np.isnan(rh) and not np.isnan(rl) and rh > rl:
                pos = (close - rl) / (rh - rl)
                if direction == "LONG" and pos < 0.2:
                    score += 1.0
                elif direction == "SHORT" and pos > 0.8:
                    score += 1.0

        # === HH/LL ===
        if self.use_hh_ll:
            rh = ind["rolling_high"][i]
            rl = ind["rolling_low"][i]
            ph = ind["prev_high"][i]
            pl = ind["prev_low"][i]
            if not any(np.isnan(x) for x in [rh, rl, ph, pl]):
                if direction == "LONG" and rl > pl:
                    score += 0.5
                elif direction == "SHORT" and rh < ph:
                    score += 0.5

        # === V2.3: Sequence bonus ===
        if self.use_sequences:
            seq_score = self._check_sequence(i, direction)
            score += seq_score

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
        ind = self._indicators

        # ADX trend filter
        adx = ind["adx"][i]
        if not np.isnan(adx) and adx > self.adx_max:
            return None

        # Time-of-day filter
        if self.use_tod_filter:
            hour = data.index[i].hour
            if hour not in self.good_hours:
                return None

        # Volatility regime filter
        if self.use_vol_regime:
            if not self._check_vol_regime(i):
                return None

        # Check all enabled patterns
        best_signal = None
        best_score = -1
        best_pattern = None

        for pat_name in self.patterns:
            val = ind["patterns"][pat_name][i]
            if val == 0:
                continue

            direction = "LONG" if val > 0 else "SHORT"

            # Direction filter
            if self.direction_filter == "long_only" and direction == "SHORT":
                continue
            if self.direction_filter == "short_only" and direction == "LONG":
                continue

            # Quality filter
            if self.use_quality_filter:
                if not self._check_quality(i, direction):
                    continue

            # Multi-timeframe alignment
            if self.use_mtf:
                if not self._check_mtf_alignment(i, direction):
                    continue

            # Previous candle check
            if self.use_prev_candle:
                if not self._check_prev_candle(i, direction):
                    continue

            score = self._score_setup(data, i, direction)

            if self.hard_filter:
                enabled_count = sum(1 for x in [
                    self.use_rsi, self.use_stoch_rsi, self.use_williams_r,
                    self.use_macd, self.use_cci, self.use_ema_alignment,
                    self.use_bb, self.use_volume, self.use_mfi,
                    self.use_range_position,
                ] if x)
                if score < enabled_count * 0.8:
                    continue
            elif score < self.min_score:
                continue

            if score > best_score:
                best_score = score
                best_signal = direction
                best_pattern = pat_name

        if best_signal is None:
            return None

        if best_signal == "LONG":
            stop = price - atr * self.stop_atr
            target = price + atr * self.target_atr
        else:
            stop = price + atr * self.stop_atr
            target = price - atr * self.target_atr

        lev = self.base_leverage
        if best_score >= 4:
            lev *= 1.3
        elif best_score >= 3:
            lev *= 1.15

        self._bars_in_trade = 0

        sig = {
            "action": best_signal,
            "stop": stop,
            "target": target,
            "signal": f"{best_pattern}|s{best_score:.1f}",
            "leverage": lev,
            "market_regime": "CANDLE_V2_3",
            "rsi_at_entry": float(ind.get("rsi", np.array([np.nan]))[min(i, len(ind.get("rsi", [0])) - 1)]) if self.use_rsi and "rsi" in ind else None,
            "atr_at_entry": float(atr),
            "score": best_score,
        }

        # V2.4: Score-based position sizing
        if self.use_score_sizing:
            multiplier = 1.0
            for min_s, mult in sorted(self.score_size_tiers):
                if best_score >= min_s:
                    multiplier = mult
            sig["size_multiplier"] = multiplier

        return sig

    def check_exit(self, data, i, open_trade):
        self._compute_indicators(data, i)
        self._bars_in_trade += 1

        atr = self._indicators["atr"][i]
        high = float(data.iloc[i]["high"])
        low = float(data.iloc[i]["low"])

        # V2.4: Trailing stop — update stop_price for next bar's engine check
        if self.use_trailing_stop and not np.isnan(atr) and atr > 0:
            if not hasattr(open_trade, '_best_price'):
                open_trade._best_price = None

            if open_trade.direction == "LONG":
                if open_trade._best_price is None:
                    open_trade._best_price = high
                open_trade._best_price = max(open_trade._best_price, high)
                profit_atr = (open_trade._best_price - open_trade.entry_price) / atr
                if profit_atr >= self.trail_activation_atr:
                    new_stop = open_trade._best_price - self.trail_distance_atr * atr
                    if new_stop > open_trade.stop_price:
                        open_trade.stop_price = new_stop
            else:
                if open_trade._best_price is None:
                    open_trade._best_price = low
                open_trade._best_price = min(open_trade._best_price, low)
                profit_atr = (open_trade.entry_price - open_trade._best_price) / atr
                if profit_atr >= self.trail_activation_atr:
                    new_stop = open_trade._best_price + self.trail_distance_atr * atr
                    if new_stop < open_trade.stop_price:
                        open_trade.stop_price = new_stop

        # V2.4: Partial profit taking
        if self.use_partial_tp:
            entry_atr = getattr(open_trade, 'atr_at_entry', None)
            if entry_atr and entry_atr > 0:
                if not hasattr(open_trade, '_partial_taken'):
                    open_trade._partial_taken = False

                if not open_trade._partial_taken:
                    if open_trade.direction == "LONG":
                        tp_price = open_trade.entry_price + self.partial_tp_atr * entry_atr
                        triggered = high >= tp_price
                    else:
                        tp_price = open_trade.entry_price - self.partial_tp_atr * entry_atr
                        triggered = low <= tp_price

                    if triggered:
                        open_trade._partial_taken = True
                        partial_size = open_trade.size_usd * self.partial_tp_pct
                        open_trade.size_usd -= partial_size
                        return {
                            "action": "PARTIAL_TP",
                            "partial_size": partial_size,
                            "partial_price": tp_price,
                        }

        # Time exit
        if self._bars_in_trade >= self.time_exit_bars:
            self._last_exit_bar = i
            return "TIME_EXIT"

        return None
