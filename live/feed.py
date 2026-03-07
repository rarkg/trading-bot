"""LiveFeed: fetches OHLCV data from Kraken REST API for BTC/ETH/SOL/LINK.

Returns pandas DataFrames matching the backtest engine's expected format.
"""

from typing import Optional

import pandas as pd

from live.exchange.kraken import KrakenSpotClient, SPOT_PAIRS


# Kraken interval values in minutes
INTERVALS: dict[str, int] = {
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


class LiveFeed:
    """Fetches historical OHLCV candles from Kraken spot REST API.

    Supports BTC (XBT), ETH, SOL, and LINK against USD.
    """

    def __init__(self, client: Optional[KrakenSpotClient] = None) -> None:
        """Initialize LiveFeed.

        Args:
            client: Optional KrakenSpotClient instance. Creates default if None.
        """
        self.client = client or KrakenSpotClient()

    def get_candles(
        self,
        asset: str,
        interval: str = "1h",
        since: Optional[int] = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV candles for an asset.

        Args:
            asset: Asset name ("BTC", "ETH", "SOL", "LINK").
            interval: Timeframe string ("1h", "4h", "1d").
            since: Unix timestamp to fetch candles after.

        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume.
            Indexed by timestamp as datetime.

        Raises:
            ValueError: If asset or interval is not supported.
        """
        if asset.upper() not in SPOT_PAIRS:
            raise ValueError(f"Unsupported asset: {asset}. Use one of {list(SPOT_PAIRS.keys())}")
        if interval not in INTERVALS:
            raise ValueError(f"Unsupported interval: {interval}. Use one of {list(INTERVALS.keys())}")

        pair = SPOT_PAIRS[asset.upper()]
        minutes = INTERVALS[interval]

        raw = self.client.get_ohlc(pair=pair, interval=minutes, since=since)

        if not raw:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(raw)
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df = df.set_index("timestamp").sort_index()
        return df

    def get_multi_timeframe(
        self,
        asset: str,
        intervals: Optional[list[str]] = None,
        since: Optional[int] = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch candles across multiple timeframes for an asset.

        Args:
            asset: Asset name ("BTC", "ETH", "SOL", "LINK").
            intervals: List of timeframe strings. Defaults to ["1h", "4h", "1d"].
            since: Unix timestamp to fetch candles after.

        Returns:
            Dict mapping interval string to DataFrame.
        """
        if intervals is None:
            intervals = ["1h", "4h", "1d"]

        result: dict[str, pd.DataFrame] = {}
        for iv in intervals:
            result[iv] = self.get_candles(asset=asset, interval=iv, since=since)
        return result
