"""
Candle V2.2 — Multi-Indicator Confirmation Strategy.

Goal: Push WR above 51% with GENERIC params (same for all assets).
Architecture:
  1. Entry trigger: candle pattern fires (top patterns from V2.1)
  2. Score: each confirmation indicator adds points
  3. Threshold: only trade when score >= min_score
  4. Exit: stop/target (ATR-based) or time exit

V2.2.1: Added hard filter mode — require specific indicator confluence
V2.2.2: Asymmetric R:R with directional bias
"""

import numpy as np
import pandas as pd
import talib


# Top patterns from V2.1 (>45% WR with filtering)
TOP_PATTERNS = {
    "CDLMARUBOZU",          # 48.9% WR — the winner
    "CDLSPINNINGTOP",       # 47.1% WR
    "CDLSHORTLINE",         # 46.0% WR
    "CDLCLOSINGMARUBOZU",   # 45.5% WR
    "CDLBELTHOLD",          # 44.4% WR
}

MARUBOZU_ONLY = {"CDLMARUBOZU"}
MARUBOZU_PLUS = {"CDLMARUBOZU", "CDLCLOSINGMARUBOZU"}


class CandleV2_2:
    """Multi-indicator confirmation strategy. ONE set of params for all assets."""

    def __init__(self,
                 # Indicator toggles (for A/B testing)
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
                 # Pattern selection
                 pattern_set="top5",  # "top5", "marubozu", "marubozu_plus"
                 # Hard filter mode: require ALL enabled indicators to confirm
                 hard_filter=False,
                 # Core params
                 min_score=2.0,
                 stop_atr=2.0,
                 target_atr=3.0,
                 time_exit_bars=36,
                 cooldown=8,
                 base_leverage=2.0,
                 adx_max=40,
                 # Directional bias (long_only, short_only, or both)
                 direction_filter="both",
                 # RSI params
                 rsi_period=14,
                 rsi_oversold=30,
                 rsi_overbought=70,
                 # BB params
                 bb_period=20,
                 bb_std=2.0,
                 # Volume params
                 vol_period=20,
                 vol_threshold=1.2,
                 # Stoch RSI params
                 stoch_rsi_period=14,
                 stoch_rsi_os=20,
                 stoch_rsi_ob=80,
                 # Williams %R
                 willr_period=14,
                 willr_os=-80,
                 willr_ob=-20,
                 # MACD
                 macd_fast=12,
                 macd_slow=26,
                 macd_signal=9,
                 # CCI
                 cci_period=20,
                 cci_os=-100,
                 cci_ob=100,
                 # EMA alignment
                 ema_fast=8,
                 ema_mid=21,
                 ema_slow=50,
                 # ATR percentile
                 atr_lookback=100,
                 atr_low_pct=25,
                 atr_high_pct=75,
                 # Keltner
                 kc_period=20,
                 kc_mult=1.5,
                 # MFI
                 mfi_period=14,
                 mfi_os=20,
                 mfi_ob=80,
                 # OBV slope
                 obv_slope_period=10,
                 # Range position
                 range_period=50,
                 # HH/LL
                 hh_ll_period=20,
                 ):
        # Pattern set
        if pattern_set == "marubozu":
            self.patterns = MARUBOZU_ONLY
        elif pattern_set == "marubozu_plus":
            self.patterns = MARUBOZU_PLUS
        else:
            self.patterns = TOP_PATTERNS

        self.hard_filter = hard_filter
        self.direction_filter = direction_filter

        # Store all params
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

        # Always need ATR for stops/targets
        ind["atr"] = talib.ATR(h, l, c, timeperiod=14)

        # Candle patterns
        ind["patterns"] = {}
        for pat_name in self.patterns:
            func = getattr(talib, pat_name)
            ind["patterns"][pat_name] = func(o, h, l, c)

        # ADX (always compute for trend filter)
        ind["adx"] = talib.ADX(h, l, c, timeperiod=14)

        # Conditional indicators
        if self.use_rsi:
            ind["rsi"] = talib.RSI(c, timeperiod=self.rsi_period)

        if self.use_stoch_rsi:
            # Stochastic RSI via fastk/fastd of RSI
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
            # Previous period highs/lows for HH/LL detection
            ind["prev_high"] = pd.Series(h).shift(self.hh_ll_period).rolling(self.hh_ll_period).max().values
            ind["prev_low"] = pd.Series(l).shift(self.hh_ll_period).rolling(self.hh_ll_period).min().values

    def _score_setup(self, data, i, direction):
        """Score the quality of the setup. Each indicator can add 0-1 points."""
        score = 0.0
        ind = self._indicators
        close = float(data.iloc[i]["close"])
        vol = float(data.iloc[i]["volume"])
        details = {}

        # === RSI ===
        if self.use_rsi:
            rsi = ind["rsi"][i]
            if not np.isnan(rsi):
                if direction == "LONG" and rsi < self.rsi_oversold:
                    score += 1.0
                    details["rsi"] = f"OS({rsi:.0f})"
                elif direction == "SHORT" and rsi > self.rsi_overbought:
                    score += 1.0
                    details["rsi"] = f"OB({rsi:.0f})"

        # === Stochastic RSI ===
        if self.use_stoch_rsi:
            sk = ind["stoch_rsi_k"][i]
            if not np.isnan(sk):
                if direction == "LONG" and sk < self.stoch_rsi_os:
                    score += 1.0
                    details["stoch_rsi"] = f"OS({sk:.0f})"
                elif direction == "SHORT" and sk > self.stoch_rsi_ob:
                    score += 1.0
                    details["stoch_rsi"] = f"OB({sk:.0f})"

        # === Williams %R ===
        if self.use_williams_r:
            wr = ind["willr"][i]
            if not np.isnan(wr):
                if direction == "LONG" and wr < self.willr_os:
                    score += 1.0
                    details["willr"] = f"OS({wr:.0f})"
                elif direction == "SHORT" and wr > self.willr_ob:
                    score += 1.0
                    details["willr"] = f"OB({wr:.0f})"

        # === MACD ===
        if self.use_macd:
            macd_hist = ind["macd_hist"][i]
            macd_hist_prev = ind["macd_hist"][i - 1] if i > 0 else np.nan
            if not np.isnan(macd_hist) and not np.isnan(macd_hist_prev):
                # MACD histogram turning (momentum shift)
                if direction == "LONG" and macd_hist > macd_hist_prev and macd_hist < 0:
                    score += 1.0
                    details["macd"] = "bull_turn"
                elif direction == "SHORT" and macd_hist < macd_hist_prev and macd_hist > 0:
                    score += 1.0
                    details["macd"] = "bear_turn"

        # === CCI ===
        if self.use_cci:
            cci = ind["cci"][i]
            if not np.isnan(cci):
                if direction == "LONG" and cci < self.cci_os:
                    score += 1.0
                    details["cci"] = f"OS({cci:.0f})"
                elif direction == "SHORT" and cci > self.cci_ob:
                    score += 1.0
                    details["cci"] = f"OB({cci:.0f})"

        # === EMA Alignment ===
        if self.use_ema_alignment:
            ef = ind["ema_fast"][i]
            em = ind["ema_mid"][i]
            es = ind["ema_slow"][i]
            if not any(np.isnan(x) for x in [ef, em, es]):
                if direction == "LONG" and ef > em > es:
                    score += 1.0
                    details["ema"] = "bull_align"
                elif direction == "SHORT" and ef < em < es:
                    score += 1.0
                    details["ema"] = "bear_align"

        # === ADX (trend strength — low ADX = mean reversion friendly) ===
        if self.use_adx:
            adx = ind["adx"][i]
            if not np.isnan(adx) and adx < 25:
                score += 0.5
                details["adx"] = f"low({adx:.0f})"

        # === Bollinger Bands ===
        if self.use_bb:
            bb_u = ind["bb_upper"][i]
            bb_l = ind["bb_lower"][i]
            if not np.isnan(bb_u) and not np.isnan(bb_l):
                if direction == "LONG" and close <= bb_l * 1.01:
                    score += 1.0
                    details["bb"] = "at_lower"
                elif direction == "SHORT" and close >= bb_u * 0.99:
                    score += 1.0
                    details["bb"] = "at_upper"

        # === ATR Percentile ===
        if self.use_atr_percentile:
            atr_arr = ind["atr_raw"]
            if i >= self.atr_lookback:
                window = atr_arr[i - self.atr_lookback:i]
                window = window[~np.isnan(window)]
                if len(window) > 10:
                    pct = np.percentile(window, [self.atr_low_pct, self.atr_high_pct])
                    cur_atr = atr_arr[i]
                    if not np.isnan(cur_atr):
                        # Low volatility = good for mean reversion
                        if cur_atr < pct[0]:
                            score += 0.5
                            details["atr_pct"] = "low_vol"

        # === Keltner Channels ===
        if self.use_keltner:
            kc_u = ind["kc_upper"][i]
            kc_l = ind["kc_lower"][i]
            if not np.isnan(kc_u) and not np.isnan(kc_l):
                if direction == "LONG" and close <= kc_l:
                    score += 1.0
                    details["keltner"] = "below_lower"
                elif direction == "SHORT" and close >= kc_u:
                    score += 1.0
                    details["keltner"] = "above_upper"

        # === Volume ===
        if self.use_volume:
            vol_avg = ind["vol_sma"][i]
            if not np.isnan(vol_avg) and vol_avg > 0 and vol > vol_avg * self.vol_threshold:
                score += 1.0
                details["volume"] = f"{vol / vol_avg:.1f}x"

        # === MFI ===
        if self.use_mfi:
            mfi = ind["mfi"][i]
            if not np.isnan(mfi):
                if direction == "LONG" and mfi < self.mfi_os:
                    score += 1.0
                    details["mfi"] = f"OS({mfi:.0f})"
                elif direction == "SHORT" and mfi > self.mfi_ob:
                    score += 1.0
                    details["mfi"] = f"OB({mfi:.0f})"

        # === OBV Slope ===
        if self.use_obv_slope:
            obv_now = ind["obv"][i]
            obv_sma = ind["obv_sma"][i]
            if not np.isnan(obv_now) and not np.isnan(obv_sma):
                if direction == "LONG" and obv_now > obv_sma:
                    score += 0.5
                    details["obv"] = "acc"
                elif direction == "SHORT" and obv_now < obv_sma:
                    score += 0.5
                    details["obv"] = "dist"

        # === Range Position ===
        if self.use_range_position:
            rh = ind["range_high"][i]
            rl = ind["range_low"][i]
            if not np.isnan(rh) and not np.isnan(rl) and rh > rl:
                pos = (close - rl) / (rh - rl)
                if direction == "LONG" and pos < 0.2:
                    score += 1.0
                    details["range"] = f"low({pos:.2f})"
                elif direction == "SHORT" and pos > 0.8:
                    score += 1.0
                    details["range"] = f"high({pos:.2f})"

        # === Higher Highs / Lower Lows ===
        if self.use_hh_ll:
            rh = ind["rolling_high"][i]
            rl = ind["rolling_low"][i]
            ph = ind["prev_high"][i]
            pl = ind["prev_low"][i]
            if not any(np.isnan(x) for x in [rh, rl, ph, pl]):
                if direction == "LONG" and rl > pl:
                    score += 0.5
                    details["hh_ll"] = "HL"
                elif direction == "SHORT" and rh < ph:
                    score += 0.5
                    details["hh_ll"] = "LH"

        return score, details

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

        # ADX trend filter — skip very strong trends
        adx = ind["adx"][i]
        if not np.isnan(adx) and adx > self.adx_max:
            return None

        # Check all enabled patterns
        best_signal = None
        best_score = -1
        best_pattern = None
        best_details = {}

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

            score, details = self._score_setup(data, i, direction)

            # Hard filter mode: require ALL enabled indicators to confirm
            if self.hard_filter:
                enabled_count = sum(1 for x in [
                    self.use_rsi, self.use_stoch_rsi, self.use_williams_r,
                    self.use_macd, self.use_cci, self.use_ema_alignment,
                    self.use_bb, self.use_volume, self.use_mfi,
                    self.use_range_position,
                ] if x)
                # In hard mode, need ALL indicators to agree
                # (ADX, ATR pct, OBV, HH/LL give 0.5 so don't count them as hard requirements)
                if score < enabled_count * 0.8:
                    continue
            elif score < self.min_score:
                continue

            if score > best_score:
                best_score = score
                best_signal = direction
                best_pattern = pat_name
                best_details = details

        if best_signal is None:
            return None

        if best_signal == "LONG":
            stop = price - atr * self.stop_atr
            target = price + atr * self.target_atr
        else:
            stop = price + atr * self.stop_atr
            target = price - atr * self.target_atr

        # Leverage scales with score
        lev = self.base_leverage
        if best_score >= 4:
            lev *= 1.3
        elif best_score >= 3:
            lev *= 1.15

        self._bars_in_trade = 0

        return {
            "action": best_signal,
            "stop": stop,
            "target": target,
            "signal": f"{best_pattern}|s{best_score:.1f}",
            "leverage": lev,
            "market_regime": "CANDLE_V2_2",
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
