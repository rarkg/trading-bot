"""
Wick Guard — V2.6 wick-resistant stop logic.

Requires a 15-minute candle CLOSE below the stop level to trigger a stop-loss.
A mere intra-candle wick touch is NOT enough. Take-profits execute immediately.

For backtesting: simulates 15m candles from hourly data by splitting hourly
bars into 4 sub-bars using OHLCV interpolation.
"""

import numpy as np
import pandas as pd
from typing import Optional


class WickGuard:
    """Tracks 15m candle closes to validate stop-loss triggers."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        # Track consecutive 15m closes beyond stop per position key
        self._close_beyond_stop: dict[str, int] = {}

    def should_trigger_stop(
        self,
        position_key: str,
        direction: str,
        stop_price: float,
        candle_close_15m: float,
    ) -> bool:
        """Check if stop should trigger based on 15m candle close.

        Args:
            position_key: Unique key for the position (e.g., "BTC_candle_v2_3").
            direction: "LONG" or "SHORT".
            stop_price: Current stop-loss price.
            candle_close_15m: Close price of the latest 15m candle.

        Returns:
            True if stop should be triggered (15m close beyond stop).
        """
        if not self.enabled:
            return True  # Pass-through when disabled

        if direction == "LONG":
            beyond = candle_close_15m <= stop_price
        else:
            beyond = candle_close_15m >= stop_price

        if beyond:
            self._close_beyond_stop[position_key] = (
                self._close_beyond_stop.get(position_key, 0) + 1
            )
            return True  # One 15m close beyond stop is enough
        else:
            # Reset counter — price recovered
            self._close_beyond_stop[position_key] = 0
            return False

    def should_trigger_tp(
        self,
        direction: str,
        target_price: float,
        current_price: float,
    ) -> bool:
        """Take-profit always executes immediately on tick touch."""
        if direction == "LONG":
            return current_price >= target_price
        else:
            return current_price <= target_price

    def clear(self, position_key: str) -> None:
        """Clear tracking for a closed position."""
        self._close_beyond_stop.pop(position_key, None)


class WickGuardBacktest:
    """Wick guard for backtesting — uses 15m data if available, else simulates."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def check_stop(
        self,
        direction: str,
        stop_price: float,
        bar_15m_closes: list[float],
    ) -> bool:
        """Check if any 15m candle close in this hour breached the stop.

        Args:
            direction: "LONG" or "SHORT".
            stop_price: Stop-loss price.
            bar_15m_closes: List of 15m close prices within the hour (up to 4).

        Returns:
            True if stop should trigger.
        """
        if not self.enabled:
            return True

        for close_15m in bar_15m_closes:
            if direction == "LONG" and close_15m <= stop_price:
                return True
            elif direction == "SHORT" and close_15m >= stop_price:
                return True
        return False

    @staticmethod
    def get_15m_closes_from_hourly(
        hourly_open: float,
        hourly_high: float,
        hourly_low: float,
        hourly_close: float,
    ) -> list[float]:
        """Simulate 4 x 15m closes from a single hourly candle.

        Creates a plausible intra-hour path: open -> extreme1 -> extreme2 -> close.
        This is a rough approximation for backtesting when 15m data isn't available.
        """
        body_up = hourly_close >= hourly_open

        if body_up:
            # Bullish candle: open -> dip to low -> rally to high -> close
            return [
                hourly_open + (hourly_low - hourly_open) * 0.6,   # Q1: dipping
                hourly_low + (hourly_close - hourly_low) * 0.3,   # Q2: bottomed
                hourly_high - (hourly_high - hourly_close) * 0.5, # Q3: rallying
                hourly_close,                                      # Q4: close
            ]
        else:
            # Bearish candle: open -> rally to high -> drop to low -> close
            return [
                hourly_open + (hourly_high - hourly_open) * 0.6,  # Q1: popping
                hourly_high - (hourly_high - hourly_close) * 0.3, # Q2: topped
                hourly_low + (hourly_close - hourly_low) * 0.5,   # Q3: dropping
                hourly_close,                                      # Q4: close
            ]
