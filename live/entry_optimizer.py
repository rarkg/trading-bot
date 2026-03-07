"""
Entry Optimizer — V2.6 smart limit entries.

Instead of market-ordering at signal price, places a limit order at a pullback:
  - LONG: entry_price - 0.3 * ATR
  - SHORT: entry_price + 0.3 * ATR

Order expires after 1 hour (next candle). If not filled, signal is skipped.

For backtesting: checks if the next bar's low (for longs) or high (for shorts)
reached the limit price within the expiry window.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
import pandas as pd


@dataclass
class PendingEntry:
    """A pending limit order waiting to be filled."""
    asset: str
    strategy: str
    direction: str
    limit_price: float
    signal: dict
    created_at: datetime
    expiry_at: datetime
    order_id: Optional[str] = None  # Exchange order ID if placed

    @property
    def key(self) -> tuple:
        return (self.asset, self.strategy)


class EntryOptimizer:
    """Manages smart limit entries with pullback pricing and expiry."""

    DEFAULT_PULLBACK_ATR = 0.3  # Pullback as fraction of ATR
    DEFAULT_EXPIRY_HOURS = 1    # Cancel unfilled orders after 1 hour

    def __init__(
        self,
        enabled: bool = True,
        pullback_atr: float = 0.3,
        expiry_hours: int = 1,
    ):
        self.enabled = enabled
        self.pullback_atr = pullback_atr
        self.expiry_hours = expiry_hours
        self.pending: dict[tuple, PendingEntry] = {}

    def compute_limit_price(
        self,
        direction: str,
        signal_price: float,
        atr: float,
    ) -> float:
        """Calculate the limit order price with pullback.

        Args:
            direction: "LONG" or "SHORT".
            signal_price: Price at signal generation.
            atr: Current ATR value.

        Returns:
            Limit price with pullback applied.
        """
        if not self.enabled:
            return signal_price

        offset = self.pullback_atr * atr
        if direction == "LONG":
            return signal_price - offset
        else:
            return signal_price + offset

    def create_pending(
        self,
        asset: str,
        strategy: str,
        direction: str,
        limit_price: float,
        signal: dict,
        now: Optional[datetime] = None,
    ) -> PendingEntry:
        """Create a pending limit entry."""
        if now is None:
            now = datetime.now(timezone.utc)

        entry = PendingEntry(
            asset=asset,
            strategy=strategy,
            direction=direction,
            limit_price=limit_price,
            signal=signal,
            created_at=now,
            expiry_at=now + timedelta(hours=self.expiry_hours),
        )
        self.pending[entry.key] = entry
        return entry

    def check_fill(
        self,
        asset: str,
        strategy: str,
        current_high: float,
        current_low: float,
        now: Optional[datetime] = None,
    ) -> Optional[PendingEntry]:
        """Check if a pending entry was filled by current price action.

        Returns the PendingEntry if filled, None otherwise.
        Expired entries are automatically removed.
        """
        key = (asset, strategy)
        if key not in self.pending:
            return None

        entry = self.pending[key]
        if now is None:
            now = datetime.now(timezone.utc)

        # Check expiry
        if now >= entry.expiry_at:
            del self.pending[key]
            return None

        # Check fill
        filled = False
        if entry.direction == "LONG" and current_low <= entry.limit_price:
            filled = True
        elif entry.direction == "SHORT" and current_high >= entry.limit_price:
            filled = True

        if filled:
            del self.pending[key]
            return entry

        return None

    def cancel_pending(self, asset: str, strategy: str) -> Optional[PendingEntry]:
        """Cancel a pending entry and return it."""
        key = (asset, strategy)
        return self.pending.pop(key, None)

    def expire_all(self, now: Optional[datetime] = None) -> list[PendingEntry]:
        """Remove all expired pending entries. Returns list of expired."""
        if now is None:
            now = datetime.now(timezone.utc)
        expired = []
        keys_to_remove = []
        for key, entry in self.pending.items():
            if now >= entry.expiry_at:
                expired.append(entry)
                keys_to_remove.append(key)
        for k in keys_to_remove:
            del self.pending[k]
        return expired


class EntryOptimizerBacktest:
    """Entry optimizer for backtesting — checks next-bar fill."""

    def __init__(self, enabled: bool = True, pullback_atr: float = 0.3):
        self.enabled = enabled
        self.pullback_atr = pullback_atr

    def compute_limit_price(self, direction: str, signal_price: float, atr: float) -> float:
        if not self.enabled:
            return signal_price
        offset = self.pullback_atr * atr
        if direction == "LONG":
            return signal_price - offset
        else:
            return signal_price + offset

    def check_fill_next_bar(
        self,
        direction: str,
        limit_price: float,
        next_bar_high: float,
        next_bar_low: float,
    ) -> bool:
        """Check if next bar would fill the limit order.

        Args:
            direction: "LONG" or "SHORT".
            limit_price: The limit order price.
            next_bar_high: High of next bar.
            next_bar_low: Low of next bar.

        Returns:
            True if the limit would have been filled.
        """
        if not self.enabled:
            return True

        if direction == "LONG":
            return next_bar_low <= limit_price
        else:
            return next_bar_high >= limit_price
