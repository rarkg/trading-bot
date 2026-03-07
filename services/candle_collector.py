#!/usr/bin/env python3
"""Candle Collector Service — fetches OHLCV candles and writes to Postgres.

Runs every 5 minutes. Fetches 1h and 15m candles for all assets from Kraken
REST API. Upserts into the `candles` table. Falls back to CSV seed on first
run if table is empty for an asset.

This service has NO trading logic. It only collects data.
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

import pandas as pd

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live.exchange.kraken import KrakenSpotClient, SPOT_PAIRS
from live.data_provider import PostgresDataProvider

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("candle-collector")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ASSETS = ["BTC", "ETH", "SOL", "LINK", "ADA", "AVAX", "DOGE", "XRP", "ICP", "SHIB"]
TIMEFRAMES = ["1h", "15m"]
FETCH_LIMIT = 720  # candles per fetch
LOOP_INTERVAL_SEC = 300  # 5 minutes

# Kraken interval in minutes
KRAKEN_INTERVALS = {"1h": 60, "15m": 15, "4h": 240, "1d": 1440}


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------
class CandleCollector:
    def __init__(self):
        self._shutdown = False
        self.kraken = KrakenSpotClient()
        self.pg = PostgresDataProvider()

    def run(self):
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        log.info("=== Candle Collector starting ===")
        log.info("Assets: %s", ASSETS)
        log.info("Timeframes: %s", TIMEFRAMES)

        # Seed from CSV on first run
        self._seed_all()

        # Initial fetch
        self._collect_all()

        while not self._shutdown:
            time.sleep(LOOP_INTERVAL_SEC)
            if self._shutdown:
                break
            self._collect_all()

        log.info("=== Candle Collector stopped ===")

    def _handle_signal(self, signum, frame):
        log.info("Received signal %d, shutting down...", signum)
        self._shutdown = True

    def _seed_all(self):
        """Seed Postgres from CSV files if empty for each asset+timeframe."""
        log.info("Checking seed status...")
        for asset in ASSETS:
            for tf in TIMEFRAMES:
                try:
                    self.pg.seed_if_empty(asset, tf)
                except Exception:
                    log.warning("Seed failed for %s %s", asset, tf, exc_info=True)

    def _collect_all(self):
        """Fetch candles for all assets and timeframes, upsert to Postgres."""
        start = time.time()
        total_rows = 0

        for asset in ASSETS:
            if asset.upper() not in SPOT_PAIRS:
                log.warning("  %s: no Kraken spot pair mapping, skipping", asset)
                continue

            for tf in TIMEFRAMES:
                count = self._fetch_and_upsert(asset, tf)
                total_rows += count

        elapsed = time.time() - start
        log.info(
            "Collection complete: %d rows upserted across %d assets in %.1fs",
            total_rows, len(ASSETS), elapsed,
        )

    def _fetch_and_upsert(self, asset, timeframe):
        """Fetch candles from Kraken and upsert to Postgres. Returns row count."""
        interval_min = KRAKEN_INTERVALS.get(timeframe)
        if interval_min is None:
            return 0

        pair = SPOT_PAIRS.get(asset.upper())
        if pair is None:
            return 0

        try:
            raw = self.kraken.get_ohlc(pair=pair, interval=interval_min)
        except Exception:
            log.warning("Kraken fetch failed for %s %s", asset, timeframe, exc_info=True)
            return 0

        if not raw:
            log.warning("  %s %s: no candles returned from Kraken", asset, timeframe)
            return 0

        count = 0
        for candle in raw:
            try:
                ts = pd.Timestamp(candle["timestamp"], unit="s", tz="UTC")
                self.pg.ingest_candle(
                    asset=asset.upper(),
                    timeframe=timeframe,
                    timestamp=ts,
                    open_=candle["open"],
                    high=candle["high"],
                    low=candle["low"],
                    close=candle["close"],
                    volume=candle["volume"],
                )
                count += 1
            except Exception:
                pass  # skip bad rows

        log.info("  %s %s: upserted %d candles", asset, timeframe, count)
        return count


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    collector = CandleCollector()
    collector.run()
