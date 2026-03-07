"""DataProvider interface and implementations for candle data.

Abstracts data source so run_live.py doesn't care whether candles come
from Kraken REST, Postgres, or CSV files.
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

log = logging.getLogger("live.data")


class DataProvider(ABC):
    """Abstract interface for OHLCV candle data."""

    @abstractmethod
    def get_candles(
        self,
        asset: str,
        timeframe: str = "1h",
        limit: int = 720,
    ) -> pd.DataFrame:
        """Fetch OHLCV candles for an asset.

        Args:
            asset: Asset name ("BTC", "ETH", etc.).
            timeframe: Candle interval ("1h", "4h", "1d").
            limit: Maximum number of candles to return.

        Returns:
            DataFrame indexed by UTC timestamp with columns:
            open, high, low, close, volume.
        """

    @abstractmethod
    def get_latest_price(self, asset: str) -> float:
        """Get the most recent close price for an asset.

        Args:
            asset: Asset name ("BTC", "ETH", etc.).

        Returns:
            Latest close price as float, or 0.0 if unavailable.
        """


class KrakenDataProvider(DataProvider):
    """Fetches candles from Kraken REST API (wraps existing LiveFeed)."""

    def __init__(self) -> None:
        from live.feed import LiveFeed
        self._feed = LiveFeed()

    def get_candles(
        self,
        asset: str,
        timeframe: str = "1h",
        limit: int = 720,
    ) -> pd.DataFrame:
        df = self._feed.get_candles(asset, timeframe)
        if len(df) > limit:
            df = df.iloc[-limit:]
        return df

    def get_latest_price(self, asset: str) -> float:
        try:
            df = self._feed.get_candles(asset, "1h")
            if len(df) > 0:
                return float(df.iloc[-1]["close"])
        except Exception:
            log.warning("Failed to get latest price for %s from Kraken", asset)
        return 0.0


class PostgresDataProvider(DataProvider):
    """Reads candles from the Postgres `candles` table.

    On startup, seeds the table from CSV files if empty for an asset.
    Falls back to Kraken if Postgres is unavailable.
    """

    DSN = "postgresql://elio@localhost:5432/trading_monitor"

    # Map timeframe strings to interval labels stored in DB
    _TF_MAP = {"1h": "1h", "4h": "4h", "1d": "1d", "15m": "15m"}

    # CSV filenames per timeframe
    _CSV_SUFFIXES = {"1h": "_USD_hourly.csv", "15m": "_USD_15m.csv"}

    def __init__(
        self,
        dsn: Optional[str] = None,
        data_dir: Optional[str] = None,
        fallback: Optional[DataProvider] = None,
    ) -> None:
        import psycopg2
        self._psycopg2 = psycopg2
        self.dsn = dsn or self.DSN
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
        )
        self._fallback = fallback
        self._conn = None
        self._connect()
        self._ensure_table()

    def _connect(self) -> None:
        try:
            self._conn = self._psycopg2.connect(self.dsn)
            self._conn.autocommit = True
            log.info("PostgresDataProvider connected to %s", self.dsn)
        except Exception:
            log.warning("PostgresDataProvider: Postgres unavailable", exc_info=True)
            self._conn = None

    def _ensure_conn(self) -> bool:
        if self._conn is not None:
            try:
                with self._conn.cursor() as cur:
                    cur.execute("SELECT 1")
                return True
            except Exception:
                self._conn = None
        self._connect()
        return self._conn is not None

    def _ensure_table(self) -> None:
        """Create the candles table if it doesn't exist."""
        if not self._ensure_conn():
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS candles (
                        id SERIAL PRIMARY KEY,
                        asset VARCHAR(10) NOT NULL,
                        timeframe VARCHAR(5) NOT NULL,
                        timestamp TIMESTAMPTZ NOT NULL,
                        open DOUBLE PRECISION NOT NULL,
                        high DOUBLE PRECISION NOT NULL,
                        low DOUBLE PRECISION NOT NULL,
                        close DOUBLE PRECISION NOT NULL,
                        volume DOUBLE PRECISION NOT NULL DEFAULT 0,
                        UNIQUE (asset, timeframe, timestamp)
                    )
                """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS idx_candles_asset_tf_ts
                    ON candles (asset, timeframe, timestamp)
                """)
        except Exception:
            log.warning("Failed to create candles table", exc_info=True)

    def seed_if_empty(self, asset: str, timeframe: str = "1h") -> None:
        """Seed candles from CSV if the table is empty for this asset+timeframe."""
        if not self._ensure_conn():
            return

        # Check if data exists
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM candles WHERE asset=%s AND timeframe=%s",
                    (asset.upper(), timeframe),
                )
                count = cur.fetchone()[0]
                if count > 0:
                    log.info("  %s %s: %d candles already in Postgres", asset, timeframe, count)
                    return
        except Exception:
            log.warning("Failed to check candle count for %s %s", asset, timeframe)
            return

        # Find CSV file
        suffix = self._CSV_SUFFIXES.get(timeframe)
        if suffix is None:
            log.info("  %s %s: no CSV mapping, skipping seed", asset, timeframe)
            return

        csv_path = os.path.join(self.data_dir, f"{asset.upper()}{suffix}")
        if not os.path.exists(csv_path):
            log.info("  %s %s: CSV not found at %s", asset, timeframe, csv_path)
            return

        # Load CSV and insert
        try:
            df = pd.read_csv(csv_path)
            # Normalize column names (lowercase)
            df.columns = [c.lower().strip() for c in df.columns]

            # Detect timestamp column
            ts_col = None
            for candidate in ["timestamp", "date", "datetime", "time"]:
                if candidate in df.columns:
                    ts_col = candidate
                    break
            if ts_col is None:
                log.warning("  %s %s: no timestamp column found in CSV", asset, timeframe)
                return

            df[ts_col] = pd.to_datetime(df[ts_col], utc=True)

            inserted = 0
            with self._conn.cursor() as cur:
                for _, row in df.iterrows():
                    try:
                        cur.execute(
                            """INSERT INTO candles (asset, timeframe, timestamp, open, high, low, close, volume)
                               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                               ON CONFLICT (asset, timeframe, timestamp) DO NOTHING""",
                            (
                                asset.upper(), timeframe, row[ts_col],
                                float(row["open"]), float(row["high"]),
                                float(row["low"]), float(row["close"]),
                                float(row.get("volume", 0)),
                            ),
                        )
                        inserted += 1
                    except Exception:
                        pass  # skip bad rows
            log.info("  %s %s: seeded %d candles from %s", asset, timeframe, inserted, csv_path)
        except Exception:
            log.warning("Failed to seed %s %s from CSV", asset, timeframe, exc_info=True)

    def get_candles(
        self,
        asset: str,
        timeframe: str = "1h",
        limit: int = 720,
    ) -> pd.DataFrame:
        if not self._ensure_conn():
            if self._fallback:
                return self._fallback.get_candles(asset, timeframe, limit)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """SELECT timestamp, open, high, low, close, volume
                       FROM candles
                       WHERE asset=%s AND timeframe=%s
                       ORDER BY timestamp DESC
                       LIMIT %s""",
                    (asset.upper(), timeframe, limit),
                )
                rows = cur.fetchall()

            if not rows:
                if self._fallback:
                    return self._fallback.get_candles(asset, timeframe, limit)
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

            df = pd.DataFrame(
                rows,
                columns=["timestamp", "open", "high", "low", "close", "volume"],
            )
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
            df = df.set_index("timestamp").sort_index()
            return df

        except Exception:
            log.warning("Failed to read candles for %s from Postgres", asset, exc_info=True)
            if self._fallback:
                return self._fallback.get_candles(asset, timeframe, limit)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def get_latest_price(self, asset: str) -> float:
        if not self._ensure_conn():
            if self._fallback:
                return self._fallback.get_latest_price(asset)
            return 0.0

        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """SELECT close FROM candles
                       WHERE asset=%s AND timeframe='1h'
                       ORDER BY timestamp DESC LIMIT 1""",
                    (asset.upper(),),
                )
                row = cur.fetchone()
                if row:
                    return float(row[0])
        except Exception:
            log.warning("Failed to get latest price for %s from Postgres", asset)

        if self._fallback:
            return self._fallback.get_latest_price(asset)
        return 0.0

    def ingest_candle(
        self,
        asset: str,
        timeframe: str,
        timestamp,
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
    ) -> None:
        """Insert or update a single candle (used by live feed to persist new data)."""
        if not self._ensure_conn():
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO candles (asset, timeframe, timestamp, open, high, low, close, volume)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (asset, timeframe, timestamp)
                       DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high,
                                     low=EXCLUDED.low, close=EXCLUDED.close,
                                     volume=EXCLUDED.volume""",
                    (asset.upper(), timeframe, timestamp, open_, high, low, close, volume),
                )
        except Exception:
            log.warning("Failed to ingest candle for %s", asset, exc_info=True)
