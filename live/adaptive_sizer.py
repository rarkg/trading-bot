"""
Adaptive Rolling Kelly Sizing — V2.5

Sizes trades based on recent observed edge per direction bucket (LONG/SHORT).
Uses half-Kelly with exponential decay weighting.

Cold start:
  - First 20 trades: fixed 1.0x
  - Trades 20-50: linear blend between fixed and adaptive
  - After 50: fully adaptive

Bounds: 0.25x - 2.0x of base size.
"""

import math
from typing import Optional, List, Tuple


class TradeRecord:
    """Lightweight record of a completed trade for Kelly calculation."""
    __slots__ = ("direction", "pnl_pct")

    def __init__(self, direction: str, pnl_pct: float):
        self.direction = direction  # "LONG" or "SHORT"
        self.pnl_pct = pnl_pct     # decimal (0.05 = 5%)


class AdaptiveSizer:
    """Rolling Kelly position sizer grouped by direction."""

    WINDOW = 50        # max trades per bucket
    MIN_TRADES = 20    # minimum before adapting
    FLOOR = 0.25       # min multiplier
    CEILING = 2.0      # max multiplier

    def __init__(self, enabled: bool = False):
        self.enabled = enabled
        # Separate buckets for LONG and SHORT
        self._trades = {"LONG": [], "SHORT": []}  # type: dict[str, List[TradeRecord]]

    def record_trade(self, direction: str, pnl_pct: float) -> None:
        """Record a completed trade result. pnl_pct is decimal (0.05 = 5%)."""
        bucket = self._trades.get(direction)
        if bucket is None:
            return
        bucket.append(TradeRecord(direction, pnl_pct))
        # Trim to window
        if len(bucket) > self.WINDOW:
            self._trades[direction] = bucket[-self.WINDOW:]

    def get_multiplier(self, direction: str) -> float:
        """Return sizing multiplier for the given direction. 1.0 = normal."""
        if not self.enabled:
            return 1.0

        bucket = self._trades.get(direction, [])
        n = len(bucket)

        if n < self.MIN_TRADES:
            return 1.0

        # Compute Kelly with exponential decay
        kelly = self._compute_kelly(bucket)
        half_kelly = kelly / 2.0

        # Clamp
        mult = max(self.FLOOR, min(self.CEILING, half_kelly))

        # Cold start blend: linear interpolation from fixed (1.0) to adaptive
        if n < self.WINDOW:
            blend = (n - self.MIN_TRADES) / (self.WINDOW - self.MIN_TRADES)
            mult = 1.0 + blend * (mult - 1.0)

        return mult

    def _compute_kelly(self, trades: List[TradeRecord]) -> float:
        """Compute Kelly fraction with exponential decay weighting.

        f = (WR * avg_win - (1-WR) * avg_loss) / avg_win

        Recent trades weighted 2x vs oldest.
        """
        n = len(trades)
        if n == 0:
            return 1.0

        # Exponential decay weights: newest = 2x oldest
        # weight_i = exp(alpha * i), where alpha = ln(2) / (n-1)
        if n > 1:
            alpha = math.log(2.0) / (n - 1)
        else:
            alpha = 0.0

        total_weight = 0.0
        win_weight = 0.0
        win_pnl_weighted = 0.0
        loss_pnl_weighted = 0.0

        for idx, t in enumerate(trades):
            w = math.exp(alpha * idx)
            total_weight += w
            if t.pnl_pct > 0:
                win_weight += w
                win_pnl_weighted += w * t.pnl_pct
            else:
                loss_pnl_weighted += w * abs(t.pnl_pct)

        if total_weight <= 0:
            return 1.0

        wr = win_weight / total_weight
        avg_win = (win_pnl_weighted / win_weight) if win_weight > 0 else 0.0
        avg_loss = (loss_pnl_weighted / (total_weight - win_weight)) if (total_weight - win_weight) > 0 else 0.001

        if avg_win <= 0:
            return self.FLOOR

        kelly = (wr * avg_win - (1.0 - wr) * avg_loss) / avg_win

        # If Kelly is negative (negative edge), return floor
        if kelly <= 0:
            return self.FLOOR

        return kelly

    @property
    def trade_counts(self) -> dict:
        """Return trade counts per bucket for diagnostics."""
        return {d: len(t) for d, t in self._trades.items()}
