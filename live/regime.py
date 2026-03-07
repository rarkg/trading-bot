"""
Regime Detection — V2.5

Classifies market regime per asset using:
  - EMA slope (50-period, normalized by ATR)
  - ADX level (trend strength)
  - ATR percentile (volatility regime)

Three regimes: TRENDING_UP, TRENDING_DOWN, RANGING.
Volatility overlay adjusts sizing independently.
"""

import numpy as np
import talib
from typing import Optional


class RegimeState:
    """Result of regime detection for one asset at one point in time."""

    __slots__ = ("regime", "volatility_multiplier", "_direction_mults")

    def __init__(self, regime: str, vol_mult: float,
                 long_mult: float, short_mult: float):
        self.regime = regime                  # "TRENDING_UP", "TRENDING_DOWN", "RANGING"
        self.volatility_multiplier = vol_mult # 0.8 - 1.0
        self._direction_mults = {"LONG": long_mult, "SHORT": short_mult}

    def direction_multiplier(self, direction: str) -> float:
        """Get sizing multiplier for a given trade direction."""
        return self._direction_mults.get(direction, 1.0)

    def __repr__(self) -> str:
        return (f"RegimeState({self.regime}, vol={self.volatility_multiplier:.2f}, "
                f"L={self._direction_mults['LONG']:.2f}, S={self._direction_mults['SHORT']:.2f})")


# Default (neutral) state — used when not enough data
_NEUTRAL = RegimeState("NEUTRAL", 1.0, 1.0, 1.0)


class RegimeDetector:
    """Detects market regime from OHLCV DataFrame."""

    # EMA slope thresholds (normalized by ATR)
    SLOPE_TREND_THRESH = 0.5
    SLOPE_RANGE_THRESH = 0.3

    # ADX thresholds
    ADX_TREND = 25
    ADX_RANGE = 20

    # Volatility percentile thresholds
    VOL_HIGH_PCT = 80
    VOL_LOW_PCT = 20
    VOL_LOOKBACK = 100

    # Sizing multipliers
    FAVOR_MULT = 1.2     # boost favored direction
    RANGING_MULT = 0.8   # reduce in chop
    HIGH_VOL_MULT = 0.8  # reduce in high vol
    LOW_VOL_MULT = 0.9   # reduce in low vol

    # Min score penalty for counter-trend signals
    SCORE_PENALTY = 1     # +1 to min_score for counter-trend

    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def detect(self, df, i: int) -> RegimeState:
        """Detect regime at bar index i.

        Args:
            df: DataFrame with OHLCV columns (lowercase).
            i: Current bar index.

        Returns:
            RegimeState with regime, direction multipliers, and vol multiplier.
        """
        if not self.enabled:
            return _NEUTRAL

        if i < 60:
            return _NEUTRAL

        c = df["close"].values.astype(float)
        h = df["high"].values.astype(float)
        l = df["low"].values.astype(float)

        # --- EMA slope ---
        ema50 = talib.EMA(c, timeperiod=50)
        atr14 = talib.ATR(h, l, c, timeperiod=14)

        if np.isnan(ema50[i]) or np.isnan(atr14[i]) or atr14[i] <= 0:
            return _NEUTRAL

        # Slope over last 10 bars, normalized by ATR
        if i < 10:
            return _NEUTRAL
        slope = (ema50[i] - ema50[i - 10]) / atr14[i]

        # --- ADX ---
        adx = talib.ADX(h, l, c, timeperiod=14)
        adx_val = adx[i] if not np.isnan(adx[i]) else 0.0

        # --- Classify regime ---
        regime, long_mult, short_mult = self._classify(slope, adx_val)

        # --- Volatility overlay ---
        vol_mult = self._volatility_overlay(atr14, i)

        return RegimeState(regime, vol_mult, long_mult, short_mult)

    def _classify(self, slope: float, adx: float):
        """Classify regime and return (regime_name, long_mult, short_mult)."""
        # TRENDING_UP: strong upward slope + trending ADX
        if slope > self.SLOPE_TREND_THRESH and adx > self.ADX_TREND:
            return ("TRENDING_UP", self.FAVOR_MULT, 1.0)

        # TRENDING_DOWN: strong downward slope + trending ADX
        if slope < -self.SLOPE_TREND_THRESH and adx > self.ADX_TREND:
            return ("TRENDING_DOWN", 1.0, self.FAVOR_MULT)

        # RANGING: low ADX or small slope
        if adx < self.ADX_RANGE or abs(slope) < self.SLOPE_RANGE_THRESH:
            return ("RANGING", self.RANGING_MULT, self.RANGING_MULT)

        # Default: neutral (between thresholds)
        return ("NEUTRAL", 1.0, 1.0)

    def _volatility_overlay(self, atr: np.ndarray, i: int) -> float:
        """Compute volatility multiplier based on ATR percentile."""
        start = max(0, i - self.VOL_LOOKBACK)
        window = atr[start:i]
        window = window[~np.isnan(window)]

        if len(window) < 20:
            return 1.0

        pct = np.sum(window <= atr[i]) / len(window) * 100

        if pct > self.VOL_HIGH_PCT:
            return self.HIGH_VOL_MULT
        elif pct < self.VOL_LOW_PCT:
            return self.LOW_VOL_MULT
        return 1.0

    def get_score_adjustment(self, regime_state: RegimeState, direction: str) -> int:
        """Return min_score adjustment for counter-trend signals.

        Returns 0 for favored/neutral, +1 for counter-trend.
        """
        if not self.enabled:
            return 0

        regime = regime_state.regime
        if regime == "TRENDING_UP" and direction == "SHORT":
            return self.SCORE_PENALTY
        if regime == "TRENDING_DOWN" and direction == "LONG":
            return self.SCORE_PENALTY
        return 0
