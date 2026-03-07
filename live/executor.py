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

    def _format_price(self, symbol: str, price: float) -> float:
        """Round price to exchange-required precision for the symbol."""
        ccxt_symbol = CCXT_SYMBOLS.get(symbol.upper(), "BTC/USD:USD")
        try:
            return float(self.client.exchange.price_to_precision(ccxt_symbol, price))
        except Exception:
            # Fallback: round to 8 significant figures
            if price == 0:
                return 0.0
            import math
            magnitude = math.floor(math.log10(abs(price)))
            factor = 10 ** (7 - magnitude)
            return round(price * factor) / factor

    def place_stop_order(
        self,
        symbol: str,
        side: str,
        size: float,
        stop_price: float,
        reduce_only: bool = True,
    ) -> dict[str, Any]:
        """Place a stop-market order (for stop-loss protection).

        Args:
            symbol: Asset name ("BTC") or futures symbol.
            side: "buy" or "sell" — the closing side.
            size: Position size in contracts.
            stop_price: Trigger price for the stop.
            reduce_only: Default True (safety — only reduces position).
        """
        ccxt_symbol = CCXT_SYMBOLS.get(symbol.upper(), "BTC/USD:USD")
        formatted_price = self._format_price(symbol, stop_price)
        params = {"stopPrice": formatted_price, "reduceOnly": reduce_only}
        return self.client.exchange.create_order(
            symbol=ccxt_symbol,
            type="stop",
            side=side.lower(),
            amount=size,
            price=None,
            params=params,
        )

    def place_take_profit_order(
        self,
        symbol: str,
        side: str,
        size: float,
        tp_price: float,
        reduce_only: bool = True,
    ) -> dict[str, Any]:
        """Place a take-profit market order.

        Args:
            symbol: Asset name ("BTC") or futures symbol.
            side: "buy" or "sell" — the closing side.
            size: Position size in contracts.
            tp_price: Trigger price for take-profit.
            reduce_only: Default True.
        """
        ccxt_symbol = CCXT_SYMBOLS.get(symbol.upper(), "BTC/USD:USD")
        formatted_price = self._format_price(symbol, tp_price)
        params = {"stopPrice": formatted_price, "reduceOnly": reduce_only}
        return self.client.exchange.create_order(
            symbol=ccxt_symbol,
            type="takeProfit",
            side=side.lower(),
            amount=size,
            price=None,
            params=params,
        )

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        reduce_only: bool = False,
        post_only: bool = True,
    ) -> dict[str, Any]:
        """Place a limit order (for smart entries).

        Args:
            symbol: Asset name ("BTC") or futures symbol.
            side: "buy" or "sell".
            size: Position size in contracts.
            price: Limit price.
            reduce_only: If True, only reduces existing position.
            post_only: If True, order is maker-only (rejected if would take).
        """
        ccxt_symbol = CCXT_SYMBOLS.get(symbol.upper(), "BTC/USD:USD")
        formatted_price = self._format_price(symbol, price)
        params = {"reduceOnly": reduce_only}
        if post_only:
            params["postOnly"] = True
        return self.client.exchange.create_order(
            symbol=ccxt_symbol,
            type="limit",
            side=side.lower(),
            amount=size,
            price=formatted_price,
            params=params,
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
