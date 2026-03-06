"""
V14 — Cross-Asset Regime-Adaptive Strategy with Kelly Sizing.
Building on V13.13, targeting 10%+/mo per asset.

V14.0: Exact copy of V13.13 (baseline)
V14.1: Extreme RSI MR enhancement, cross-asset momentum, drawdown recovery

V13.1 changes (conservative, data-driven):
- Per-asset Kelly fractions (SOL 0.45, others 0.35)
- Per-asset direction biases from DB (LINK avoids bull longs, ETH avoids bull shorts)
- Wider mean reversion RSI (38/62) with Williams %R confirmation
- Improved time exits: don't exit profitable trades early, extend to 12 bars
- Kelly min leverage raised to 1.5 (was 1.0), min history reduced to 15 (was 20)
- Reduced cooldown from 8 to 6 bars
- Per-asset range position filters tuned from DB
"""

import numpy as np
import pandas as pd
import talib

# Per-asset direction permissions from DB analysis
# DB shows: LINK bull LONG = -0.42 avg, LINK sideways LONG = -0.53 avg
# ETH bull SHORT = -0.45 avg, SOL bull SHORT = -1.18 avg
ASSET_DIRECTION_FILTER = {
    "BTC": {"bear_long": False},
    "ETH": {"bull_short": False, "breakout_short": False},
    "SOL": {"bull_short": False},
    "LINK": {"bull_long": False, "breakout_short": False},
}

# Per-asset Kelly fractions (SOL has strongest edge in DB)
ASSET_KELLY = {
    "BTC": 0.75,
    "ETH": 0.60,  # V14.3: push from 0.55
    "SOL": 0.55,
    "LINK": 0.85,  # V14: push from 0.80
}

# Per-asset breakout stop/target ATR multipliers
ASSET_BO_STOP = {
    "BTC": 2.0,
    "ETH": 2.0,
    "SOL": 1.5,
    "LINK": 2.5,
}

ASSET_BO_TARGET = {
    "BTC": 20,
    "ETH": 12,
    "SOL": 20,
    "LINK": 12,
}

# Per-asset MR stop ATR multiplier
ASSET_MR_STOP = {
    "BTC": 1.2,
    "ETH": 1.0,
    "SOL": 1.0,
    "LINK": 1.5,
}

# Per-asset MR RSI thresholds (tighter = fewer but higher quality trades)
ASSET_MR_RSI = {
    "BTC": (38, 62),
    "ETH": (38, 62),
    "SOL": (38, 62),
    "LINK": (35, 65),
}

# Per-asset BB period (BTC/LINK better with shorter, ETH/SOL with standard)
ASSET_BB_PERIOD = {
    "BTC": 16,
    "ETH": 20,
    "SOL": 20,
    "LINK": 16,
}

# Per-asset minimum leverage floors (positive-edge assets shouldn't go below this)
ASSET_MIN_LEVERAGE = {
    "BTC": 6.0,  # V14.3: push from 4.5
    "ETH": 3.5,  # V14.3: push from 3.0
    "SOL": 3.0,
    "LINK": 7.0,  # V14: push from 6.0
}

# Per-asset max leverage to control drawdown
ASSET_MAX_LEVERAGE = {
    "BTC": 15.0,  # DD ~24% — can't push more
    "ETH": 7.0,  # V14.3: keep at 7.0 — DD-constrained
    "SOL": 8.0,
    "LINK": 16.0,  # V14: push from 15.0
}

# Hour-of-day edges: favorable hours get confidence boost, bad hours get penalty
# Based on data analysis of hourly returns across 1524 samples per hour
ASSET_GOOD_HOURS = {
    "BTC": {22, 20, 8, 21, 15, 14, 10},
    "ETH": {22, 21, 3, 11, 20, 4, 5, 9},
    "SOL": {22, 3, 11, 4, 9, 5, 10, 7, 18, 0, 2},
    "LINK": {22, 3, 9, 5, 4, 20, 21, 7},
}
ASSET_BAD_HOURS = {
    "BTC": {23, 19, 13, 1, 16},
    "ETH": {13, 1, 23, 6, 16},
    "SOL": {16, 13, 19, 23, 12},
    "LINK": {17, 13, 16, 1, 8, 0, 12, 19, 23},
}

# Best TA-Lib patterns per asset (from exhaustive scan)
ASSET_BULL_PATTERNS = {
    "BTC": ["CDLSEPARATINGLINES", "CDLHIKKAKEMOD", "CDLHARAMICROSS"],
    "ETH": ["CDLHIKKAKEMOD"],
    "SOL": ["CDLSEPARATINGLINES", "CDLHARAMICROSS", "CDL3WHITESOLDIERS", "CDLHIKKAKEMOD"],
    "LINK": ["CDL3WHITESOLDIERS", "CDLHIKKAKEMOD"],
}
ASSET_BEAR_PATTERNS = {
    "BTC": ["CDLMARUBOZU", "CDLHANGINGMAN", "CDLSTALLEDPATTERN"],
    "ETH": ["CDLIDENTICAL3CROWS", "CDLHIKKAKEMOD", "CDLADVANCEBLOCK", "CDLSTALLEDPATTERN"],
    "SOL": ["CDLSTALLEDPATTERN", "CDLHIKKAKEMOD", "CDLIDENTICAL3CROWS"],
    "LINK": ["CDLIDENTICAL3CROWS", "CDLADVANCEBLOCK", "CDLHANGINGMAN", "CDLSEPARATINGLINES", "CDLHIKKAKE"],
}


