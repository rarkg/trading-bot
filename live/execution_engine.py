"""ExecutionEngine interface and implementations for order execution.

Abstracts order placement so run_live.py works identically whether
executing on Kraken, simulating fills, or running a backtest.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

log = logging.getLogger("live.exec")


class ExecutionEngine(ABC):
    """Abstract interface for trade execution."""

    @abstractmethod
    def open_position(
        self,
        asset: str,
        direction: str,
        size_usd: float,
        price: float,
        stop: float,
        target: float,
    ) -> str:
        """Open a new position.

        Args:
            asset: Asset name ("BTC", "ETH", etc.).
            direction: "LONG" or "SHORT".
            size_usd: Position size in USD.
            price: Current price (for contract sizing).
            stop: Stop-loss price.
            target: Take-profit price.

        Returns:
            Order ID string (or empty string on failure).
        """

    @abstractmethod
    def close_position(
        self,
        asset: str,
        direction: str,
        size_usd: float,
        price: float,
        reason: str,
    ) -> float:
        """Close an existing position.

        Args:
            asset: Asset name.
            direction: Original direction ("LONG" or "SHORT").
            size_usd: Original position size in USD.
            price: Current price.
            reason: Exit reason (e.g., "STOP", "TARGET", "SIGNAL").

        Returns:
            Realized PnL in USD.
        """

    @abstractmethod
    def place_stop_loss(
        self,
        asset: str,
        direction: str,
        size_contracts: float,
        stop_price: float,
    ) -> str:
        """Place a stop-loss order on the exchange.

        Returns:
            Order ID string (or empty string).
        """

    @abstractmethod
    def place_take_profit(
        self,
        asset: str,
        direction: str,
        size_contracts: float,
        tp_price: float,
    ) -> str:
        """Place a take-profit order on the exchange.

        Returns:
            Order ID string (or empty string).
        """

    @abstractmethod
    def cancel_order(self, order_id: str, asset: str) -> None:
        """Cancel an open order."""

    @abstractmethod
    def get_balance(self) -> float:
        """Get total account balance in USD."""

    @abstractmethod
    def get_positions(self) -> list:
        """Get all open positions from the exchange.

        Returns:
            List of position dicts with keys: symbol, side, size,
            entry_price, pnl.
        """


class KrakenExecutionEngine(ExecutionEngine):
    """Executes orders on Kraken via the existing KrakenExecutor."""

    def __init__(self, api_key: str, api_secret: str, demo: bool = True) -> None:
        from live.executor import KrakenExecutor
        self._executor = KrakenExecutor(api_key, api_secret, demo=demo)

    def open_position(
        self,
        asset: str,
        direction: str,
        size_usd: float,
        price: float,
        stop: float,
        target: float,
    ) -> str:
        side = "buy" if direction == "LONG" else "sell"
        size_contracts = size_usd / price if price > 0 else 0
        if size_contracts <= 0:
            return ""

        order = self._executor.place_order(
            symbol=asset,
            side=side,
            size=size_contracts,
            order_type="mkt",
        )
        return order.get("id", "")

    def close_position(
        self,
        asset: str,
        direction: str,
        size_usd: float,
        price: float,
        reason: str,
    ) -> float:
        close_side = "sell" if direction == "LONG" else "buy"
        # Use the entry price approximation for contract sizing
        size_contracts = size_usd / price if price > 0 else 0
        if size_contracts <= 0:
            return 0.0

        self._executor.place_order(
            symbol=asset,
            side=close_side,
            size=abs(size_contracts),
            order_type="mkt",
            reduce_only=True,
        )
        # PnL is computed by the caller from price data
        return 0.0

    def place_stop_loss(
        self,
        asset: str,
        direction: str,
        size_contracts: float,
        stop_price: float,
    ) -> str:
        if size_contracts <= 0 or stop_price <= 0:
            return ""
        close_side = "sell" if direction == "LONG" else "buy"
        order = self._executor.place_stop_order(
            symbol=asset,
            side=close_side,
            size=abs(size_contracts),
            stop_price=stop_price,
        )
        return order.get("id", "")

    def place_take_profit(
        self,
        asset: str,
        direction: str,
        size_contracts: float,
        tp_price: float,
    ) -> str:
        if size_contracts <= 0 or tp_price <= 0:
            return ""
        close_side = "sell" if direction == "LONG" else "buy"
        order = self._executor.place_take_profit_order(
            symbol=asset,
            side=close_side,
            size=abs(size_contracts),
            tp_price=tp_price,
        )
        return order.get("id", "")

    def cancel_order(self, order_id: str, asset: str) -> None:
        self._executor.cancel_order(order_id, asset)

    def get_balance(self) -> float:
        bal = self._executor.get_balance()
        return bal.get("USD_total", bal.get("USD", 0.0))

    def get_positions(self) -> list:
        return self._executor.get_positions()


class MockExecutionEngine(ExecutionEngine):
    """Simulated execution engine for paper trading and backtesting.

    Fills immediately at the given price. Tracks positions and balance
    in memory.
    """

    def __init__(self, initial_balance: float = 5000.0) -> None:
        self._balance = initial_balance
        self._initial_balance = initial_balance
        self._positions: list[dict] = []
        self._next_order_id = 1
        self._orders: dict[str, dict] = {}  # outstanding SL/TP orders

    def _gen_order_id(self) -> str:
        oid = f"mock-{self._next_order_id}"
        self._next_order_id += 1
        return oid

    def open_position(
        self,
        asset: str,
        direction: str,
        size_usd: float,
        price: float,
        stop: float,
        target: float,
    ) -> str:
        oid = self._gen_order_id()
        size_contracts = size_usd / price if price > 0 else 0
        self._positions.append({
            "symbol": f"{asset}/USD:USD",
            "side": "long" if direction == "LONG" else "short",
            "size": size_contracts,
            "entry_price": price,
            "pnl": 0.0,
            "order_id": oid,
            "asset": asset,
        })
        log.info("[MOCK] Opened %s %s %.4f contracts @ %.2f ($%.0f)",
                 asset, direction, size_contracts, price, size_usd)
        return oid

    def close_position(
        self,
        asset: str,
        direction: str,
        size_usd: float,
        price: float,
        reason: str,
    ) -> float:
        # Find and remove the matching position
        for i, pos in enumerate(self._positions):
            if pos.get("asset") == asset:
                entry = pos["entry_price"]
                if direction == "LONG":
                    pnl_pct = (price - entry) / entry
                else:
                    pnl_pct = (entry - price) / entry
                pnl_usd = pnl_pct * size_usd
                self._balance += pnl_usd
                self._positions.pop(i)
                log.info("[MOCK] Closed %s %s @ %.2f | pnl=$%.2f (%s)",
                         asset, direction, price, pnl_usd, reason)
                return pnl_usd
        return 0.0

    def place_stop_loss(
        self,
        asset: str,
        direction: str,
        size_contracts: float,
        stop_price: float,
    ) -> str:
        oid = self._gen_order_id()
        self._orders[oid] = {
            "type": "SL", "asset": asset, "direction": direction,
            "size": size_contracts, "price": stop_price,
        }
        log.info("[MOCK] Placed SL %s for %s @ %.2f", oid, asset, stop_price)
        return oid

    def place_take_profit(
        self,
        asset: str,
        direction: str,
        size_contracts: float,
        tp_price: float,
    ) -> str:
        oid = self._gen_order_id()
        self._orders[oid] = {
            "type": "TP", "asset": asset, "direction": direction,
            "size": size_contracts, "price": tp_price,
        }
        log.info("[MOCK] Placed TP %s for %s @ %.2f", oid, asset, tp_price)
        return oid

    def cancel_order(self, order_id: str, asset: str) -> None:
        if order_id in self._orders:
            del self._orders[order_id]
            log.info("[MOCK] Cancelled order %s for %s", order_id, asset)

    def get_balance(self) -> float:
        return self._balance

    def get_positions(self) -> list:
        return list(self._positions)
