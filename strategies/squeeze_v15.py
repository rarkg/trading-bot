"""
V15.5 — Incremental Adaptive Strategy.
Based on V15.4 static params. Only 3 params are adaptive (most sensitive to regime drift):
1. Kelly fraction — recalc from rolling win_rate * avg_win/avg_loss per direction
2. Regime multipliers (trans_mult, sw_mult) — scale proportionally to rolling P&L per regime
3. BO stop ATR — widen if >60% BO trades exit via STOP, tighten if <30%

Everything else stays STATIC at V15.4 hand-tuned values.
"""

import numpy as np
import pandas as pd
import talib


# ============================================================
# STATIC CONFIG — these never change adaptively
# ============================================================

ASSET_DIRECTION_FILTER = {
    "BTC": {"bear_long": False},
    "ETH": {"bull_short": False, "breakout_short": False},
    "SOL": {"bull_short": False},
    "LINK": {"bull_long": False, "breakout_short": False},
}

ASSET_MIN_LEVERAGE = {
    "BTC": 3.0, "ETH": 2.0, "SOL": 3.0, "LINK": 7.0,  # V15.2: BTC 6→3, ETH 3.5→2
}

ASSET_MAX_LEVERAGE = {
    "BTC": 14.0, "ETH": 5.5, "SOL": 8.0, "LINK": 14.5,  # V15.5: LINK 12.8→14.5
}

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

VWAP_BOUNCE_ASSETS = {"ETH", "LINK"}


# ============================================================
# V14.3 BASELINE DEFAULTS (proven starting point)
# ============================================================

V14_DEFAULTS = {
    "BTC": {
        "kelly_fraction": 0.75, "mr_rsi_long": 38, "mr_rsi_short": 62,
        "bb_period": 16, "trans_mult": 4.0, "sw_mult": 2.0,
        "mr_target_ext": 0.85, "bo_stop_atr": 2.5, "bo_target_atr": 20,
        "good_hours": {22, 20, 8, 21, 15, 14, 10},
        "bad_hours": {23, 19, 13, 1, 16},
        "trans_bo_min_score": 65, "mr_stop_atr": 1.2,
        "default_leverage": 4.0, "pyramid_thresh": 3.0, "pyramid_pct": 50,
        "max_pyramids": 1,
    },
    "ETH": {  # V15.4 converged values
        "kelly_fraction": 0.524, "mr_rsi_long": 36, "mr_rsi_short": 64,
        "bb_period": 18, "trans_mult": 2.0, "sw_mult": 2.0,
        "mr_target_ext": 0.85, "bo_stop_atr": 1.837, "bo_target_atr": 12,
        "good_hours": {13, 18, 20, 21, 23},
        "bad_hours": {1, 4, 5, 9, 12},
        "trans_bo_min_score": 65, "mr_stop_atr": 1.114,
        "default_leverage": 5.288, "pyramid_thresh": 1.55, "pyramid_pct": 75,
        "max_pyramids": 2,
    },
    "SOL": {
        "kelly_fraction": 0.55, "mr_rsi_long": 38, "mr_rsi_short": 62,
        "bb_period": 20, "trans_mult": 2.0, "sw_mult": 2.0,
        "mr_target_ext": 0.7, "bo_stop_atr": 1.5, "bo_target_atr": 20,
        "good_hours": {22, 3, 11, 4, 9, 5, 10, 7, 18, 0, 2},
        "bad_hours": {16, 13, 19, 23, 12},
        "trans_bo_min_score": 55, "mr_stop_atr": 1.0,
        "default_leverage": 3.0, "pyramid_thresh": 2.0, "pyramid_pct": 50,
        "max_pyramids": 2,
    },
    "LINK": {  # V15.5: kelly 1.0, mults 3.0, max_lev 14.5 (leverage-capped asset)
        "kelly_fraction": 1.0, "mr_rsi_long": 35, "mr_rsi_short": 65,
        "bb_period": 16, "trans_mult": 3.0, "sw_mult": 3.0,
        "mr_target_ext": 1.10, "bo_stop_atr": 2.5, "bo_target_atr": 12,
        "good_hours": {22, 3, 9, 5, 4, 20, 21, 7},
        "bad_hours": {17, 13, 16, 1, 8, 0, 12, 19, 23},
        "trans_bo_min_score": 55, "mr_stop_atr": 1.5,
        "default_leverage": 15.0, "pyramid_thresh": 2.0, "pyramid_pct": 75,
        "max_pyramids": 2,
    },
}


