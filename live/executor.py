"""KrakenExecutor: places/cancels orders on Kraken Futures.

Supports both demo and live modes via endpoint selection.
"""

from typing import Any, Optional

from live.exchange.kraken import KrakenFuturesClient, FUTURES_SYMBOLS


class KrakenExecutor:
    """Executes trades on Kraken Futures.

    Provides a unified interface for order management and position queries.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        demo: bool = True,
    ) -> None:
        """Initialize KrakenExecutor.

        Args:
            api_key: Kraken Futures API key.
            api_secret: Kraken Futures API secret (base64-encoded).
            demo: If True, uses demo-futures.kraken.com. If False, uses live.
        """
        self.client = KrakenFuturesClient(
            api_key=api_key,
            api_secret=api_secret,
            demo=demo,
        )
        self.demo = demo

    def _resolve_symbol(self, symbol: str) -> str:
        """Resolve asset name to Kraken futures symbol.

        Args:
            symbol: Either a futures symbol ("PF_XBTUSD") or asset name ("BTC").

        Returns:
            Kraken futures symbol string.
        """
        upper = symbol.upper()
        if upper in FUTURES_SYMBOLS:
            return FUTURES_SYMBOLS[upper]
        if upper.startswith("PF_"):
            return upper
        raise ValueError(f"Unknown symbol: {symbol}. Use asset name (BTC) or futures symbol (PF_XBTUSD)")

    def place_order(
        self,
        symbol: str,
        side: str,
        size: float,
        order_type: str = "mkt",
        price: Optional[float] = None,
    ) -> dict[str, Any]:
        """Place an order on Kraken Futures.

        Args:
            symbol: Asset name ("BTC") or futures symbol ("PF_XBTUSD").
            side: "buy" or "sell".
            size: Position size in contracts.
            order_type: "mkt" (market) or "lmt" (limit).
            price: Limit price. Required for limit orders.

        Returns:
            Order response dict with order_id and status.

        Raises:
            ValueError: If side is invalid or limit order missing price.
        """
        if side.lower() not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {side}. Must be 'buy' or 'sell'")
        if order_type == "lmt" and price is None:
            raise ValueError("Limit orders require a price")

        futures_symbol = self._resolve_symbol(symbol)
        return self.client.send_order(
            symbol=futures_symbol,
            side=side.lower(),
            size=size,
            order_type=order_type,
            price=price,
        )

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order.

        Args:
            order_id: The order ID to cancel.

        Returns:
            Cancellation response dict.
        """
        return self.client.cancel_order(order_id)

    def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions.

        Returns:
            List of position dicts with symbol, side, size, entry_price, pnl.
        """
        resp = self.client.get_open_positions()
        positions = []
        for pos in resp.get("openPositions", []):
            positions.append({
                "symbol": pos.get("symbol", ""),
                "side": pos.get("side", ""),
                "size": float(pos.get("size", 0)),
                "entry_price": float(pos.get("price", 0)),
                "pnl": float(pos.get("unrealizedFunding", 0)),
            })
        return positions

    def get_balance(self) -> dict[str, float]:
        """Get account balances.

        Returns:
            Dict mapping currency to available balance.
        """
        resp = self.client.get_accounts()
        balances: dict[str, float] = {}
        for account in resp.get("accounts", {}).values():
            currency = account.get("currency", "unknown")
            balances[currency] = float(account.get("auxiliary", {}).get("af", 0))
        return balances
