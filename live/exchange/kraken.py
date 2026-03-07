"""Kraken API client for spot data and futures order execution.

Spot REST API for OHLCV data, Futures API for order management.
Auth uses HMAC-SHA512 per Kraken Futures spec.
"""

import hashlib
import hmac
import time
import base64
from typing import Any, Optional
from urllib.parse import urlencode

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

SPOT_BASE_URL = "https://api.kraken.com"
FUTURES_LIVE_URL = "https://futures.kraken.com/derivatives/api/v3"
FUTURES_DEMO_URL = "https://demo-futures.kraken.com/derivatives/api/v3"


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
            interval: Candle interval in minutes (1, 5, 15, 30, 60, 240, 1440, 10080, 21600).
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

        # Response has pair key (may differ from input), find it
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
    """Client for Kraken Futures REST API with HMAC-SHA512 auth."""

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        demo: bool = True,
        timeout: int = 30,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = FUTURES_DEMO_URL if demo else FUTURES_LIVE_URL
        self.timeout = timeout
        self.session = requests.Session()

    def _sign(self, endpoint: str, postdata: str = "", nonce: str = "") -> str:
        """Generate HMAC-SHA512 signature for Kraken Futures API.

        Args:
            endpoint: API path after /derivatives/api/v3.
            postdata: URL-encoded POST body.
            nonce: Nonce value.

        Returns:
            Base64-encoded signature string.
        """
        if not self.api_secret:
            raise KrakenAPIError("API secret not configured")

        # Kraken Futures auth: SHA256(postdata + nonce + endpoint) then HMAC-SHA512
        message = postdata + nonce + endpoint
        sha256_hash = hashlib.sha256(message.encode("utf-8")).digest()
        secret_bytes = base64.b64decode(self.api_secret)
        signature = hmac.new(secret_bytes, sha256_hash, hashlib.sha512)
        return base64.b64encode(signature.digest()).decode("utf-8")

    def _auth_headers(self, endpoint: str, postdata: str = "") -> dict[str, str]:
        """Build authenticated headers for a futures request."""
        nonce = str(int(time.time() * 1000))
        sig = self._sign(endpoint, postdata, nonce)
        return {
            "APIKey": self.api_key,
            "Nonce": nonce,
            "Authent": sig,
        }

    def _get(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Authenticated GET request."""
        url = f"{self.base_url}{endpoint}"
        headers = self._auth_headers(endpoint)
        resp = self.session.get(url, headers=headers, params=params, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise KrakenAPIError(data["error"])
        return data

    def _post(self, endpoint: str, payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        """Authenticated POST request."""
        postdata = urlencode(payload) if payload else ""
        url = f"{self.base_url}{endpoint}"
        headers = self._auth_headers(endpoint, postdata)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        resp = self.session.post(url, headers=headers, data=postdata, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        if data.get("error"):
            raise KrakenAPIError(data["error"])
        return data

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
            symbol: Futures symbol (e.g. "PF_XBTUSD").
            side: "buy" or "sell".
            size: Order size in contracts.
            order_type: "lmt" or "mkt".
            price: Limit price (required for lmt orders).
            reduce_only: If True, only reduces existing position.

        Returns:
            Order response dict from Kraken.
        """
        payload: dict[str, Any] = {
            "orderType": order_type,
            "symbol": symbol,
            "side": side,
            "size": size,
        }
        if price is not None:
            payload["limitPrice"] = price
        if reduce_only:
            payload["reduceOnly"] = "true"

        return self._post("/sendorder", payload)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order.

        Args:
            order_id: The order ID to cancel.

        Returns:
            Cancellation response dict.
        """
        return self._post("/cancelorder", {"order_id": order_id})

    def get_open_positions(self) -> dict[str, Any]:
        """Get all open positions."""
        return self._get("/openpositions")

    def get_accounts(self) -> dict[str, Any]:
        """Get account balances."""
        return self._get("/accounts")

    def get_open_orders(self) -> dict[str, Any]:
        """Get all open orders."""
        return self._get("/openorders")


class KrakenAPIError(Exception):
    """Raised when Kraken API returns an error."""
    pass
