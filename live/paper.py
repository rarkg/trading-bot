"""PaperTrader: wraps KrakenExecutor pointing at demo-futures.kraken.com.

Same interface as KrakenExecutor, always uses demo endpoint.
"""

from typing import Any, Optional

from live.executor import KrakenExecutor


class PaperTrader:
    """Paper trading via Kraken Futures demo environment.

    Identical interface to KrakenExecutor but hardcoded to demo mode.
    Uses demo-futures.kraken.com for all API calls.
    """

    def __init__(self, api_key: str, api_secret: str) -> None:
        """Initialize PaperTrader.

        Args:
            api_key: Kraken Futures demo API key.
            api_secret: Kraken Futures demo API secret (base64-encoded).
        """
        self._executor = KrakenExecutor(
            api_key=api_key,
            api_secret=api_secret,
            demo=True,
        )

    def place_order(
        self,
        symbol: str,
        side: str,
        size: float,
        order_type: str = "mkt",
        price: Optional[float] = None,
    ) -> dict[str, Any]:
        """Place an order on Kraken Futures demo.

        Args:
            symbol: Asset name ("BTC") or futures symbol ("PF_XBTUSD").
            side: "buy" or "sell".
            size: Position size in contracts.
            order_type: "mkt" (market) or "lmt" (limit).
            price: Limit price. Required for limit orders.

        Returns:
            Order response dict.
        """
        return self._executor.place_order(
            symbol=symbol,
            side=side,
            size=size,
            order_type=order_type,
            price=price,
        )

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel an open order on demo.

        Args:
            order_id: The order ID to cancel.

        Returns:
            Cancellation response dict.
        """
        return self._executor.cancel_order(order_id)

    def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions on demo.

        Returns:
            List of position dicts.
        """
        return self._executor.get_positions()

    def get_balance(self) -> dict[str, float]:
        """Get demo account balances.

        Returns:
            Dict mapping currency to available balance.
        """
        return self._executor.get_balance()
