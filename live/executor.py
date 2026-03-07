"""KrakenExecutor: places/cancels orders on Kraken Futures via ccxt.

Supports both demo and live modes via set_sandbox_mode.
"""

from typing import Any, Optional

from live.exchange.kraken import KrakenFuturesClient, FUTURES_SYMBOLS, CCXT_SYMBOLS


class KrakenExecutor:
    """Executes trades on Kraken Futures via ccxt."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        demo: bool = True,
    ) -> None:
        self.client = KrakenFuturesClient(
            api_key=api_key,
            api_secret=api_secret,
            demo=demo,
        )
        self.demo = demo

    def _resolve_symbol(self, symbol: str) -> str:
        """Resolve asset name to Kraken futures symbol."""
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
        reduce_only: bool = False,
    ) -> dict[str, Any]:
        """Place an order on Kraken Futures.

        Args:
            symbol: Asset name ("BTC") or futures symbol ("PF_XBTUSD").
            side: "buy" or "sell".
            size: Position size in contracts.
            order_type: "mkt" (market) or "lmt" (limit).
            price: Limit price. Required for limit orders.
            reduce_only: If True, only reduces existing position.

        Returns:
            ccxt order response dict.
        """
        if side.lower() not in ("buy", "sell"):
            raise ValueError(f"Invalid side: {side}. Must be 'buy' or 'sell'")
        if order_type == "lmt" and price is None:
            raise ValueError("Limit orders require a price")

        return self.client.send_order(
            symbol=symbol,
            side=side.lower(),
            size=size,
            order_type=order_type,
            price=price,
            reduce_only=reduce_only,
        )

    def cancel_order(self, order_id: str, symbol: str = "BTC") -> dict[str, Any]:
        """Cancel an open order."""
        ccxt_sym = CCXT_SYMBOLS.get(symbol.upper(), "BTC/USD:USD")
        return self.client.cancel_order(order_id, ccxt_sym)

    def get_positions(self) -> list[dict[str, Any]]:
        """Get all open positions.

        Returns:
            List of position dicts with symbol, side, size, entry_price, pnl.
        """
        raw_positions = self.client.get_open_positions()
        positions = []
        for pos in raw_positions:
            size = float(pos.get("contracts", 0) or 0)
            if size == 0:
                continue
            positions.append({
                "symbol": pos.get("symbol", ""),
                "side": pos.get("side", ""),
                "size": size,
                "entry_price": float(pos.get("entryPrice", 0) or 0),
                "pnl": float(pos.get("unrealizedPnl", 0) or 0),
            })
        return positions

    def get_balance(self) -> dict[str, float]:
        """Get account balances.

        Returns:
            Dict mapping currency to available balance.
        """
        balance = self.client.get_accounts()
        result: dict[str, float] = {}
        if "USD" in balance:
            usd = balance["USD"]
            result["USD"] = float(usd.get("free", 0) or 0)
            result["USD_total"] = float(usd.get("total", 0) or 0)
        # Also check 'total' and 'free' top-level
        if "total" in balance:
            for currency, amount in balance["total"].items():
                if amount and float(amount) > 0:
                    result[f"{currency}_total"] = float(amount)
        if "free" in balance:
            for currency, amount in balance["free"].items():
                if amount and float(amount) > 0:
                    result[f"{currency}_free"] = float(amount)
        return result
