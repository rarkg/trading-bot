"""ExchangeAdapter ABC + KrakenAdapter + MockAdapter.

Abstracts exchange operations so trader.py can work with Kraken (live),
a mock (paper/backtest), or any future exchange.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

log = logging.getLogger("live.exchange_adapter")


class ExchangeAdapter(ABC):
    """Abstract interface for exchange operations."""

    @abstractmethod
    def get_balance(self):
        # type: () -> dict
        """Get account balances. Returns dict with at least 'USD_total'."""

    @abstractmethod
    def get_positions(self):
        # type: () -> list
        """Get open positions. Returns list of dicts with symbol, side, size, entry_price, pnl."""

    @abstractmethod
    def get_open_orders(self, symbol=None):
        # type: (Optional[str]) -> list
        """Get open orders. Returns list of ccxt-style order dicts."""

    @abstractmethod
    def place_market_order(self, symbol, side, size, reduce_only=False):
        # type: (str, str, float, bool) -> dict
        """Place a market order. Returns order dict with at least 'id'."""

    @abstractmethod
    def place_stop_order(self, symbol, side, size, stop_price, reduce_only=True):
        # type: (str, str, float, float, bool) -> dict
        """Place a stop-market order. Returns order dict."""

    @abstractmethod
    def place_take_profit_order(self, symbol, side, size, tp_price, reduce_only=True):
        # type: (str, str, float, float, bool) -> dict
        """Place a take-profit market order. Returns order dict."""

    @abstractmethod
    def cancel_order(self, order_id, symbol="BTC"):
        # type: (str, str) -> dict
        """Cancel an open order."""

    @abstractmethod
    def fetch_order(self, order_id, symbol="BTC"):
        # type: (str, str) -> dict
        """Fetch order status. Returns ccxt-style order dict."""

    @abstractmethod
    def get_ticker(self, symbol):
        # type: (str,) -> dict
        """Get current ticker. Returns dict with at least 'last' price."""


class KrakenAdapter(ExchangeAdapter):
    """Wraps live/executor.py KrakenExecutor for the ExchangeAdapter interface."""

    def __init__(self, executor):
        """
        Args:
            executor: A KrakenExecutor instance (from live/executor.py).
        """
        self._exec = executor

    def get_balance(self):
        # type: () -> dict
        return self._exec.get_balance()

    def get_positions(self):
        # type: () -> list
        return self._exec.get_positions()

    def get_open_orders(self, symbol=None):
        # type: (Optional[str]) -> list
        return self._exec.client.get_open_orders(symbol)

    def place_market_order(self, symbol, side, size, reduce_only=False):
        # type: (str, str, float, bool) -> dict
        return self._exec.place_order(
            symbol=symbol, side=side, size=size,
            order_type="mkt", reduce_only=reduce_only,
        )

    def place_stop_order(self, symbol, side, size, stop_price, reduce_only=True):
        # type: (str, str, float, float, bool) -> dict
        return self._exec.place_stop_order(
            symbol=symbol, side=side, size=size,
            stop_price=stop_price, reduce_only=reduce_only,
        )

    def place_take_profit_order(self, symbol, side, size, tp_price, reduce_only=True):
        # type: (str, str, float, float, bool) -> dict
        return self._exec.place_take_profit_order(
            symbol=symbol, side=side, size=size,
            tp_price=tp_price, reduce_only=reduce_only,
        )

    def cancel_order(self, order_id, symbol="BTC"):
        # type: (str, str) -> dict
        return self._exec.cancel_order(order_id, symbol)

    def fetch_order(self, order_id, symbol="BTC"):
        # type: (str, str) -> dict
        from live.exchange.kraken import CCXT_SYMBOLS
        ccxt_sym = CCXT_SYMBOLS.get(symbol.upper(), symbol + "/USD:USD")
        return self._exec.client.exchange.fetch_order(order_id, ccxt_sym)

    def get_ticker(self, symbol):
        # type: (str,) -> dict
        from live.exchange.kraken import CCXT_SYMBOLS
        ccxt_sym = CCXT_SYMBOLS.get(symbol.upper(), symbol + "/USD:USD")
        ticker = self._exec.client.exchange.fetch_ticker(ccxt_sym)
        return {"last": float(ticker.get("last", 0) or 0), "raw": ticker}


class MockAdapter(ExchangeAdapter):
    """Mock exchange adapter for paper trading and backtesting.

    Tracks positions and orders in memory. No real exchange calls.
    """

    def __init__(self, initial_balance=5000.0):
        # type: (float) -> None
        self._balance = initial_balance
        self._positions = {}  # type: dict  # symbol -> position dict
        self._orders = {}  # type: dict  # order_id -> order dict
        self._order_counter = 0
        self._prices = {}  # type: dict  # symbol -> last price

    def set_price(self, symbol, price):
        # type: (str, float) -> None
        """Set the current price for a symbol (used by paper/backtest driver)."""
        self._prices[symbol.upper()] = price

    def get_balance(self):
        # type: () -> dict
        return {
            "USD": self._balance,
            "USD_total": self._balance,
        }

    def get_positions(self):
        # type: () -> list
        result = []
        for sym, pos in self._positions.items():
            if abs(pos.get("size", 0)) > 0:
                result.append({
                    "symbol": sym,
                    "side": pos.get("side", "long"),
                    "size": abs(pos["size"]),
                    "entry_price": pos.get("entry_price", 0),
                    "pnl": 0.0,
                })
        return result

    def get_open_orders(self, symbol=None):
        # type: (Optional[str]) -> list
        orders = []
        for oid, order in self._orders.items():
            if order.get("status") != "open":
                continue
            if symbol and order.get("symbol", "").upper() != symbol.upper():
                continue
            orders.append(order)
        return orders

    def place_market_order(self, symbol, side, size, reduce_only=False):
        # type: (str, str, float, bool) -> dict
        self._order_counter += 1
        oid = "mock-%d" % self._order_counter
        price = self._prices.get(symbol.upper(), 0)

        if reduce_only:
            pos = self._positions.get(symbol.upper())
            if pos:
                self._positions.pop(symbol.upper(), None)
        else:
            self._positions[symbol.upper()] = {
                "side": "long" if side.lower() == "buy" else "short",
                "size": size,
                "entry_price": price,
            }

        order = {
            "id": oid,
            "symbol": symbol.upper(),
            "side": side.lower(),
            "type": "market",
            "amount": size,
            "price": price,
            "average": price,
            "status": "closed",
        }
        self._orders[oid] = order
        return order

    def place_stop_order(self, symbol, side, size, stop_price, reduce_only=True):
        # type: (str, str, float, float, bool) -> dict
        self._order_counter += 1
        oid = "mock-sl-%d" % self._order_counter
        order = {
            "id": oid,
            "symbol": symbol.upper(),
            "side": side.lower(),
            "type": "stop",
            "amount": size,
            "stopPrice": stop_price,
            "status": "open",
            "reduce_only": reduce_only,
        }
        self._orders[oid] = order
        return order

    def place_take_profit_order(self, symbol, side, size, tp_price, reduce_only=True):
        # type: (str, str, float, float, bool) -> dict
        self._order_counter += 1
        oid = "mock-tp-%d" % self._order_counter
        order = {
            "id": oid,
            "symbol": symbol.upper(),
            "side": side.lower(),
            "type": "takeProfit",
            "amount": size,
            "stopPrice": tp_price,
            "status": "open",
            "reduce_only": reduce_only,
        }
        self._orders[oid] = order
        return order

    def cancel_order(self, order_id, symbol="BTC"):
        # type: (str, str) -> dict
        if order_id in self._orders:
            self._orders[order_id]["status"] = "canceled"
            return self._orders[order_id]
        return {"id": order_id, "status": "canceled"}

    def fetch_order(self, order_id, symbol="BTC"):
        # type: (str, str) -> dict
        if order_id in self._orders:
            return self._orders[order_id]
        return {"id": order_id, "status": "unknown"}

    def get_ticker(self, symbol):
        # type: (str,) -> dict
        price = self._prices.get(symbol.upper(), 0)
        return {"last": price}