# ============================================================
# ADAPTIVE PARAMETER MANAGER
# ============================================================

class AdaptiveParameterManager:
    """V15.5 — Incremental adaptive. Only 3 params adapt; rest stay static at V15.4 values.

    Adaptive params (trade-outcome-driven, all assets):
    1. Kelly fraction — from rolling win_rate * avg_win/avg_loss per direction
    2. Regime multipliers (trans_mult, sw_mult) — proportional to rolling regime P&L
    3. BO stop ATR — widen if too many stops, tighten if few

    Recalibration: every 20 completed trades (trade-driven, not bar-driven).
    """

    # Max drift from V14 baseline (±fraction of baseline value)
    MAX_DRIFT = {
        "kelly_fraction": 0.25,
        "trans_mult": 0.30,
        "sw_mult": 0.30,
        "bo_stop_atr": 0.25,
    }

    # Dampening: blend new value with old (0.20 = 20% new, 80% old)
    BLEND_FACTOR = 0.20

    def __init__(self, asset_name):
        self.asset_name = asset_name

        # Load V14 defaults or median for unknown assets
        if asset_name in V14_DEFAULTS:
            self.params = dict(V14_DEFAULTS[asset_name])
        else:
            self.params = self._median_defaults()

        # Copy sets so they're mutable
        self.params["good_hours"] = set(self.params["good_hours"])
        self.params["bad_hours"] = set(self.params["bad_hours"])

        # Trade history for recalibration
        self._trades = []           # (direction, pnl_pct, signal_type, regime, bar, hour)
        self._regime_pnl = {}       # regime -> [pnl_pct]
        self._bo_trades = []        # (pnl_pct,) for BO-type trades
        self._last_recalib_trade_count = 0
        self._param_log = []        # [(bar, param_name, old_val, new_val)]

    def _median_defaults(self):
        """For unknown assets, use median of all known asset defaults."""
        all_vals = list(V14_DEFAULTS.values())
        result = {}
        for key in all_vals[0]:
            if key in ("good_hours", "bad_hours"):
                result[key] = set(V14_DEFAULTS["BTC"][key])
            elif isinstance(all_vals[0][key], (int, float)):
                vals = [d[key] for d in all_vals]
                result[key] = float(np.median(vals))
                if isinstance(all_vals[0][key], int):
                    result[key] = int(round(result[key]))
        return result

    def get(self, param):
        return self.params[param]

    def record_trade(self, direction, pnl_pct, signal_type, regime, bar, hour,
                     exit_reason=None):
        self._trades.append((direction, pnl_pct, signal_type, regime, bar, hour))

        if regime not in self._regime_pnl:
            self._regime_pnl[regime] = []
        self._regime_pnl[regime].append(pnl_pct)

        # Track BO trade outcomes for bo_stop_atr adaptation
        if signal_type in ("breakout", "vwap_bounce", "momentum"):
            self._bo_trades.append(pnl_pct)

    def record_pyramid(self, success):
        pass  # Pyramids stay static in V15.5

    def cache_price_data(self, closes, highs, lows):
        pass  # No price-based recalibration in V15.5

    def is_signal_disabled(self, signal_type):
        return False  # No adaptive signal disabling in V15.5

    def should_recalibrate(self, bar):
        # Trade-driven: recalibrate every 20 completed trades
        return len(self._trades) >= self._last_recalib_trade_count + 20

    def recalibrate(self, bar):
        """Run only the 3 adaptive recalibrations."""
        self._last_recalib_trade_count = len(self._trades)
        self._recalib_kelly(bar)
        self._recalib_regime_mults(bar)
        self._recalib_bo_stop_atr(bar)

    def _set_param(self, bar, name, new_val):
        """Set parameter with dampening and drift-bounded from V14 baseline."""
        old_val = self.params[name]
        if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
            new_val = old_val * (1 - self.BLEND_FACTOR) + new_val * self.BLEND_FACTOR
            # Enforce max drift from V14 baseline
            if name in self.MAX_DRIFT:
                defaults = V14_DEFAULTS.get(self.asset_name, {})
                if name in defaults:
                    baseline = defaults[name]
                    drift = self.MAX_DRIFT[name]
                    if isinstance(baseline, (int, float)) and baseline != 0:
                        lo = baseline * (1 - drift)
                        hi = baseline * (1 + drift)
                        new_val = max(lo, min(hi, new_val))
            if isinstance(self.params.get(name), int):
                new_val = int(round(new_val))
            else:
                new_val = round(new_val, 3)
        if old_val != new_val:
            self.params[name] = new_val
            self._param_log.append((bar, name, old_val, new_val))

    # --- 1. Kelly fraction ---
    def _recalib_kelly(self, bar):
        """Scale kelly relative to baseline: compare recent edge vs full-history edge."""
        if len(self._trades) < 25:
            return
        baseline = V14_DEFAULTS.get(self.asset_name, {}).get("kelly_fraction", 0.6)

        def _edge(trades):
            wins = [t for t in trades if t[1] > 0]
            losses = [t for t in trades if t[1] <= 0]
            if not wins or not losses:
                return None
            wr = len(wins) / len(trades)
            avg_w = np.mean([t[1] for t in wins])
            avg_l = abs(np.mean([t[1] for t in losses]))
            if avg_l == 0:
                return None
            return wr * avg_w - (1 - wr) * avg_l

        full_edge = _edge(self._trades)
        recent_edge = _edge(self._trades[-25:])
        if full_edge is None or recent_edge is None or full_edge == 0:
            return

        # Scale factor: how much better/worse is recent vs full history
        ratio = recent_edge / abs(full_edge)
        # Clamp scale: 0.85x to 1.15x of baseline
        scale = max(0.85, min(1.15, 0.5 + 0.5 * ratio))
        new_frac = baseline * scale
        self._set_param(bar, "kelly_fraction", round(new_frac, 3))

    # --- 2. Regime multipliers ---
    def _recalib_regime_mults(self, bar):
        """Scale regime multipliers proportionally to rolling P&L edge per regime."""
        trans_pnl = self._regime_pnl.get("TRANSITION", [])
        sw_pnl = self._regime_pnl.get("SIDEWAYS", [])

        if len(trans_pnl) >= 10:
            recent = trans_pnl[-30:]
            avg_pnl = np.mean(recent)
            baseline = V14_DEFAULTS.get(self.asset_name, {}).get("trans_mult", 2.0)
            # Scale: positive edge → boost, negative → reduce
            if avg_pnl > 1.0:
                scale = 1.0 + min(0.3, avg_pnl * 0.05)
            elif avg_pnl < -1.0:
                scale = max(0.7, 1.0 + avg_pnl * 0.05)
            else:
                scale = 1.0
            new_mult = baseline * scale
            self._set_param(bar, "trans_mult", round(new_mult, 2))

        if len(sw_pnl) >= 10:
            recent = sw_pnl[-30:]
            avg_pnl = np.mean(recent)
            baseline = V14_DEFAULTS.get(self.asset_name, {}).get("sw_mult", 2.0)
            if avg_pnl > 1.0:
                scale = 1.0 + min(0.3, avg_pnl * 0.05)
            elif avg_pnl < -1.0:
                scale = max(0.7, 1.0 + avg_pnl * 0.05)
            else:
                scale = 1.0
            new_mult = baseline * scale
            self._set_param(bar, "sw_mult", round(new_mult, 2))

    # --- 3. BO stop ATR ---
    def _recalib_bo_stop_atr(self, bar):
        """Adjust BO stop based on win rate of recent BO trades.
        Low win rate → widen stop (stops too tight). High win rate → tighten slightly."""
        if len(self._bo_trades) < 15:
            return
        recent = self._bo_trades[-20:]
        wins = sum(1 for p in recent if p > 0)
        win_rate = wins / len(recent)
        baseline = V14_DEFAULTS.get(self.asset_name, {}).get("bo_stop_atr", 2.0)

        if win_rate < 0.30:
            # Low win rate — stops might be too tight, widen
            new_stop = baseline * 1.08
            self._set_param(bar, "bo_stop_atr", round(new_stop, 2))
        elif win_rate > 0.55:
            # High win rate — can tighten slightly
            new_stop = baseline * 0.95
            self._set_param(bar, "bo_stop_atr", round(new_stop, 2))

    def get_param_evolution(self):
        """Return summary of parameter changes for reporting."""
        if not self._param_log:
            return {}
        changes = {}
        for bar, name, old, new in self._param_log:
            if name not in changes:
                changes[name] = {"count": 0, "first_bar": bar, "values": []}
            changes[name]["count"] += 1
            changes[name]["values"].append((bar, old, new))
        return changes


