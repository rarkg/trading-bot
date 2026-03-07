"""Kraken API client for spot data and futures order execution.

Spot REST API for OHLCV data (public, no auth).
Futures API via ccxt.krakenfutures for all authenticated calls.
"""

from typing import Any, Optional

import ccxt
import requests


# Kraken spot pairs -> futures perp symbols
SPOT_PAIRS: dict[str, str] = {
    "BTC": "XBTUSD",
    "ETH": "ETHUSD",
    "SOL": "SOLUSD",
    "LINK": "LINKUSD",
}

FUTURES_SYMBOLS: dict[str, str] = {
    "BTC": "PF_XBTUSD",
    "ETH": "PF_ETHUSD",
    "SOL": "PF_SOLUSD",
    "LINK": "PF_LINKUSD",
}

# ccxt market IDs for Kraken Futures
CCXT_SYMBOLS: dict[str, str] = {
    "BTC": "BTC/USD:USD",
    "ETH": "ETH/USD:USD",
    "SOL": "SOL/USD:USD",
    "LINK": "LINK/USD:USD",
}

SPOT_BASE_URL = "https://api.kraken.com"


class KrakenSpotClient:
    """Client for Kraken Spot REST API (public endpoints only)."""

    def __init__(self, base_url: str = SPOT_BASE_URL, timeout: int = 30) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.session = requests.Session()

    def get_ohlc(
        self, pair: str, interval: int = 60, since: Optional[int] = None
    ) -> list[dict[str, Any]]:
        """Fetch OHLCV candles from Kraken spot.

        Args:
            pair: Kraken pair name (e.g. "XBTUSD").
            interval: Candle interval in minutes (1, 5, 15, 30, 60, 240, 1440).
            since: Unix timestamp to fetch candles after.

        Returns:
            List of dicts with keys: timestamp, open, high, low, close, volume.
        """
        params: dict[str, Any] = {"pair": pair, "interval": interval}
        if since is not None:
            params["since"] = since

        resp = self.session.get(
            f"{self.base_url}/0/public/OHLC",
            params=params,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("error"):
            raise KrakenAPIError(data["error"])

        result_key = [k for k in data["result"] if k != "last"][0]
        raw_candles = data["result"][result_key]

        candles = []
        for c in raw_candles:
            candles.append({
                "timestamp": int(c[0]),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[6]),
            })
        return candles


class KrakenFuturesClient:
    """Client for Kraken Futures via ccxt.krakenfutures.

    Uses ccxt for ALL authenticated calls. set_sandbox_mode(True) for demo.
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        demo: bool = True,
        timeout: int = 30,
    ) -> None:
        self.demo = demo
        self.exchange = ccxt.krakenfutures({
            "apiKey": api_key,
            "secret": api_secret,
            "timeout": timeout * 1000,
            "enableRateLimit": True,
        })
        if demo:
            self.exchange.set_sandbox_mode(True)

    def send_order(
        self,
        symbol: str,
        side: str,
        size: float,
        order_type: str = "lmt",
        price: Optional[float] = None,
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """Place an order on Kraken Futures.

        Args:
            symbol: Futures symbol ("PF_XBTUSD") or asset name ("BTC").
            side: "buy" or "sell".
            size: Order size in contracts.
            order_type: "lmt" or "mkt".
            price: Limit price (required for lmt orders).
            reduce_only: If True, only reduces existing position.
        """
        ccxt_symbol = self._resolve_ccxt_symbol(symbol)
        ccxt_type = "limit" if order_type == "lmt" else "market"
        params = {}
        if reduce_only:
            params["reduceOnly"] = True

        order = self.exchange.create_order(
            symbol=ccxt_symbol,
            type=ccxt_type,
            side=side.lower(),
            amount=size,
            price=price,
            params=params,
        )
        return order

    def cancel_order(self, order_id: str, symbol: str = "BTC/USD:USD") -> dict[str, Any]:
        """Cancel an open order."""
        return self.exchange.cancel_order(order_id, symbol)

    def get_open_positions(self) -> list[dict[str, Any]]:
        """Get all open positions."""
        return self.exchange.fetch_positions()

    def get_accounts(self) -> dict[str, float]:
        """Get account balances."""
        return self.exchange.fetch_balance()

    def get_open_orders(self, symbol: Optional[str] = None) -> list[dict[str, Any]]:
        """Get all open orders."""
        return self.exchange.fetch_open_orders(symbol)

    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", since: Optional[int] = None, limit: int = 500
    ) -> list:
        """Fetch OHLCV from futures exchange (useful for price reference)."""
        ccxt_symbol = self._resolve_ccxt_symbol(symbol)
        return self.exchange.fetch_ohlcv(ccxt_symbol, timeframe, since, limit)

    def _resolve_ccxt_symbol(self, symbol: str) -> str:
        """Resolve asset/futures symbol to ccxt symbol format."""
        upper = symbol.upper()
        if upper in CCXT_SYMBOLS:
            return CCXT_SYMBOLS[upper]
        # Map PF_XBTUSD -> BTC/USD:USD etc.
        for asset, pf in FUTURES_SYMBOLS.items():
            if upper == pf:
                return CCXT_SYMBOLS[asset]
        # Already in ccxt format
        if "/" in symbol:
            return symbol
        raise ValueError(f"Unknown symbol: {symbol}")


class KrakenAPIError(Exception):
    """Raised when Kraken API returns an error."""
    pass