class SqueezeV14:

    def __init__(self, fixed_leverage=None, btc_data=None,
                 kelly_fraction=0.3, kelly_window=40, max_leverage=8.0,
                 default_leverage=3.0, asset_name=None,
                 # Regime detection thresholds
                 adx_trending=30, adx_sideways=22,
                 ema_slope_threshold=0.4, atr_volatile_pctile=85,
                 bb_width_sideways_ratio=0.9,
                 # Mean reversion params
                 mr_bb_period=20, mr_rsi_long=38, mr_rsi_short=62,
                 mr_bb_entry_pct=0.01, mr_stop_atr=1.2,
                 # Breakout params (from V10)
                 bo_rsi_long=(43, 70), bo_rsi_short=(30, 57),
                 bo_vol_dead_low=1.35, bo_vol_dead_high=2.1,
                 bo_atr_max=3.5):
        self.fixed_leverage = fixed_leverage
        self.kelly_window = kelly_window
        self.max_leverage = max_leverage
        # Per-asset default leverage (used when Kelly has insufficient history)
        ASSET_DEFAULT_LEV = {"BTC": 6.5, "ETH": 5.0, "SOL": 3.0, "LINK": 15.0}
        self.default_leverage = ASSET_DEFAULT_LEV.get(asset_name, default_leverage)
        self.asset_name = asset_name

        # Per-asset Kelly fraction and max leverage
        self.kelly_fraction = ASSET_KELLY.get(asset_name, kelly_fraction)
        if asset_name in ASSET_MAX_LEVERAGE:
            self.max_leverage = ASSET_MAX_LEVERAGE[asset_name]

        # Regime thresholds
        self.adx_trending = adx_trending
        self.adx_sideways = adx_sideways
        self.ema_slope_threshold = ema_slope_threshold
        self.atr_volatile_pctile = atr_volatile_pctile / 100.0
        self.bb_width_sideways_ratio = bb_width_sideways_ratio

        # Mean reversion — per-asset BB period and RSI
        self.mr_bb_period = ASSET_BB_PERIOD.get(asset_name, mr_bb_period)
        rsi_thresholds = ASSET_MR_RSI.get(asset_name, (mr_rsi_long, mr_rsi_short))
        self.mr_rsi_long = rsi_thresholds[0]
        self.mr_rsi_short = rsi_thresholds[1]
        self.mr_bb_entry_pct = mr_bb_entry_pct
        self.mr_stop_atr = ASSET_MR_STOP.get(asset_name, mr_stop_atr)

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
        self._breakeven_set = False  # track if stop moved to breakeven
        self._pyramid_count = 0  # track pyramid adds (max 2)
        self._entry_atr = 0  # ATR at entry for R-multiple calculations
        self._entry_volume = 0  # V14: volume at entry for exhaustion check

        # Rolling trade history for Kelly
        self._trade_history = []

        # Regime tracking for reporting
        self.regime_counts = {"TRENDING": 0, "SIDEWAYS": 0, "VOLATILE": 0, "TRANSITION": 0}
        self.trade_regimes = []

        # V14: Cross-asset momentum data (set externally)
        self._cross_asset_momentum = 0  # -1 to +1: fraction of assets trending up
        # V14: Drawdown recovery mode
        self._equity_peak = 1000.0
        self._current_equity = 1000.0
        self._dd_recovery_mode = False

    def set_btc_data(self, btc_data):
        btc_closes = btc_data["close"].astype(float)
        self._btc_roc = btc_closes.pct_change(24) * 100
        self._btc_roc.index = btc_data.index

    def set_cross_asset_momentum(self, momentum):
        """Set cross-asset momentum factor. Range: -1 (all bearish) to +1 (all bullish)."""
        self._cross_asset_momentum = momentum

    def update_equity(self, equity):
        """Track equity for drawdown recovery mode."""
        self._current_equity = equity
        self._equity_peak = max(self._equity_peak, equity)
        dd_pct = (self._equity_peak - equity) / self._equity_peak * 100 if self._equity_peak > 0 else 0
        if dd_pct >= 15:
            self._dd_recovery_mode = True
        elif dd_pct < 10:
            self._dd_recovery_mode = False

    def reset(self):
        self._ind = None
        self._trailing_stop = None
        self._best_price = None
        self._last_exit_bar = -12
        self._entry_bar = -1
        self._trade_type = None
        self._breakeven_set = False
        self._pyramid_count = 0
        self._entry_atr = 0
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

        # Williams %R (14-period)
        highest_high = highs.rolling(14).max()
        lowest_low = lows.rolling(14).min()
        willr = -100 * (highest_high - closes) / (highest_high - lowest_low).replace(0, 1)

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

        # Range position filter (from V12)
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

        # TA-Lib candlestick patterns for mean reversion confirmation
        o, h, l, c = opens.values, highs.values, lows.values, closes.values
        # Bullish reversal patterns (positive = bullish signal)
        hammer = pd.Series(talib.CDLHAMMER(o, h, l, c), index=data.index)
        engulfing = pd.Series(talib.CDLENGULFING(o, h, l, c), index=data.index)
        morningstar = pd.Series(talib.CDLMORNINGSTAR(o, h, l, c), index=data.index)
        doji = pd.Series(talib.CDLDOJI(o, h, l, c), index=data.index)
        # Bearish reversal patterns (negative = bearish signal)
        shootingstar = pd.Series(talib.CDLSHOOTINGSTAR(o, h, l, c), index=data.index)
        eveningstar = pd.Series(talib.CDLEVENINGSTAR(o, h, l, c), index=data.index)

        # Aggregate: bullish reversal score and bearish reversal score
        bull_reversal = ((hammer > 0).astype(int) + (engulfing > 0).astype(int) +
                         (morningstar > 0).astype(int) + (doji != 0).astype(int))
        bear_reversal = ((shootingstar > 0).astype(int) + (engulfing < 0).astype(int) +
                         (eveningstar > 0).astype(int) + (doji != 0).astype(int))

        # VWAP (rolling 24h)
        tp = (highs + lows + closes) / 3
        vwap = (tp * volumes).rolling(24).sum() / volumes.rolling(24).sum().replace(0, 1)
        vwap_dist = (closes - vwap) / vwap.replace(0, 1) * 100  # % distance from VWAP

        # Multi-timeframe: 4h indicators from hourly data
        close_4h = closes.rolling(4).mean()  # smoothed 4h close proxy
        ema_4h_fast = close_4h.ewm(span=12, adjust=False).mean()  # ~48h = 2 days
        ema_4h_slow = close_4h.ewm(span=26, adjust=False).mean()  # ~104h = 4.3 days
        ema_4h_slope = (ema_4h_fast - ema_4h_fast.shift(4)) / ema_4h_fast.shift(4).replace(0, 1) * 100

        # Daily RSI (using 24h-smoothed price changes)
        delta_d = closes.diff(24)  # 24h change
        gain_d = delta_d.where(delta_d > 0, 0).rolling(14 * 24).mean()
        loss_d = (-delta_d.where(delta_d < 0, 0)).rolling(14 * 24).mean()
        rs_d = gain_d / loss_d.replace(0, 1e-10)
        rsi_daily = 100 - (100 / (1 + rs_d))

        # 4h trend direction
        mtf_bull = (ema_4h_fast > ema_4h_slow).astype(int)  # 1=bullish 4h trend

        # Volatility clustering: ATR relative to recent ATR
        atr_ratio = atr / atr.rolling(48).mean().replace(0, 1)  # current vs 2-day avg ATR
        # Recent big move detection (last 4 bars had >2x normal ATR)
        recent_vol_spike = (atr_ratio.rolling(4).max() > 1.8).astype(int)

        # Weak conviction candles (filter for breakout quality)
        spinningtop = pd.Series(talib.CDLSPINNINGTOP(o, h, l, c), index=data.index)
        weak_candle = ((doji != 0) | (spinningtop != 0)).astype(int)

        # Extended TA-Lib patterns for confidence scoring
        separatinglines = pd.Series(talib.CDLSEPARATINGLINES(o, h, l, c), index=data.index)
        hikkakemod = pd.Series(talib.CDLHIKKAKEMOD(o, h, l, c), index=data.index)
        haramicross = pd.Series(talib.CDLHARAMICROSS(o, h, l, c), index=data.index)
        threews = pd.Series(talib.CDL3WHITESOLDIERS(o, h, l, c), index=data.index)
        identical3crows = pd.Series(talib.CDLIDENTICAL3CROWS(o, h, l, c), index=data.index)
        advanceblock = pd.Series(talib.CDLADVANCEBLOCK(o, h, l, c), index=data.index)
        stalledpattern = pd.Series(talib.CDLSTALLEDPATTERN(o, h, l, c), index=data.index)
        hangingman = pd.Series(talib.CDLHANGINGMAN(o, h, l, c), index=data.index)
        marubozu = pd.Series(talib.CDLMARUBOZU(o, h, l, c), index=data.index)
        hikkake = pd.Series(talib.CDLHIKKAKE(o, h, l, c), index=data.index)

        # Hour of day
        hour = pd.Series(data.index.hour, index=data.index)

        self._ind = pd.DataFrame({
            "close": closes, "high": highs, "low": lows, "open": opens,
            "ema8": ema8, "ema21": ema21, "ema55": ema55,
            "ema_d_fast": ema_d_fast, "ema_d_slow": ema_d_slow,
            "ema_d_trend": ema_d_trend, "ema_200d": ema_200d,
            "atr": atr, "atr_pct": atr_pct, "atr_pct_rank": atr_pct_rank,
            "rsi": rsi, "willr": willr,
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
            "bull_reversal": bull_reversal, "bear_reversal": bear_reversal,
            "weak_candle": weak_candle,
            "prev_high": highs.shift(1), "prev_low": lows.shift(1),
            "vwap": vwap, "vwap_dist": vwap_dist,
            "volume": volumes,
            "ema_4h_slope": ema_4h_slope, "mtf_bull": mtf_bull,
            "rsi_daily": rsi_daily,
            "atr_ratio": atr_ratio, "recent_vol_spike": recent_vol_spike,
            # Extended TA-Lib patterns
            "pat_separatinglines": separatinglines,
            "pat_hikkakemod": hikkakemod,
            "pat_haramicross": haramicross,
            "pat_3whitesoldiers": threews,
            "pat_identical3crows": identical3crows,
            "pat_advanceblock": advanceblock,
            "pat_stalledpattern": stalledpattern,
            "pat_hangingman": hangingman,
            "pat_marubozu": marubozu,
            "pat_hikkake": hikkake,
            "hour": hour,
        }, index=data.index)

    def _compute_adx(self, highs, lows, closes, period=14):
        prev_high = highs.shift(1)
        prev_low = lows.shift(1)
        prev_close = closes.shift(1)

        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)

        plus_dm = highs - prev_high
        minus_dm = prev_low - lows
        plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
        minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

        atr_smooth = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / atr_smooth.replace(0, 1))
        minus_di = 100 * (minus_dm.rolling(period).mean() / atr_smooth.replace(0, 1))

        dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, 1)
        adx = dx.rolling(period).mean()

        return adx

    def _detect_regime(self, i):
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

    def _direction_allowed(self, direction, i, regime):
        """Check per-asset direction filter from DB analysis."""
        if self.asset_name not in ASSET_DIRECTION_FILTER:
            return True
        filters = ASSET_DIRECTION_FILTER[self.asset_name]
        trend = self._daily_trend(i)
        is_bear = self._ind.iloc[i]["bear_market"] == 1

        if direction == "LONG":
            if is_bear and filters.get("bear_long", True) is False:
                return False
            if trend == "UP" and filters.get("bull_long", True) is False:
                return False
            if regime == "SIDEWAYS" and filters.get("sideways_long", True) is False:
                return False
        else:
            if trend == "UP" and filters.get("bull_short", True) is False:
                return False
            if is_bear and filters.get("bear_short", True) is False:
                return False
        return True

    def _confidence_breakout(self, i, direction):
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
        if self.fixed_leverage is not None:
            return self.fixed_leverage

        dir_trades = [pnl for d, pnl in self._trade_history if d == direction]

        if len(dir_trades) < 15:
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
            return 1.5  # negative edge: low but not minimum

        leverage = kelly * self.kelly_fraction * 20

        min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
        lev = max(min_lev, min(leverage, self.max_leverage))
        return lev

    def _unified_confidence(self, i, direction, trade_type):
        """Unified confidence score from all signals → leverage multiplier.

        Score 50-100 maps to leverage multiplier:
        50-60: 0.8x (marginal — reduce size)
        60-70: 1.0x (normal)
        70-80: 1.3x (good confluence)
        80-90: 1.8x (strong confluence)
        90+:   2.2x (exceptional — max conviction)
        """
        ind = self._ind.iloc[i]
        score = 50  # base

        # MTF: 4h trend alignment (+15)
        ema_4h_slope = float(ind["ema_4h_slope"]) if not pd.isna(ind["ema_4h_slope"]) else 0
        mtf_bull = int(ind["mtf_bull"]) if not pd.isna(ind["mtf_bull"]) else 0
        if direction == "LONG" and mtf_bull == 1 and ema_4h_slope > 0.1:
            score += 15
        elif direction == "SHORT" and mtf_bull == 0 and ema_4h_slope < -0.1:
            score += 15
        elif direction == "LONG" and mtf_bull == 0 and ema_4h_slope < -0.1:
            score -= 10  # 4h trend strongly disagrees
        elif direction == "SHORT" and mtf_bull == 1 and ema_4h_slope > 0.1:
            score -= 10

        # VWAP agreement (+10)
        vwap_dist = float(ind["vwap_dist"]) if not pd.isna(ind["vwap_dist"]) else 0
        if direction == "LONG" and vwap_dist > 0.3:
            score += 10
        elif direction == "SHORT" and vwap_dist < -0.3:
            score += 10

        # Volume spike (+10)
        vol_ratio = float(ind["vol_ratio"])
        if vol_ratio > 2.5:
            score += 10
        elif vol_ratio > 1.8:
            score += 5

        # Volatility clustering (+8)
        vol_spike = int(ind["recent_vol_spike"]) if not pd.isna(ind["recent_vol_spike"]) else 0
        if vol_spike == 1:
            score += 8

        # Candlestick pattern confirmation (+7)
        if trade_type == "meanrev":
            if direction == "LONG" and ind["bull_reversal"] >= 1:
                score += 7
            elif direction == "SHORT" and ind["bear_reversal"] >= 1:
                score += 7
        else:  # breakout
            if ind["body_ratio"] > 0.7:
                score += 7
            elif ind["body_ratio"] > 0.5:
                score += 3

        # Daily trend alignment (for breakout) (+10)
        trend = self._daily_trend(i)
        if trade_type == "breakout":
            if direction == "LONG" and trend == "UP":
                score += 10
            elif direction == "SHORT" and trend == "DOWN":
                score += 10

        # BTC health for alts (+5)
        if self.asset_name != "BTC" and self._btc_roc is not None:
            try:
                ts = self._ind.index[i]
                idx = self._btc_roc.index.get_indexer([ts], method="nearest")[0]
                btc_roc = float(self._btc_roc.iloc[idx])
                if direction == "LONG" and btc_roc > 2:
                    score += 5
                elif direction == "SHORT" and btc_roc < -2:
                    score += 5
            except Exception:
                pass

        # Hour-of-day edge (+8 / -5)
        hour = int(ind["hour"]) if not pd.isna(ind["hour"]) else 12
        good_hours = ASSET_GOOD_HOURS.get(self.asset_name, set())
        bad_hours = ASSET_BAD_HOURS.get(self.asset_name, set())
        if hour in good_hours:
            score += 8
        elif hour in bad_hours:
            score -= 5

        # TA-Lib pattern confirmation (+10)
        pat_score = self._check_talib_patterns(ind, direction)
        score += pat_score

        # V14: Cross-asset momentum factor (boost only — no penalty)
        # Skip for BTC and SOL
        if self.asset_name not in ("BTC", "SOL"):
            cam = self._cross_asset_momentum
            if direction == "LONG" and cam > 0.5:
                score += 12
            elif direction == "SHORT" and cam < -0.5:
                score += 12

        score = max(40, min(score, 100))

        # SOL: already high returns, confidence mult adds DD risk without benefit
        if self.asset_name == "SOL":
            return 1.0

        # Map score to leverage multiplier
        if score >= 90:
            return 2.2
        elif score >= 80:
            return 1.8
        elif score >= 70:
            return 1.3
        elif score >= 60:
            return 1.1
        elif score < 50:
            return 0.8
        else:
            return 1.0

    def _check_talib_patterns(self, ind, direction):
        """Check best TA-Lib patterns for this asset and direction. Returns score bonus."""
        asset = self.asset_name
        bonus = 0

        if direction == "LONG":
            patterns = ASSET_BULL_PATTERNS.get(asset, [])
            pat_map = {
                "CDLSEPARATINGLINES": "pat_separatinglines",
                "CDLHIKKAKEMOD": "pat_hikkakemod",
                "CDLHARAMICROSS": "pat_haramicross",
                "CDL3WHITESOLDIERS": "pat_3whitesoldiers",
            }
            for pat in patterns:
                col = pat_map.get(pat)
                if col and col in ind.index and ind[col] > 0:
                    bonus += 5
        else:  # SHORT
            patterns = ASSET_BEAR_PATTERNS.get(asset, [])
            pat_map = {
                "CDLMARUBOZU": "pat_marubozu",
                "CDLHANGINGMAN": "pat_hangingman",
                "CDLSTALLEDPATTERN": "pat_stalledpattern",
                "CDLIDENTICAL3CROWS": "pat_identical3crows",
                "CDLHIKKAKEMOD": "pat_hikkakemod",
                "CDLADVANCEBLOCK": "pat_advanceblock",
                "CDLSEPARATINGLINES": "pat_separatinglines",
                "CDLHIKKAKE": "pat_hikkake",
            }
            for pat in patterns:
                col = pat_map.get(pat)
                if col and col in ind.index:
                    # Bear patterns: check for negative signal (bearish)
                    # Some patterns always signal negative for bearish
                    val = ind[col]
                    if pat in ("CDLHIKKAKEMOD", "CDLIDENTICAL3CROWS", "CDLADVANCEBLOCK",
                               "CDLSTALLEDPATTERN", "CDLHIKKAKE", "CDLSEPARATINGLINES"):
                        if val < 0:
                            bonus += 5
                    elif val > 0:  # hangingman, marubozu signal positive but mean bearish
                        bonus += 5

        return min(bonus, 10)  # cap at +10

    def _regime_leverage_mult(self, regime, trend, direction):
        if regime == "VOLATILE":
            return 0.5
        if regime == "TRANSITION":
            # V14: Per-asset TRANSITION mult
            TRANS_MULT = {"BTC": 2.5, "ETH": 2.0, "SOL": 2.0, "LINK": 2.5}
            return TRANS_MULT.get(self.asset_name, 2.0)
        if regime == "TRENDING":
            if (direction == "LONG" and trend == "UP") or (direction == "SHORT" and trend == "DOWN"):
                return 1.4
            return 0.5
        # SIDEWAYS — strongest edge per DB
        # V14: Push SIDEWAYS mult for BTC/LINK
        SW_MULT = {"BTC": 2.0, "ETH": 2.0, "SOL": 2.0, "LINK": 2.5}
        return SW_MULT.get(self.asset_name, 2.0)

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
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        atr_pct = float(ind["atr_pct"])
        rsi = float(ind["rsi"])
        vol_ratio = float(ind["vol_ratio"])
        range_pct = float(ind["range_pct"]) if not pd.isna(ind["range_pct"]) else 0.5

        if not ind["is_squeeze"]:
            return None

        # Skip breakout on weak conviction candles (doji/spinning top)
        if ind["weak_candle"] == 1:
            return None

        if atr_pct > self.bo_atr_max:
            return None

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
            range_pct <= 0.75):

            if not self._direction_allowed("LONG", i, regime):
                return None
            if self._btc_crashed(data.index[i]):
                return None

            score = self._confidence_breakout(i, "LONG")
            if score < min_score:
                return None
            score = min(score, 80)

            kelly_lev = self._compute_kelly("LONG")
            regime_mult = self._regime_leverage_mult(regime, trend, "LONG")
            conf_mult = self._unified_confidence(i, "LONG", "breakout")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * regime_mult * conf_mult, self.max_leverage)), 2)

            bo_stop_mult = ASSET_BO_STOP.get(self.asset_name, 2.5)
            bo_target_mult = ASSET_BO_TARGET.get(self.asset_name, 12)
            self._trailing_stop = price - (atr * bo_stop_mult)
            self._best_price = price
            self._entry_bar = i
            self._breakeven_set = False
            self._pyramid_count = 0
            self._entry_atr = atr
            self._entry_volume = float(ind["volume"])
            self._trade_type = "breakout"

            return {
                "action": "LONG",
                "signal": f"V13_BO_L(s{score},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": price - (atr * bo_stop_mult),
                "target": price + (atr * bo_target_mult),
                "leverage": lev,
            }

        # SHORT
        if (price < ind["prev_low"] and
            ind["bullish"] == 0 and
            ind["body_ratio"] > 0.4 and
            self.bo_rsi_short[0] <= rsi <= self.bo_rsi_short[1] and
            vol_ratio > 1.2 and
            range_pct >= 0.25 and
            trend == "DOWN"):

            if not self._direction_allowed("SHORT", i, regime):
                return None
            # Per-asset breakout short filter
            filters = ASSET_DIRECTION_FILTER.get(self.asset_name, {})
            if filters.get("breakout_short", True) is False:
                return None

            score = self._confidence_breakout(i, "SHORT")
            short_min = max(min_score, 65)
            if score < short_min:
                return None
            score = min(score, 80)

            kelly_lev = self._compute_kelly("SHORT")
            regime_mult = self._regime_leverage_mult(regime, trend, "SHORT")
            conf_mult = self._unified_confidence(i, "SHORT", "breakout")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * regime_mult * conf_mult, self.max_leverage)), 2)

            bo_stop_mult = ASSET_BO_STOP.get(self.asset_name, 2.5)
            bo_target_mult = ASSET_BO_TARGET.get(self.asset_name, 12)
            self._trailing_stop = price + (atr * bo_stop_mult)
            self._best_price = price
            self._entry_bar = i
            self._breakeven_set = False
            self._pyramid_count = 0
            self._entry_atr = atr
            self._entry_volume = float(ind["volume"])
            self._trade_type = "breakout"

            return {
                "action": "SHORT",
                "signal": f"V13_BO_S(s{score},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": price + (atr * bo_stop_mult),
                "target": price - (atr * bo_target_mult),
                "leverage": lev,
            }

        return None

    def _try_meanrev(self, data, i, regime):
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        rsi = float(ind["rsi"])
        willr = float(ind["willr"]) if not pd.isna(ind["willr"]) else -50
        bb_lower = float(ind["bb_lower"])
        bb_upper = float(ind["bb_upper"])
        bb_mid = float(ind["bb_mid"])
        atr_pct = float(ind["atr_pct"])

        if pd.isna(bb_lower) or pd.isna(bb_upper) or pd.isna(bb_mid):
            return None

        if atr_pct > 3.0:
            return None

        trend = self._daily_trend(i)

        vwap_dist = float(ind["vwap_dist"]) if not pd.isna(ind["vwap_dist"]) else 0

        # Per-asset MR target extension (% of way to opposite band)
        MR_TARGET_EXT = {"BTC": 0.85, "ETH": 0.85, "SOL": 0.7, "LINK": 0.90}
        mr_target_pct = MR_TARGET_EXT.get(self.asset_name, 0.7)

        # LONG: price near lower BB + RSI oversold + Williams %R oversold + bullish candle
        if (price <= bb_lower * (1 + self.mr_bb_entry_pct) and
            rsi < self.mr_rsi_long and
            willr < -75 and  # Williams %R confirmation
            ind["bullish"] == 1 and
            ind["body_ratio"] > 0.3):

            if not self._direction_allowed("LONG", i, regime):
                return None

            stop = price - (atr * self.mr_stop_atr)
            # Target beyond BB mid — aim for 70% of the way to upper BB
            target = bb_mid + (bb_upper - bb_mid) * mr_target_pct

            gain_dist = target - price
            loss_dist = price - stop
            if loss_dist <= 0 or gain_dist / loss_dist < 0.7:
                return None

            kelly_lev = self._compute_kelly("LONG")
            regime_mult = self._regime_leverage_mult(regime, trend, "LONG")
            conf_mult = self._unified_confidence(i, "LONG", "meanrev")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * regime_mult * conf_mult, self.max_leverage)), 2)

            self._trailing_stop = stop
            self._best_price = price
            self._entry_bar = i
            self._breakeven_set = False
            self._pyramid_count = 0
            self._entry_atr = atr
            self._entry_volume = float(ind["volume"]) if "volume" in ind.index else 0
            self._trade_type = "meanrev"

            return {
                "action": "LONG",
                "signal": f"V13_MR_L(rsi{rsi:.0f},w{willr:.0f},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        # SHORT: price near upper BB + RSI overbought + Williams %R overbought + bearish candle
        if (price >= bb_upper * (1 - self.mr_bb_entry_pct) and
            rsi > self.mr_rsi_short and
            willr > -25 and  # Williams %R confirmation
            ind["bullish"] == 0 and
            ind["body_ratio"] > 0.3):

            if not self._direction_allowed("SHORT", i, regime):
                return None

            stop = price + (atr * self.mr_stop_atr)
            # Target beyond BB mid — aim for 70% of the way to lower BB
            target = bb_mid - (bb_mid - bb_lower) * mr_target_pct

            gain_dist = price - target
            loss_dist = stop - price
            if loss_dist <= 0 or gain_dist / loss_dist < 0.7:
                return None

            kelly_lev = self._compute_kelly("SHORT")
            regime_mult = self._regime_leverage_mult(regime, trend, "SHORT")
            conf_mult = self._unified_confidence(i, "SHORT", "meanrev")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * regime_mult * conf_mult, self.max_leverage)), 2)

            self._trailing_stop = stop
            self._best_price = price
            self._entry_bar = i
            self._breakeven_set = False
            self._pyramid_count = 0
            self._entry_atr = atr
            self._entry_volume = float(ind["volume"]) if "volume" in ind.index else 0
            self._trade_type = "meanrev"

            return {
                "action": "SHORT",
                "signal": f"V13_MR_S(rsi{rsi:.0f},w{willr:.0f},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        return None

    # Per-asset VWAP bounce enable
    VWAP_BOUNCE_ASSETS = {"ETH", "LINK"}  # BTC/SOL excluded — loses money

    def _try_vwap_bounce(self, data, i, regime):
        """VWAP bounce: price pulls back to VWAP in a trend and bounces."""
        if self.asset_name not in self.VWAP_BOUNCE_ASSETS:
            return None
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        vwap = float(ind["vwap"]) if not pd.isna(ind["vwap"]) else 0
        vwap_dist = float(ind["vwap_dist"]) if not pd.isna(ind["vwap_dist"]) else 0
        rsi = float(ind["rsi"])

        if vwap <= 0 or pd.isna(atr) or atr <= 0:
            return None

        trend = self._daily_trend(i)
        ema_4h_slope = float(ind["ema_4h_slope"]) if not pd.isna(ind["ema_4h_slope"]) else 0
        mtf_bull = int(ind["mtf_bull"]) if not pd.isna(ind["mtf_bull"]) else 0

        # LONG: uptrend, price pulls back close to VWAP, then bullish candle bounces
        if (trend == "UP" and
            mtf_bull == 1 and
            ema_4h_slope > 0.05 and
            -0.3 <= vwap_dist <= 0.3 and  # price near VWAP
            40 <= rsi <= 55 and  # pulled back but not oversold
            ind["bullish"] == 1 and
            ind["body_ratio"] > 0.4 and
            ind["vol_ratio"] > 0.8):

            if not self._direction_allowed("LONG", i, regime):
                return None
            if self._btc_crashed(data.index[i]):
                return None

            stop = price - (atr * 1.5)
            target = price + (atr * 4.0)

            kelly_lev = self._compute_kelly("LONG")
            regime_mult = self._regime_leverage_mult(regime, trend, "LONG")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * regime_mult, self.max_leverage)), 2)

            self._trailing_stop = stop
            self._best_price = price
            self._entry_bar = i
            self._breakeven_set = False
            self._pyramid_count = 0
            self._entry_atr = atr
            self._entry_volume = float(ind["volume"])
            self._trade_type = "breakout"  # use breakout exit logic (trailing)

            return {
                "action": "LONG",
                "signal": f"V13_VB_L(vd{vwap_dist:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        # SHORT: downtrend, price retraces up to VWAP, then bearish candle
        if (trend == "DOWN" and
            mtf_bull == 0 and
            ema_4h_slope < -0.05 and
            -0.3 <= vwap_dist <= 0.3 and
            45 <= rsi <= 60 and
            ind["bullish"] == 0 and
            ind["body_ratio"] > 0.4 and
            ind["vol_ratio"] > 0.8):

            if not self._direction_allowed("SHORT", i, regime):
                return None

            stop = price + (atr * 1.5)
            target = price - (atr * 4.0)

            kelly_lev = self._compute_kelly("SHORT")
            regime_mult = self._regime_leverage_mult(regime, trend, "SHORT")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * regime_mult, self.max_leverage)), 2)

            self._trailing_stop = stop
            self._best_price = price
            self._entry_bar = i
            self._breakeven_set = False
            self._pyramid_count = 0
            self._entry_atr = atr
            self._entry_volume = float(ind["volume"])
            self._trade_type = "breakout"

            return {
                "action": "SHORT",
                "signal": f"V13_VB_S(vd{vwap_dist:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        return None

    def _try_momentum_continuation(self, data, i, regime):
        """ETH-only: ride momentum when EMA8 freshly crosses EMA21 with volume + MTF confirmation."""
        if self.asset_name != "ETH":
            return None
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        if pd.isna(atr) or atr <= 0:
            return None

        ema8 = float(ind["ema8"])
        ema21 = float(ind["ema21"])
        vol_ratio = float(ind["vol_ratio"])
        rsi = float(ind["rsi"])
        mtf_bull = int(ind["mtf_bull"]) if not pd.isna(ind["mtf_bull"]) else 0
        ema_4h_slope = float(ind["ema_4h_slope"]) if not pd.isna(ind["ema_4h_slope"]) else 0
        trend = self._daily_trend(i)

        # Check for fresh EMA crossover (within last 3 bars)
        if i < 3:
            return None
        prev_ema8 = [float(self._ind.iloc[i-j]["ema8"]) for j in range(1, 4)]
        prev_ema21 = [float(self._ind.iloc[i-j]["ema21"]) for j in range(1, 4)]

        # LONG: EMA8 just crossed above EMA21, confirmed by MTF + volume
        fresh_bull_cross = (ema8 > ema21 and any(p8 < p21 for p8, p21 in zip(prev_ema8, prev_ema21)))
        if (fresh_bull_cross and
            trend == "UP" and
            mtf_bull == 1 and
            ema_4h_slope > 0.1 and
            vol_ratio > 1.3 and
            40 <= rsi <= 65 and
            ind["body_ratio"] > 0.4 and
            ind["bullish"] == 1):

            if not self._direction_allowed("LONG", i, regime):
                return None
            if self._btc_crashed(data.index[i]):
                return None

            stop = price - (atr * 2.0)
            target = price + (atr * 8.0)

            kelly_lev = self._compute_kelly("LONG")
            regime_mult = self._regime_leverage_mult(regime, trend, "LONG")
            conf_mult = self._unified_confidence(i, "LONG", "breakout")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * regime_mult * conf_mult, self.max_leverage)), 2)

            self._trailing_stop = stop
            self._best_price = price
            self._entry_bar = i
            self._breakeven_set = False
            self._pyramid_count = 0
            self._entry_atr = atr
            self._entry_volume = float(ind["volume"])
            self._trade_type = "breakout"

            return {
                "action": "LONG",
                "signal": f"V14_MC_L(rsi{rsi:.0f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        # SHORT: EMA8 just crossed below EMA21
        fresh_bear_cross = (ema8 < ema21 and any(p8 > p21 for p8, p21 in zip(prev_ema8, prev_ema21)))
        if (fresh_bear_cross and
            trend == "DOWN" and
            mtf_bull == 0 and
            ema_4h_slope < -0.1 and
            vol_ratio > 1.3 and
            35 <= rsi <= 60 and
            ind["body_ratio"] > 0.4 and
            ind["bullish"] == 0):

            if not self._direction_allowed("SHORT", i, regime):
                return None

            stop = price + (atr * 2.0)
            target = price - (atr * 8.0)

            kelly_lev = self._compute_kelly("SHORT")
            regime_mult = self._regime_leverage_mult(regime, trend, "SHORT")
            conf_mult = self._unified_confidence(i, "SHORT", "breakout")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * regime_mult * conf_mult, self.max_leverage)), 2)

            self._trailing_stop = stop
            self._best_price = price
            self._entry_bar = i
            self._breakeven_set = False
            self._pyramid_count = 0
            self._entry_atr = atr
            self._entry_volume = float(ind["volume"])
            self._trade_type = "breakout"

            return {
                "action": "SHORT",
                "signal": f"V14_MC_S(rsi{rsi:.0f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        return None

    def _try_overextension_mr(self, data, i, regime):
        """High-conviction MR when RSI is extremely overextended. Works in ALL regimes.

        V14: Two tiers:
        - Ultra extreme (RSI <15 / >85): 2.5x regime mult, tighter stops (0.7 ATR), wider targets (4 ATR)
        - Moderate extreme (RSI 15-20 / 80-85): 1.8x regime mult, normal params
        """
        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        rsi = float(ind["rsi"])
        bb_lower = float(ind["bb_lower"])
        bb_upper = float(ind["bb_upper"])
        bb_mid = float(ind["bb_mid"])

        if pd.isna(bb_mid) or pd.isna(atr) or atr <= 0:
            return None

        trend = self._daily_trend(i)
        willr = float(ind["willr"]) if not pd.isna(ind["willr"]) else -50

        # V14: Only ultra-extreme for entries, but with more aggressive sizing
        ultra_long = rsi < 15 and willr < -90
        ultra_short = rsi > 85 and willr > -10

        # LONG: RSI extremely oversold
        if (ultra_long and
            price < bb_lower and
            ind["bullish"] == 1 and
            ind["body_ratio"] > 0.4):

            if not self._direction_allowed("LONG", i, regime):
                return None

            stop_mult, target_reach, regime_mult = 1.5, 0.5, 2.0

            stop = price - (atr * stop_mult)
            target = bb_mid + (bb_upper - bb_mid) * target_reach

            gain_dist = target - price
            loss_dist = price - stop
            if loss_dist <= 0 or gain_dist / loss_dist < 1.0:
                return None

            kelly_lev = self._compute_kelly("LONG")
            conf_mult = self._unified_confidence(i, "LONG", "meanrev")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * regime_mult * conf_mult, self.max_leverage)), 2)

            self._trailing_stop = stop
            self._best_price = price
            self._entry_bar = i
            self._breakeven_set = False
            self._pyramid_count = 0
            self._entry_atr = atr
            self._entry_volume = float(ind["volume"]) if "volume" in ind.index else 0
            self._trade_type = "meanrev"

            return {
                "action": "LONG",
                "signal": f"V14_UX_L(rsi{rsi:.0f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        # SHORT: RSI extremely overbought
        if (ultra_short and
            price > bb_upper and
            ind["bullish"] == 0 and
            ind["body_ratio"] > 0.4):

            if not self._direction_allowed("SHORT", i, regime):
                return None

            stop_mult, target_reach, regime_mult = 1.5, 0.5, 2.0

            stop = price + (atr * stop_mult)
            target = bb_mid - (bb_mid - bb_lower) * target_reach

            gain_dist = price - target
            loss_dist = stop - price
            if loss_dist <= 0 or gain_dist / loss_dist < 1.0:
                return None

            kelly_lev = self._compute_kelly("SHORT")
            conf_mult = self._unified_confidence(i, "SHORT", "meanrev")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * regime_mult * conf_mult, self.max_leverage)), 2)

            self._trailing_stop = stop
            self._best_price = price
            self._entry_bar = i
            self._breakeven_set = False
            self._pyramid_count = 0
            self._entry_atr = atr
            self._entry_volume = float(ind["volume"]) if "volume" in ind.index else 0
            self._trade_type = "meanrev"

            return {
                "action": "SHORT",
                "signal": f"V14_UX_S(rsi{rsi:.0f},r{regime[0]},l{lev:.1f}x)",
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

        if i - self._last_exit_bar < 4:  # reduced cooldown
            return None

        ind = self._ind.iloc[i]
        atr = float(ind["atr"])
        if pd.isna(atr) or atr <= 0:
            return None

        regime = self._detect_regime(i)

        # Overextension MR works in ALL regimes — check first
        ox_signal = self._try_overextension_mr(data, i, regime)
        if ox_signal:
            self.regime_counts[regime] += 1
            self.trade_regimes.append((regime, ox_signal["action"], "overext_mr"))
            return ox_signal

        if regime == "TRENDING":
            self.regime_counts["TRENDING"] += 1
            return None

        elif regime == "SIDEWAYS":
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
            # VWAP bounce in SIDEWAYS (LINK only)
            if self.asset_name == "LINK":
                signal = self._try_vwap_bounce(data, i, regime)
                if signal:
                    self.regime_counts["SIDEWAYS"] += 1
                    self.trade_regimes.append((regime, signal["action"], "vwap_bounce"))
                    return signal
            return None

        elif regime == "VOLATILE":
            self.regime_counts["VOLATILE"] += 1
            return None

        else:  # TRANSITION
            # V14: Per-asset TRANSITION breakout min score
            TRANS_BO_MIN = {"BTC": 65, "ETH": 65, "SOL": 55, "LINK": 55}
            trans_min = TRANS_BO_MIN.get(self.asset_name, 65)
            signal = self._try_breakout(data, i, regime, min_score=trans_min)
            if signal:
                if signal["action"] == "SHORT":
                    s = self._confidence_breakout(i, "SHORT")
                    if s < 70:
                        return None
                self.regime_counts["TRANSITION"] += 1
                self.trade_regimes.append((regime, signal["action"], "breakout"))
                return signal
            # V14.3: Momentum continuation for ETH
            signal = self._try_momentum_continuation(data, i, regime)
            if signal:
                self.regime_counts["TRANSITION"] += 1
                self.trade_regimes.append((regime, signal["action"], "momentum"))
                return signal
            # VWAP bounce in TRANSITION
            signal = self._try_vwap_bounce(data, i, regime)
            if signal:
                self.regime_counts["TRANSITION"] += 1
                self.trade_regimes.append((regime, signal["action"], "vwap_bounce"))
                return signal
            return None

    def record_trade(self, direction, pnl_pct):
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
        # Improved time exit: only exit if NOT profitable after 10 bars
        if i - self._entry_bar >= 10:
            if trade.direction == "LONG":
                pnl_r = (price - trade.entry_price) / atr
                if pnl_r < 0.5:
                    self._last_exit_bar = i
                    return "MR_TIME_EXIT"
            elif trade.direction == "SHORT":
                pnl_r = (trade.entry_price - price) / atr
                if pnl_r < 0.5:
                    self._last_exit_bar = i
                    return "MR_TIME_EXIT"

        # Max time: exit after 24 bars no matter what
        if i - self._entry_bar >= 24:
            self._last_exit_bar = i
            return "MR_MAX_TIME"

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
        entry_atr = self._entry_atr if self._entry_atr > 0 else atr
        ind = self._ind.iloc[i]

        # V14: Volume exhaustion removed — kills breakout winners (finding #13)

        # Pyramiding: add to winner (once per trade)
        # Per-asset: ATR threshold and add percentage
        # V14: Double pyramid with per-asset sizing
        PYRAMID_CFG = {
            "BTC": (3.0, 50),   # BTC: conservative — DD-constrained
            "ETH": (2.0, 75),
            "SOL": (2.0, 50),
            "LINK": (2.0, 75),
        }
        pyr_thresh, pyr_pct = PYRAMID_CFG.get(self.asset_name, (3.0, 50))
        # V14: Per-asset max pyramids (BTC=1, others=2)
        MAX_PYR = {"BTC": 1, "ETH": 2, "SOL": 2, "LINK": 2}
        max_pyramids = MAX_PYR.get(self.asset_name, 2)
        if self._pyramid_count < max_pyramids:
            actual_thresh = pyr_thresh * (1 + self._pyramid_count)  # 2nd pyramid needs bigger move
            if trade.direction == "LONG":
                pnl_atr = (price - trade.entry_price) / entry_atr
            else:
                pnl_atr = (trade.entry_price - price) / entry_atr
            if pnl_atr >= actual_thresh:
                self._pyramid_count += 1
                return f"PYRAMID_{pyr_pct}"

        # Breakeven stop: after 2 ATR in profit, move stop to entry + 0.5 ATR
        if not self._breakeven_set:
            if trade.direction == "LONG":
                pnl_atr = (price - trade.entry_price) / atr
                if pnl_atr >= 2.0:
                    be_stop = trade.entry_price + atr * 0.5
                    if be_stop > (self._trailing_stop or 0):
                        self._trailing_stop = be_stop
                        trade.stop_price = be_stop
                    self._breakeven_set = True
            elif trade.direction == "SHORT":
                pnl_atr = (trade.entry_price - price) / atr
                if pnl_atr >= 2.0:
                    be_stop = trade.entry_price - atr * 0.5
                    if be_stop < (self._trailing_stop or float('inf')):
                        self._trailing_stop = be_stop
                        trade.stop_price = be_stop
                    self._breakeven_set = True

        # Time exit: exit unprofitable trades after 12 bars
        if i - self._entry_bar >= 12:
            if trade.direction == "LONG":
                pnl_r = (price - trade.entry_price) / atr
                if pnl_r < 0.5:
                    self._last_exit_bar = i
                    return "TIME_EXIT"
            elif trade.direction == "SHORT":
                pnl_r = (trade.entry_price - price) / atr
                if pnl_r < 0.5:
                    self._last_exit_bar = i
                    return "TIME_EXIT"

        # Trailing stop — V14: per-asset trail width
        TRAIL_BASE = {"BTC": 2.5, "ETH": 2.5, "SOL": 2.5, "LINK": 3.0}
        TRAIL_DECAY = {"BTC": 0.08, "ETH": 0.08, "SOL": 0.08, "LINK": 0.06}
        trail_base = TRAIL_BASE.get(self.asset_name, 2.5)
        trail_decay = TRAIL_DECAY.get(self.asset_name, 0.08)

        if trade.direction == "LONG":
            if price > (self._best_price or price):
                self._best_price = price
                pnl_r = (price - trade.entry_price) / atr
                trail = max(1.0, trail_base - pnl_r * trail_decay)
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
                trail = max(1.0, trail_base - pnl_r * trail_decay)
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