# ============================================================
# V15 STRATEGY
# ============================================================

class SqueezeV15:

    def __init__(self, fixed_leverage=None, btc_data=None,
                 kelly_window=40, asset_name=None,
                 adx_trending=30, adx_sideways=22,
                 ema_slope_threshold=0.4, atr_volatile_pctile=85,
                 bb_width_sideways_ratio=0.9,
                 mr_bb_entry_pct=0.01,
                 bo_rsi_long=(43, 70), bo_rsi_short=(30, 57),
                 bo_vol_dead_low=1.35, bo_vol_dead_high=2.1,
                 bo_atr_max=3.5):
        self.fixed_leverage = fixed_leverage
        self.kelly_window = kelly_window
        self.asset_name = asset_name

        # Adaptive parameter manager
        self.apm = AdaptiveParameterManager(asset_name)

        # Per-asset max leverage from static config
        self.max_leverage = ASSET_MAX_LEVERAGE.get(asset_name, 8.0)

        # Regime thresholds (static)
        self.adx_trending = adx_trending
        self.adx_sideways = adx_sideways
        self.ema_slope_threshold = ema_slope_threshold
        self.atr_volatile_pctile = atr_volatile_pctile / 100.0
        self.bb_width_sideways_ratio = bb_width_sideways_ratio

        # MR entry
        self.mr_bb_entry_pct = mr_bb_entry_pct

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
        self._trade_type = None
        self._breakeven_set = False
        self._pyramid_count = 0
        self._entry_atr = 0
        self._entry_volume = 0

        # Rolling trade history for Kelly
        self._trade_history = []

        # Regime tracking
        self.regime_counts = {"TRENDING": 0, "SIDEWAYS": 0, "VOLATILE": 0, "TRANSITION": 0}
        self.trade_regimes = []

        # Cross-asset momentum
        self._cross_asset_momentum = 0
        self._equity_peak = 1000.0
        self._current_equity = 1000.0
        self._dd_recovery_mode = False

        # Track current trade's signal type for APM
        self._current_signal_type = None
        self._current_regime = None

    def set_btc_data(self, btc_data):
        btc_closes = btc_data["close"].astype(float)
        self._btc_roc = btc_closes.pct_change(24) * 100
        self._btc_roc.index = btc_data.index

    def set_cross_asset_momentum(self, momentum):
        self._cross_asset_momentum = momentum

    def update_equity(self, equity):
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

        # Cache for APM recalibration
        self.apm.cache_price_data(closes, highs, lows)

        # Get current adaptive BB period
        bb_period = self.apm.get("bb_period")

        # EMAs
        ema8 = closes.ewm(span=8, adjust=False).mean()
        ema21 = closes.ewm(span=21, adjust=False).mean()
        ema55 = closes.ewm(span=55, adjust=False).mean()
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

        # Williams %R
        highest_high = highs.rolling(14).max()
        lowest_low = lows.rolling(14).min()
        willr = -100 * (highest_high - closes) / (highest_high - lowest_low).replace(0, 1)

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        # Bollinger Bands (adaptive period)
        bb_mid = closes.rolling(bb_period).mean()
        bb_std = closes.rolling(bb_period).std()
        bb_upper = bb_mid + 2 * bb_std
        bb_lower = bb_mid - 2 * bb_std
        bb_width = (bb_std / bb_mid.replace(0, 1)) * 100
        bb_width_avg = bb_width.rolling(120).mean()
        bb_width_ratio = bb_width / bb_width_avg.replace(0, 1)
        is_squeeze = bb_width < (bb_width_avg * 0.65)

        # Range position
        range_high = highs.rolling(50).max()
        range_low = lows.rolling(50).min()
        range_pct = (closes - range_low) / (range_high - range_low).replace(0, 1)

        # ADX
        adx = self._compute_adx(highs, lows, closes, period=14)

        # Slopes
        d_slope = (ema_d_fast - ema_d_fast.shift(24)) / ema_d_fast.shift(24).replace(0, 1) * 100
        ema21_slope = (ema21 - ema21.shift(5)) / ema21.shift(5).replace(0, 1) * 100

        # Candle stats
        body_ratio = (closes - opens).abs() / (highs - lows).replace(0, 1)
        bullish_int = (closes > opens).astype(int)
        bear_market = (closes < ema_200d).astype(int)

        # TA-Lib patterns
        o, h, l, c = opens.values, highs.values, lows.values, closes.values
        hammer = pd.Series(talib.CDLHAMMER(o, h, l, c), index=data.index)
        engulfing = pd.Series(talib.CDLENGULFING(o, h, l, c), index=data.index)
        morningstar = pd.Series(talib.CDLMORNINGSTAR(o, h, l, c), index=data.index)
        doji = pd.Series(talib.CDLDOJI(o, h, l, c), index=data.index)
        shootingstar = pd.Series(talib.CDLSHOOTINGSTAR(o, h, l, c), index=data.index)
        eveningstar = pd.Series(talib.CDLEVENINGSTAR(o, h, l, c), index=data.index)

        bull_reversal = ((hammer > 0).astype(int) + (engulfing > 0).astype(int) +
                         (morningstar > 0).astype(int) + (doji != 0).astype(int))
        bear_reversal = ((shootingstar > 0).astype(int) + (engulfing < 0).astype(int) +
                         (eveningstar > 0).astype(int) + (doji != 0).astype(int))

        # VWAP
        tp = (highs + lows + closes) / 3
        vwap = (tp * volumes).rolling(24).sum() / volumes.rolling(24).sum().replace(0, 1)
        vwap_dist = (closes - vwap) / vwap.replace(0, 1) * 100

        # 4h MTF
        close_4h = closes.rolling(4).mean()
        ema_4h_fast = close_4h.ewm(span=12, adjust=False).mean()
        ema_4h_slow = close_4h.ewm(span=26, adjust=False).mean()
        ema_4h_slope = (ema_4h_fast - ema_4h_fast.shift(4)) / ema_4h_fast.shift(4).replace(0, 1) * 100

        # Daily RSI
        delta_d = closes.diff(24)
        gain_d = delta_d.where(delta_d > 0, 0).rolling(14 * 24).mean()
        loss_d = (-delta_d.where(delta_d < 0, 0)).rolling(14 * 24).mean()
        rs_d = gain_d / loss_d.replace(0, 1e-10)
        rsi_daily = 100 - (100 / (1 + rs_d))

        mtf_bull = (ema_4h_fast > ema_4h_slow).astype(int)
        atr_ratio = atr / atr.rolling(48).mean().replace(0, 1)
        recent_vol_spike = (atr_ratio.rolling(4).max() > 1.8).astype(int)

        spinningtop = pd.Series(talib.CDLSPINNINGTOP(o, h, l, c), index=data.index)
        weak_candle = ((doji != 0) | (spinningtop != 0)).astype(int)

        # Extended patterns
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
            return self.apm.get("default_leverage")

        recent = dir_trades[-self.kelly_window:]
        wins = [p for p in recent if p > 0]
        losses = [p for p in recent if p <= 0]
        if not wins or not losses:
            return self.apm.get("default_leverage")

        W = len(wins) / len(recent)
        R = abs(np.mean(wins)) / abs(np.mean(losses))
        kelly = W - (1 - W) / R
        if kelly <= 0:
            return 1.5

        leverage = kelly * self.apm.get("kelly_fraction") * 20
        min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
        lev = max(min_lev, min(leverage, self.max_leverage))
        return lev

    def _unified_confidence(self, i, direction, trade_type):
        ind = self._ind.iloc[i]
        score = 50

        ema_4h_slope = float(ind["ema_4h_slope"]) if not pd.isna(ind["ema_4h_slope"]) else 0
        mtf_bull = int(ind["mtf_bull"]) if not pd.isna(ind["mtf_bull"]) else 0
        if direction == "LONG" and mtf_bull == 1 and ema_4h_slope > 0.1:
            score += 15
        elif direction == "SHORT" and mtf_bull == 0 and ema_4h_slope < -0.1:
            score += 15
        elif direction == "LONG" and mtf_bull == 0 and ema_4h_slope < -0.1:
            score -= 10
        elif direction == "SHORT" and mtf_bull == 1 and ema_4h_slope > 0.1:
            score -= 10

        vwap_dist = float(ind["vwap_dist"]) if not pd.isna(ind["vwap_dist"]) else 0
        if direction == "LONG" and vwap_dist > 0.3:
            score += 10
        elif direction == "SHORT" and vwap_dist < -0.3:
            score += 10

        vol_ratio = float(ind["vol_ratio"])
        if vol_ratio > 2.5:
            score += 10
        elif vol_ratio > 1.8:
            score += 5

        vol_spike = int(ind["recent_vol_spike"]) if not pd.isna(ind["recent_vol_spike"]) else 0
        if vol_spike == 1:
            score += 8

        if trade_type == "meanrev":
            if direction == "LONG" and ind["bull_reversal"] >= 1:
                score += 7
            elif direction == "SHORT" and ind["bear_reversal"] >= 1:
                score += 7
        else:
            if ind["body_ratio"] > 0.7:
                score += 7
            elif ind["body_ratio"] > 0.5:
                score += 3

        trend = self._daily_trend(i)
        if trade_type == "breakout":
            if direction == "LONG" and trend == "UP":
                score += 10
            elif direction == "SHORT" and trend == "DOWN":
                score += 10

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

        # Adaptive good/bad hours
        hour = int(ind["hour"]) if not pd.isna(ind["hour"]) else 12
        good_hours = self.apm.get("good_hours")
        bad_hours = self.apm.get("bad_hours")
        if hour in good_hours:
            score += 8
        elif hour in bad_hours:
            score -= 5

        pat_score = self._check_talib_patterns(ind, direction)
        score += pat_score

        if self.asset_name not in ("BTC", "SOL"):
            cam = self._cross_asset_momentum
            if direction == "LONG" and cam > 0.5:
                score += 12
            elif direction == "SHORT" and cam < -0.5:
                score += 12

        score = max(40, min(score, 100))

        if self.asset_name == "SOL":
            return 1.0

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
        else:
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
                    val = ind[col]
                    if pat in ("CDLHIKKAKEMOD", "CDLIDENTICAL3CROWS", "CDLADVANCEBLOCK",
                               "CDLSTALLEDPATTERN", "CDLHIKKAKE", "CDLSEPARATINGLINES"):
                        if val < 0:
                            bonus += 5
                    elif val > 0:
                        bonus += 5
        return min(bonus, 10)

    def _regime_leverage_mult(self, regime, trend, direction):
        if regime == "VOLATILE":
            return 0.5
        if regime == "TRANSITION":
            return self.apm.get("trans_mult")
        if regime == "TRENDING":
            if (direction == "LONG" and trend == "UP") or (direction == "SHORT" and trend == "DOWN"):
                return 1.4
            return 0.5
        # SIDEWAYS
        return self.apm.get("sw_mult")

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
        # Check adaptive direction filter
        if self.apm.is_signal_disabled("breakout"):
            return None

        ind = self._ind.iloc[i]
        price = float(ind["close"])
        atr = float(ind["atr"])
        atr_pct = float(ind["atr_pct"])
        rsi = float(ind["rsi"])
        vol_ratio = float(ind["vol_ratio"])
        range_pct = float(ind["range_pct"]) if not pd.isna(ind["range_pct"]) else 0.5

        if not ind["is_squeeze"]:
            return None
        if ind["weak_candle"] == 1:
            return None
        if atr_pct > self.bo_atr_max:
            return None

        in_good_vol = (vol_ratio <= self.bo_vol_dead_low) or (vol_ratio >= self.bo_vol_dead_high)
        if not in_good_vol:
            return None

        is_bear = ind["bear_market"] == 1
        trend = self._daily_trend(i)

        # Adaptive BO params
        bo_stop_mult = self.apm.get("bo_stop_atr")
        bo_target_mult = self.apm.get("bo_target_atr")

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
                "signal": f"V15_BO_L(s{score},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
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
                "signal": f"V15_BO_S(s{score},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": price + (atr * bo_stop_mult),
                "target": price - (atr * bo_target_mult),
                "leverage": lev,
            }

        return None

    def _try_meanrev(self, data, i, regime):
        if self.apm.is_signal_disabled("meanrev"):
            return None

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

        # Adaptive params
        mr_rsi_long = self.apm.get("mr_rsi_long")
        mr_rsi_short = self.apm.get("mr_rsi_short")
        mr_stop_atr = self.apm.get("mr_stop_atr")
        mr_target_pct = self.apm.get("mr_target_ext")

        # LONG
        if (price <= bb_lower * (1 + self.mr_bb_entry_pct) and
            rsi < mr_rsi_long and
            willr < -75 and
            ind["bullish"] == 1 and
            ind["body_ratio"] > 0.3):

            if not self._direction_allowed("LONG", i, regime):
                return None

            stop = price - (atr * mr_stop_atr)
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
                "signal": f"V15_MR_L(rsi{rsi:.0f},w{willr:.0f},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        # SHORT
        if (price >= bb_upper * (1 - self.mr_bb_entry_pct) and
            rsi > mr_rsi_short and
            willr > -25 and
            ind["bullish"] == 0 and
            ind["body_ratio"] > 0.3):

            if not self._direction_allowed("SHORT", i, regime):
                return None

            stop = price + (atr * mr_stop_atr)
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
                "signal": f"V15_MR_S(rsi{rsi:.0f},w{willr:.0f},k{kelly_lev:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        return None

    def _try_vwap_bounce(self, data, i, regime):
        if self.asset_name not in VWAP_BOUNCE_ASSETS:
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

        if (trend == "UP" and
            mtf_bull == 1 and
            ema_4h_slope > 0.05 and
            -0.3 <= vwap_dist <= 0.3 and
            40 <= rsi <= 55 and
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
            self._trade_type = "breakout"

            return {
                "action": "LONG",
                "signal": f"V15_VB_L(vd{vwap_dist:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

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
                "signal": f"V15_VB_S(vd{vwap_dist:.1f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop,
                "target": target,
                "leverage": lev,
            }

        return None

    def _try_momentum_continuation(self, data, i, regime):
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

        if i < 3:
            return None
        prev_ema8 = [float(self._ind.iloc[i-j]["ema8"]) for j in range(1, 4)]
        prev_ema21 = [float(self._ind.iloc[i-j]["ema21"]) for j in range(1, 4)]

        fresh_bull_cross = (ema8 > ema21 and any(p8 < p21 for p8, p21 in zip(prev_ema8, prev_ema21)))
        if (fresh_bull_cross and
            trend == "UP" and mtf_bull == 1 and ema_4h_slope > 0.1 and
            vol_ratio > 1.3 and 40 <= rsi <= 65 and
            ind["body_ratio"] > 0.4 and ind["bullish"] == 1):

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
                "signal": f"V15_MC_L(rsi{rsi:.0f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop, "target": target, "leverage": lev,
            }

        fresh_bear_cross = (ema8 < ema21 and any(p8 > p21 for p8, p21 in zip(prev_ema8, prev_ema21)))
        if (fresh_bear_cross and
            trend == "DOWN" and mtf_bull == 0 and ema_4h_slope < -0.1 and
            vol_ratio > 1.3 and 35 <= rsi <= 60 and
            ind["body_ratio"] > 0.4 and ind["bullish"] == 0):

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
                "signal": f"V15_MC_S(rsi{rsi:.0f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop, "target": target, "leverage": lev,
            }

        return None

    def _try_overextension_mr(self, data, i, regime):
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

        ultra_long = rsi < 15 and willr < -90
        ultra_short = rsi > 85 and willr > -10

        if (ultra_long and price < bb_lower and
            ind["bullish"] == 1 and ind["body_ratio"] > 0.4):

            if not self._direction_allowed("LONG", i, regime):
                return None

            stop = price - (atr * 1.5)
            target = bb_mid + (bb_upper - bb_mid) * 0.5

            gain_dist = target - price
            loss_dist = price - stop
            if loss_dist <= 0 or gain_dist / loss_dist < 1.0:
                return None

            kelly_lev = self._compute_kelly("LONG")
            conf_mult = self._unified_confidence(i, "LONG", "meanrev")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * 2.0 * conf_mult, self.max_leverage)), 2)

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
                "signal": f"V15_UX_L(rsi{rsi:.0f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop, "target": target, "leverage": lev,
            }

        if (ultra_short and price > bb_upper and
            ind["bullish"] == 0 and ind["body_ratio"] > 0.4):

            if not self._direction_allowed("SHORT", i, regime):
                return None

            stop = price + (atr * 1.5)
            target = bb_mid - (bb_mid - bb_lower) * 0.5

            gain_dist = price - target
            loss_dist = stop - price
            if loss_dist <= 0 or gain_dist / loss_dist < 1.0:
                return None

            kelly_lev = self._compute_kelly("SHORT")
            conf_mult = self._unified_confidence(i, "SHORT", "meanrev")
            min_lev = ASSET_MIN_LEVERAGE.get(self.asset_name, 1.5)
            lev = round(max(min_lev, min(kelly_lev * 2.0 * conf_mult, self.max_leverage)), 2)

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
                "signal": f"V15_UX_S(rsi{rsi:.0f},r{regime[0]},l{lev:.1f}x)",
                "stop": stop, "target": target, "leverage": lev,
            }

        return None

    def generate_signal(self, data, i):
        if self._ind is None:
            self._precompute(data)

        if i < 1400 or i >= len(self._ind):
            return None

        # Adaptive recalibration every 500 bars after warmup
        if self.apm.should_recalibrate(i):
            self.apm.recalibrate(i)

        if i - self._last_exit_bar < 4:
            return None

        ind = self._ind.iloc[i]
        atr = float(ind["atr"])
        if pd.isna(atr) or atr <= 0:
            return None

        regime = self._detect_regime(i)

        ox_signal = self._try_overextension_mr(data, i, regime)
        if ox_signal:
            self.regime_counts[regime] += 1
            self.trade_regimes.append((regime, ox_signal["action"], "overext_mr"))
            self._current_signal_type = "overext_mr"
            self._current_regime = regime
            return ox_signal

        if regime == "TRENDING":
            self.regime_counts["TRENDING"] += 1
            return None

        elif regime == "SIDEWAYS":
            signal = self._try_meanrev(data, i, regime)
            if signal:
                self.regime_counts["SIDEWAYS"] += 1
                self.trade_regimes.append((regime, signal["action"], "meanrev"))
                self._current_signal_type = "meanrev"
                self._current_regime = regime
                return signal
            signal = self._try_breakout(data, i, regime, min_score=60)
            if signal:
                self.regime_counts["SIDEWAYS"] += 1
                self.trade_regimes.append((regime, signal["action"], "breakout"))
                self._current_signal_type = "breakout"
                self._current_regime = regime
                return signal
            if self.asset_name == "LINK":
                signal = self._try_vwap_bounce(data, i, regime)
                if signal:
                    self.regime_counts["SIDEWAYS"] += 1
                    self.trade_regimes.append((regime, signal["action"], "vwap_bounce"))
                    self._current_signal_type = "vwap_bounce"
                    self._current_regime = regime
                    return signal
            return None

        elif regime == "VOLATILE":
            self.regime_counts["VOLATILE"] += 1
            return None

        else:  # TRANSITION
            trans_min = self.apm.get("trans_bo_min_score")
            signal = self._try_breakout(data, i, regime, min_score=trans_min)
            if signal:
                if signal["action"] == "SHORT":
                    s = self._confidence_breakout(i, "SHORT")
                    if s < 70:
                        return None
                self.regime_counts["TRANSITION"] += 1
                self.trade_regimes.append((regime, signal["action"], "breakout"))
                self._current_signal_type = "breakout"
                self._current_regime = regime
                return signal
            signal = self._try_momentum_continuation(data, i, regime)
            if signal:
                self.regime_counts["TRANSITION"] += 1
                self.trade_regimes.append((regime, signal["action"], "momentum"))
                self._current_signal_type = "momentum"
                self._current_regime = regime
                return signal
            signal = self._try_vwap_bounce(data, i, regime)
            if signal:
                self.regime_counts["TRANSITION"] += 1
                self.trade_regimes.append((regime, signal["action"], "vwap_bounce"))
                self._current_signal_type = "vwap_bounce"
                self._current_regime = regime
                return signal
            return None

    def record_trade(self, direction, pnl_pct, exit_reason=None):
        self._trade_history.append((direction, pnl_pct))
        # Feed to APM
        hour = 12  # default
        if self._ind is not None and self._entry_bar >= 0 and self._entry_bar < len(self._ind):
            h = self._ind.iloc[self._entry_bar].get("hour", 12)
            hour = int(h) if not pd.isna(h) else 12
        sig_type = self._current_signal_type or "unknown"
        regime = self._current_regime or "TRANSITION"
        self.apm.record_trade(direction, pnl_pct, sig_type, regime, self._entry_bar, hour,
                              exit_reason=exit_reason)

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

        # Adaptive pyramid config
        pyr_thresh = self.apm.get("pyramid_thresh")
        pyr_pct = self.apm.get("pyramid_pct")
        max_pyramids = self.apm.params.get("max_pyramids", 2)

        if self._pyramid_count < max_pyramids:
            actual_thresh = pyr_thresh * (1 + self._pyramid_count)
            if trade.direction == "LONG":
                pnl_atr = (price - trade.entry_price) / entry_atr
            else:
                pnl_atr = (trade.entry_price - price) / entry_atr
            if pnl_atr >= actual_thresh:
                self._pyramid_count += 1
                self.apm.record_pyramid(True)
                return f"PYRAMID_{int(pyr_pct)}"

        # Breakeven stop
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

        # Time exit
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

        # Trailing stop — per-asset trail width (static)
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
