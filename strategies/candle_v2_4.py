"""
Candle V2.4 — Push WR as high as physically possible. 90%+ target.

Based on V2.3 (85.2% avg WR). New features:
  1. 4H primary timeframe (patterns on 4H, confirm with daily)
  2. Consecutive timeframe agreement (prev bar too)
  3. Extreme R:R options (up to 10:1 stop:target)
  4. Volume spike requirement on pattern candle
  5. MARUBOZU-only mode
  6. Long/short direction tracking
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
    """V2.4: Maximum WR optimization. 90%+ target."""

    def __init__(self,
                 # Pattern config
                 pattern_set="top5",  # "top5", "marubozu", "marubozu_plus", "all"
                 # Indicator toggles (V2.2 baseline)
                 use_rsi=True,
                 use_stoch_rsi=True,
                 use_williams_r=True,
                 use_macd=True,
                 use_cci=True,
                 use_ema_alignment=True,
                 use_adx=True,
                 use_bb=True,
                 use_atr_percentile=True,
                 use_keltner=True,
                 use_volume=True,
                 use_mfi=True,
                 use_obv_slope=True,
                 use_range_position=True,
                 use_hh_ll=True,
                 # Entry params
                 min_score=3.0,
                 stop_atr=5.0,
                 target_atr=2.0,
                 time_exit_bars=144,
                 cooldown=12,
                 base_leverage=2.0,
                 adx_max=25,
                 direction_filter="both",
                 # Indicator params
                 rsi_period=14, rsi_oversold=30, rsi_overbought=70,
                 bb_period=20, bb_std=2.0,
                 vol_period=20, vol_threshold=1.2,
                 stoch_rsi_period=14, stoch_rsi_os=20, stoch_rsi_ob=80,
                 willr_period=14, willr_os=-80, willr_ob=-20,
                 macd_fast=12, macd_slow=26, macd_signal=9,
                 cci_period=20, cci_os=-100, cci_ob=100,
                 ema_fast=8, ema_mid=21, ema_slow=50,
                 atr_lookback=100, atr_low_pct=25, atr_high_pct=75,
                 kc_period=20, kc_mult=1.5,
                 mfi_period=14, mfi_os=20, mfi_ob=80,
                 obv_slope_period=10,
                 range_period=50,
                 hh_ll_period=20,
                 # === V2.3 MTF ===
                 use_mtf=True,
                 mtf_require="both",
                 # === V2.4 NEW ===
                 # 4H primary timeframe
                 primary_tf="1h",  # "1h" or "4h"
                 # Consecutive TF agreement
                 use_consecutive=False,
                 consecutive_bars=2,  # How many consecutive 4H/daily bars must agree
                 # Volume spike on pattern candle
                 vol_spike_mult=1.0,  # 1.0=disabled, 2.0=require 2x avg volume
                 # Quality filter
                 use_quality_filter=False,
                 min_body_ratio=0.0,
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

        self.direction_filter = direction_filter
        self.min_score = min_score
        self.stop_atr = stop_atr
        self.target_atr = target_atr
        self.time_exit_bars = time_exit_bars
        self.cooldown = cooldown
        self.base_leverage = base_leverage
        self.adx_max = adx_max

        # Indicator toggles
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

        # Indicator params
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

        # V2.3 MTF
        self.use_mtf = use_mtf
        self.mtf_require = mtf_require

        # V2.4 new
        self.primary_tf = primary_tf
        self.use_consecutive = use_consecutive
        self.consecutive_bars = consecutive_bars
        self.vol_spike_mult = vol_spike_mult
        self.use_quality_filter = use_quality_filter
        self.min_body_ratio = min_body_ratio

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

        ind["atr"] = talib.ATR(h, l, c, timeperiod=14)
        ind["adx"] = talib.ADX(h, l, c, timeperiod=14)
        ind["open"] = o
        ind["high"] = h
        ind["low"] = l
        ind["close"] = c
        ind["volume"] = v

        # === Primary TF patterns ===
        if self.primary_tf == "4h":
            self._compute_4h_primary(data)
        else:
            # Hourly patterns (original behavior)
            ind["patterns"] = {}
            for pat_name in self.patterns:
                func = getattr(talib, pat_name)
                ind["patterns"][pat_name] = func(o, h, l, c)

        # === MTF patterns (for confirmation) ===
        if self.use_mtf:
            self._compute_mtf_patterns(data)

        # === Volume SMA for spike detection ===
        ind["vol_sma"] = talib.SMA(v, timeperiod=self.vol_period)

        # === Standard indicators ===
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

    def _compute_4h_primary(self, data):
        """Use 4H candles as primary pattern source, mapped back to hourly."""
        ind = self._indicators

        df_4h = data.resample("4h").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum"
        }).dropna()

        ind["patterns"] = {}
        if len(df_4h) > 10:
            o4 = df_4h["open"].values.astype(float)
            h4 = df_4h["high"].values.astype(float)
            l4 = df_4h["low"].values.astype(float)
            c4 = df_4h["close"].values.astype(float)
            for pat_name in self.patterns:
                try:
                    func = getattr(talib, pat_name)
                    vals = func(o4, h4, l4, c4)
                    mapped = pd.Series(vals, index=df_4h.index).reindex(data.index, method="ffill")
                    ind["patterns"][pat_name] = mapped.values
                except Exception:
                    ind["patterns"][pat_name] = np.zeros(len(data))
        else:
            for pat_name in self.patterns:
                ind["patterns"][pat_name] = np.zeros(len(data))

    def _compute_mtf_patterns(self, data):
        """Compute candle patterns on 4H and daily timeframes."""
        ind = self._indicators

        # For 4H primary, we only need daily confirmation
        # For 1H primary, we need both 4H and daily
        mtf_patterns_to_check = ALL_CDL_PATTERNS

        if self.primary_tf != "4h":
            # Resample to 4H
            df_4h = data.resample("4h").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum"
            }).dropna()

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
                        mapped = pd.Series(vals, index=df_4h.index).reindex(data.index, method="ffill")
                        ind["mtf_4h"][pat] = mapped.values
                    except Exception:
                        pass

                # Consecutive: store previous bar values too
                if self.use_consecutive:
                    ind["mtf_4h_prev"] = {}
                    for pat in mtf_patterns_to_check:
                        try:
                            func = getattr(talib, pat)
                            vals = func(o4, h4, l4, c4)
                            # Shift by 1 in 4H space then forward-fill
                            shifted = pd.Series(vals, index=df_4h.index).shift(1)
                            mapped = shifted.reindex(data.index, method="ffill")
                            ind["mtf_4h_prev"][pat] = mapped.values
                        except Exception:
                            pass

        # Resample to daily
        df_d = data.resample("1D").agg({
            "open": "first", "high": "max", "low": "min",
            "close": "last", "volume": "sum"
        }).dropna()

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

            if self.use_consecutive:
                ind["mtf_daily_prev"] = {}
                for pat in mtf_patterns_to_check:
                    try:
                        func = getattr(talib, pat)
                        vals = func(od, hd, ld, cd)
                        shifted = pd.Series(vals, index=df_d.index).shift(1)
                        mapped = shifted.reindex(data.index, method="ffill")
                        ind["mtf_daily_prev"][pat] = mapped.values
                    except Exception:
                        pass

    def _check_mtf_alignment(self, i, direction):
        """Check if higher timeframe patterns align with direction."""
        ind = self._indicators
        sign = 1 if direction == "LONG" else -1

        if self.primary_tf == "4h":
            # 4H is primary, only need daily confirmation
            has_daily = False
            for pat, vals in ind.get("mtf_daily", {}).items():
                if i < len(vals) and not np.isnan(vals[i]):
                    if vals[i] * sign > 0:
                        has_daily = True
                        break
            if not has_daily:
                return False

            # Consecutive daily check
            if self.use_consecutive:
                has_daily_prev = False
                for pat, vals in ind.get("mtf_daily_prev", {}).items():
                    if i < len(vals) and not np.isnan(vals[i]):
                        if vals[i] * sign > 0:
                            has_daily_prev = True
                            break
                if not has_daily_prev:
                    return False

            return True

        # 1H primary: need 4H and/or daily
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
            if not (has_4h and has_daily):
                return False
        else:
            if not (has_4h or has_daily):
                return False

        # Consecutive check for 1H primary
        if self.use_consecutive:
            if has_4h:
                has_4h_prev = False
                for pat, vals in ind.get("mtf_4h_prev", {}).items():
                    if i < len(vals) and not np.isnan(vals[i]):
                        if vals[i] * sign > 0:
                            has_4h_prev = True
                            break
                if not has_4h_prev:
                    return False

            if has_daily:
                has_daily_prev = False
                for pat, vals in ind.get("mtf_daily_prev", {}).items():
                    if i < len(vals) and not np.isnan(vals[i]):
                        if vals[i] * sign > 0:
                            has_daily_prev = True
                            break
                if not has_daily_prev:
                    return False

        return True

    def _score_setup(self, data, i, direction):
        """Score the quality of the setup."""
        score = 0.0
        ind = self._indicators
        close = float(data.iloc[i]["close"])
        vol = float(data.iloc[i]["volume"])

        if self.use_rsi:
            rsi = ind["rsi"][i]
            if not np.isnan(rsi):
                if direction == "LONG" and rsi < self.rsi_oversold:
                    score += 1.0
                elif direction == "SHORT" and rsi > self.rsi_overbought:
                    score += 1.0

        if self.use_stoch_rsi:
            sk = ind["stoch_rsi_k"][i]
            if not np.isnan(sk):
                if direction == "LONG" and sk < self.stoch_rsi_os:
                    score += 1.0
                elif direction == "SHORT" and sk > self.stoch_rsi_ob:
                    score += 1.0

        if self.use_williams_r:
            wr = ind["willr"][i]
            if not np.isnan(wr):
                if direction == "LONG" and wr < self.willr_os:
                    score += 1.0
                elif direction == "SHORT" and wr > self.willr_ob:
                    score += 1.0

        if self.use_macd:
            macd_hist = ind["macd_hist"][i]
            macd_hist_prev = ind["macd_hist"][i - 1] if i > 0 else np.nan
            if not np.isnan(macd_hist) and not np.isnan(macd_hist_prev):
                if direction == "LONG" and macd_hist > macd_hist_prev and macd_hist < 0:
                    score += 1.0
                elif direction == "SHORT" and macd_hist < macd_hist_prev and macd_hist > 0:
                    score += 1.0

        if self.use_cci:
            cci = ind["cci"][i]
            if not np.isnan(cci):
                if direction == "LONG" and cci < self.cci_os:
                    score += 1.0
                elif direction == "SHORT" and cci > self.cci_ob:
                    score += 1.0

        if self.use_ema_alignment:
            ef = ind["ema_fast"][i]
            em = ind["ema_mid"][i]
            es = ind["ema_slow"][i]
            if not any(np.isnan(x) for x in [ef, em, es]):
                if direction == "LONG" and ef > em > es:
                    score += 1.0
                elif direction == "SHORT" and ef < em < es:
                    score += 1.0

        if self.use_adx:
            adx = ind["adx"][i]
            if not np.isnan(adx) and adx < 25:
                score += 0.5

        if self.use_bb:
            bb_u = ind["bb_upper"][i]
            bb_l = ind["bb_lower"][i]
            if not np.isnan(bb_u) and not np.isnan(bb_l):
                if direction == "LONG" and close <= bb_l * 1.01:
                    score += 1.0
                elif direction == "SHORT" and close >= bb_u * 0.99:
                    score += 1.0

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

        if self.use_keltner:
            kc_u = ind["kc_upper"][i]
            kc_l = ind["kc_lower"][i]
            if not np.isnan(kc_u) and not np.isnan(kc_l):
                if direction == "LONG" and close <= kc_l:
                    score += 1.0
                elif direction == "SHORT" and close >= kc_u:
                    score += 1.0

        if self.use_volume:
            vol_avg = ind["vol_sma"][i]
            if not np.isnan(vol_avg) and vol_avg > 0 and vol > vol_avg * self.vol_threshold:
                score += 1.0

        if self.use_mfi:
            mfi = ind["mfi"][i]
            if not np.isnan(mfi):
                if direction == "LONG" and mfi < self.mfi_os:
                    score += 1.0
                elif direction == "SHORT" and mfi > self.mfi_ob:
                    score += 1.0

        if self.use_obv_slope:
            obv_now = ind["obv"][i]
            obv_sma = ind["obv_sma"][i]
            if not np.isnan(obv_now) and not np.isnan(obv_sma):
                if direction == "LONG" and obv_now > obv_sma:
                    score += 0.5
                elif direction == "SHORT" and obv_now < obv_sma:
                    score += 0.5

        if self.use_range_position:
            rh = ind["range_high"][i]
            rl = ind["range_low"][i]
            if not np.isnan(rh) and not np.isnan(rl) and rh > rl:
                pos = (close - rl) / (rh - rl)
                if direction == "LONG" and pos < 0.2:
                    score += 1.0
                elif direction == "SHORT" and pos > 0.8:
                    score += 1.0

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

        # Check all enabled patterns
        best_signal = None
        best_score = -1
        best_pattern = None

        for pat_name in self.patterns:
            if pat_name not in ind["patterns"]:
                continue
            vals = ind["patterns"][pat_name]
            if i >= len(vals):
                continue
            val = vals[i]
            if val == 0 or np.isnan(val):
                continue

            direction = "LONG" if val > 0 else "SHORT"

            # Direction filter
            if self.direction_filter == "long_only" and direction == "SHORT":
                continue
            if self.direction_filter == "short_only" and direction == "LONG":
                continue

            # Volume spike check
            if self.vol_spike_mult > 1.0:
                v = ind["volume"][i]
                vol_avg = ind["vol_sma"][i]
                if not np.isnan(vol_avg) and vol_avg > 0:
                    if v / vol_avg < self.vol_spike_mult:
                        continue

            # Quality filter
            if self.use_quality_filter and self.min_body_ratio > 0:
                o_i = ind["open"][i]
                h_i = ind["high"][i]
                l_i = ind["low"][i]
                c_i = ind["close"][i]
                total_range = h_i - l_i
                if total_range > 0:
                    body_ratio = abs(c_i - o_i) / total_range
                    if body_ratio < self.min_body_ratio:
                        continue

            # Multi-timeframe alignment
            if self.use_mtf:
                if not self._check_mtf_alignment(i, direction):
                    continue

            score = self._score_setup(data, i, direction)
            if score < self.min_score:
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
        self._bars_in_trade = 0

        return {
            "action": best_signal,
            "stop": stop,
            "target": target,
            "signal": f"{best_pattern}|s{best_score:.1f}",
            "leverage": lev,
            "market_regime": "CANDLE_V2_4",
            "rsi_at_entry": float(ind.get("rsi", np.array([np.nan]))[min(i, len(ind.get("rsi", [0])) - 1)]) if self.use_rsi and "rsi" in ind else None,
            "atr_at_entry": float(atr),
        }

    def check_exit(self, data, i, open_trade):
        self._compute_indicators(data, i)
        self._bars_in_trade += 1

        if self._bars_in_trade >= self.time_exit_bars:
            self._last_exit_bar = i
            return "TIME_EXIT"

        return None
