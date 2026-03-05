"""
Multi-Asset Momentum Rotation (V9 approach).
Core idea: rank assets by momentum each hour, always trade the strongest/weakest.

Logic:
- Compute short/long EMA ratio for BTC, ETH, SOL
- The asset with highest ratio = strongest momentum = LONG candidate
- The asset with lowest ratio = weakest momentum = SHORT candidate
- Only trade when momentum is decisive (clear leader/laggard)
- This generates many more high-quality trades than any single-asset signal

This is different from all previous strategies:
- Uses RELATIVE momentum, not absolute
- Always has something to trade
- Natural regime adaptation (relative strength shifts)
"""

import numpy as np
import pandas as pd
from backtest.engine import Trade


class MomentumRotation:
    """
    Single-strategy object that handles multi-asset rotation.
    Requires pre-loaded data for all assets.
    """

    def __init__(self, fast_period=24, slow_period=168, score_threshold=0.3,
                 fixed_leverage=None):
        """
        Args:
            fast_period: Fast EMA period in hours (24 = 1 day)
            slow_period: Slow EMA period in hours (168 = 1 week)
            score_threshold: Min momentum score difference to enter
            fixed_leverage: If set, use fixed leverage
        """
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.score_threshold = score_threshold
        self.fixed_leverage = fixed_leverage
        self._asset_ind = {}
        self._last_exit_bar = -12
        self._entry_bar = -1

    def precompute_all(self, asset_data: dict):
        """Precompute indicators for all assets."""
        for sym, data in asset_data.items():
            self._precompute_asset(sym, data)

    def _precompute_asset(self, sym, data):
        closes = data["close"].astype(float)
        highs = data["high"].astype(float)
        lows = data["low"].astype(float)
        volumes = data["volume"].astype(float)

        fast_ema = closes.ewm(span=self.fast_period, adjust=False).mean()
        slow_ema = closes.ewm(span=self.slow_period, adjust=False).mean()

        # Momentum ratio: how far fast is above/below slow (in %)
        momentum_ratio = (fast_ema - slow_ema) / slow_ema * 100

        # Rate of change
        roc_24 = closes.pct_change(24) * 100  # 24h return
        roc_72 = closes.pct_change(72) * 100  # 3-day return

        # ATR
        prev_close = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_close).abs(),
            (lows - prev_close).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(14).mean()

        # Volume
        vol_avg = volumes.rolling(20).mean()
        vol_ratio = volumes / vol_avg.replace(0, 1)

        # RSI
        delta = closes.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, 1e-10)
        rsi = 100 - (100 / (1 + rs))

        # Daily trend
        ema_d_fast = closes.ewm(span=192, adjust=False).mean()
        ema_d_slow = closes.ewm(span=504, adjust=False).mean()
        ema_d_trend = closes.ewm(span=1320, adjust=False).mean()

        # Volatility regime (realized vol)
        returns = closes.pct_change()
        realized_vol = returns.rolling(24).std() * 100  # % daily vol

        self._asset_ind[sym] = pd.DataFrame({
            "close": closes, "high": highs, "low": lows,
            "momentum_ratio": momentum_ratio,
            "roc_24": roc_24, "roc_72": roc_72,
            "fast_ema": fast_ema, "slow_ema": slow_ema,
            "atr": atr, "vol_ratio": vol_ratio, "rsi": rsi,
            "realized_vol": realized_vol,
            "ema_d_fast": ema_d_fast,
            "ema_d_slow": ema_d_slow,
            "ema_d_trend": ema_d_trend,
        }, index=data.index)

    def _trend(self, sym, i):
        ind = self._asset_ind[sym].iloc[i]
        if ind["ema_d_fast"] > ind["ema_d_slow"] > ind["ema_d_trend"]:
            return "UP"
        elif ind["ema_d_fast"] < ind["ema_d_slow"] < ind["ema_d_trend"]:
            return "DOWN"
        return "FLAT"

    def get_signals(self, asset_data: dict, i: int) -> list:
        """
        Returns list of signals sorted by confidence.
        Each signal: (symbol, action, stop, target, leverage, score)
        """
        if i < 1400:
            return []

        if i - self._last_exit_bar < 8:
            return []

        # Get momentum scores for all available assets
        asset_scores = {}
        for sym in self._asset_ind:
            ind = self._asset_ind[sym]
            if i >= len(ind):
                continue
            row = ind.iloc[i]
            if pd.isna(row["momentum_ratio"]) or pd.isna(row["atr"]):
                continue

            asset_scores[sym] = {
                "momentum": float(row["momentum_ratio"]),
                "roc_24": float(row["roc_24"]),
                "roc_72": float(row["roc_72"]),
                "atr": float(row["atr"]),
                "rsi": float(row["rsi"]),
                "close": float(row["close"]),
                "vol_ratio": float(row["vol_ratio"]),
                "realized_vol": float(row["realized_vol"]) if not pd.isna(row["realized_vol"]) else 3.0,
                "trend": self._trend(sym, i),
            }

        if len(asset_scores) < 2:
            return []

        # Sort by momentum ratio
        sorted_assets = sorted(asset_scores.items(), key=lambda x: x[1]["momentum"], reverse=True)
        strongest = sorted_assets[0]
        weakest = sorted_assets[-1]

        signals = []

        # LONG signal: strongest asset with positive momentum + trend confirmation
        sym, data = strongest
        if (data["momentum"] > self.score_threshold and
            data["trend"] in ("UP", "FLAT") and
            data["rsi"] < 75 and
            data["rsi"] > 45):

            score = 50
            if data["trend"] == "UP":
                score += 20
            if data["momentum"] > 1.0:
                score += 15
            elif data["momentum"] > 0.5:
                score += 8
            if data["roc_24"] > 2:
                score += 10
            if data["vol_ratio"] > 1.3:
                score += 5

            # Volatility-adjusted leverage
            vol = max(data["realized_vol"], 1.0)
            target_vol = 2.5  # Target 2.5% daily vol exposure
            vol_scale = min(target_vol / vol, 2.0)

            if self.fixed_leverage:
                lev = self.fixed_leverage
            else:
                base_lev = 1.5 + (score - 50) / 100 * 3.0
                lev = min(5.0, max(1.5, base_lev * vol_scale))

            atr = data["atr"]
            price = data["close"]

            signals.append({
                "symbol": sym,
                "action": "LONG",
                "signal": f"ROT_L({sym},m{data['momentum']:.1f},s{score})",
                "stop": price - atr * 2.5,
                "target": price + atr * 10,
                "leverage": lev,
                "score": score,
            })

        # SHORT signal: weakest asset with negative momentum
        sym, data = weakest
        if (data["momentum"] < -self.score_threshold and
            data["trend"] in ("DOWN", "FLAT") and
            data["rsi"] > 25 and
            data["rsi"] < 55):

            score = 50
            if data["trend"] == "DOWN":
                score += 20
            if data["momentum"] < -1.0:
                score += 15
            elif data["momentum"] < -0.5:
                score += 8
            if data["roc_24"] < -2:
                score += 10
            if data["vol_ratio"] > 1.3:
                score += 5

            vol = max(data["realized_vol"], 1.0)
            target_vol = 2.5
            vol_scale = min(target_vol / vol, 2.0)

            if self.fixed_leverage:
                lev = self.fixed_leverage
            else:
                base_lev = 1.5 + (score - 50) / 100 * 3.0
                lev = min(5.0, max(1.5, base_lev * vol_scale))

            atr = data["atr"]
            price = data["close"]

            signals.append({
                "symbol": sym,
                "action": "SHORT",
                "signal": f"ROT_S({sym},m{data['momentum']:.1f},s{score})",
                "stop": price + atr * 2.5,
                "target": price - atr * 10,
                "leverage": lev,
                "score": score,
            })

        return signals

    def mark_exit(self, i):
        self._last_exit_bar = i

    def check_exit_for_asset(self, sym, i, trade):
        if sym not in self._asset_ind or i >= len(self._asset_ind[sym]):
            return None

        ind = self._asset_ind[sym].iloc[i]
        price = float(ind["close"])
        trend = self._trend(sym, i)

        # Exit when momentum reverses (fast EMA crosses slow EMA back)
        momentum = float(ind["momentum_ratio"])

        if trade.direction == "LONG":
            if momentum < 0:  # Trend reversed
                self.mark_exit(i)
                return "MOM_REVERSE"
            if trend == "DOWN":
                self.mark_exit(i)
                return "TREND_FLIP"

        elif trade.direction == "SHORT":
            if momentum > 0:
                self.mark_exit(i)
                return "MOM_REVERSE"
            if trend == "UP":
                self.mark_exit(i)
                return "TREND_FLIP"

        return None
