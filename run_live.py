#!/usr/bin/env python3
"""Live trading runner — feeds V15 squeeze + V2.3 candle strategies into Kraken demo.

Loops every hour:
  1. Fetch latest 1h candles for BTC/ETH/SOL/LINK
  2. Feed to both strategies
  3. If signal → check risk → size → execute on Kraken demo
  4. Check open positions for stop/target hits
  5. Log to console + SQLite
"""

import os
import sys
import signal
import sqlite3
import subprocess
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List

import pandas as pd
from dotenv import load_dotenv

from strategies.squeeze_v15 import SqueezeV15
from strategies.candle_v2_3 import CandleV2_3
from backtest.engine import Trade
from live.feed import LiveFeed
from live.executor import KrakenExecutor
from live.risk import RiskManager
from live.pg_writer import PgWriter
from live import config
from live.exchange.kraken import CCXT_SYMBOLS

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("live")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
ASSETS = config.ASSETS
CAPITAL_PER_ASSET = config.CAPITAL_PER_ASSET
MIN_BARS = 200  # minimum candles needed for indicators
LOOP_INTERVAL_SEC = 60  # check every minute, act on new hourly candle

# ---------------------------------------------------------------------------
# SQLite trade log
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), "data", "live_trades.db")


def init_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            asset TEXT NOT NULL,
            strategy TEXT NOT NULL,
            direction TEXT NOT NULL,
            signal TEXT,
            entry_price REAL,
            stop_price REAL,
            target_price REAL,
            size_usd REAL,
            leverage REAL,
            exit_price REAL,
            exit_reason TEXT,
            pnl_usd REAL,
            status TEXT DEFAULT 'OPEN'
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            asset TEXT NOT NULL,
            strategy TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT
        )
    """)
    conn.commit()
    return conn


def log_decision(conn: sqlite3.Connection, asset: str, strategy: str, action: str, details: str = ""):
    conn.execute(
        "INSERT INTO decisions (ts, asset, strategy, action, details) VALUES (?, ?, ?, ?, ?)",
        (datetime.now(timezone.utc).isoformat(), asset, strategy, action, details),
    )
    conn.commit()


def log_trade_open(conn: sqlite3.Connection, asset: str, strategy: str, trade: Trade) -> int:
    cur = conn.execute(
        """INSERT INTO trades (ts, asset, strategy, direction, signal, entry_price,
           stop_price, target_price, size_usd, leverage, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')""",
        (
            datetime.now(timezone.utc).isoformat(),
            asset,
            strategy,
            trade.direction,
            trade.signal,
            trade.entry_price,
            trade.stop_price,
            trade.target_price,
            trade.size_usd,
            trade.leverage,
        ),
    )
    conn.commit()
    return cur.lastrowid


def log_trade_close(conn: sqlite3.Connection, trade_id: int, exit_price: float, exit_reason: str, pnl_usd: float):
    conn.execute(
        "UPDATE trades SET exit_price=?, exit_reason=?, pnl_usd=?, status='CLOSED' WHERE id=?",
        (exit_price, exit_reason, pnl_usd, trade_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Open position tracker
# ---------------------------------------------------------------------------
@dataclass
class LivePosition:
    trade_id: int  # SQLite row id
    asset: str
    strategy: str
    trade: Trade
    order_id: Optional[str] = None
    pg_trade_id: Optional[int] = None  # Postgres trade id
    sl_order_id: Optional[str] = None  # Exchange stop-loss order id
    tp_order_id: Optional[str] = None  # Exchange take-profit order id


# ---------------------------------------------------------------------------
# iMessage alerts
# ---------------------------------------------------------------------------
IMSG_CHAT_ID = "dan.k.ngo@gmail.com"


def send_imsg(message: str) -> None:
    """Send an iMessage alert. Swallows errors."""
    try:
        subprocess.run(
            ["imsg", "send", "--chat-id", IMSG_CHAT_ID, "--text", message],
            timeout=10,
            capture_output=True,
        )
    except Exception:
        log.warning("Failed to send iMessage alert", exc_info=True)


# ---------------------------------------------------------------------------
# Strategy wrappers
# ---------------------------------------------------------------------------
def make_squeeze_strategies() -> dict[str, SqueezeV15]:
    """Create one SqueezeV15 per asset."""
    strats = {}
    for asset in ASSETS:
        strats[asset] = SqueezeV15(asset_name=asset)
    return strats


def make_candle_strategies() -> dict[str, CandleV2_3]:
    """Create one CandleV2_3 per asset with MTF enabled."""
    strats = {}
    for asset in ASSETS:
        strats[asset] = CandleV2_3(
            # Config 3: R:R 2:3, score>=2, all indicators, MTF both
            min_score=2,
            stop_atr=2.0,
            target_atr=3.0,
            use_mtf=True,
            mtf_require="both",
            use_rsi=True, use_stoch_rsi=True, use_williams_r=True,
            use_macd=True, use_cci=True, use_ema_alignment=True,
            use_adx=True, use_bb=True, use_atr_percentile=True,
            use_keltner=True, use_volume=True, use_mfi=True,
            use_obv_slope=True, use_range_position=True, use_hh_ll=True,
            pattern_set="top5",
            adx_max=40,
            cooldown=12,
            time_exit_bars=144,
            base_leverage=2.0,
        )
    return strats


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
def size_position(
    signal: dict,
    price: float,
    risk_mgr: RiskManager,
    capital: float,
) -> float:
    """Calculate position size in USD from signal stop distance and risk budget."""
    stop = signal["stop"]
    stop_dist = abs(price - stop) / price if price > 0 else 0
    if stop_dist <= 0:
        return 0.0
    max_size = risk_mgr.max_position_size(capital, stop_dist)
    leverage = signal.get("leverage", 1.0)
    return min(max_size * leverage, capital * leverage)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
class LiveRunner:
    def __init__(self):
        load_dotenv(".env.demo")
        api_key = os.environ["KRAKEN_DEMO_API_KEY"]
        api_secret = os.environ["KRAKEN_DEMO_API_SECRET"]

        self.feed = LiveFeed()
        self.executor = KrakenExecutor(api_key, api_secret, demo=config.DEMO)
        self.risk_mgr = RiskManager(max_risk_per_trade=config.MAX_RISK_PER_TRADE)
        self.db = init_db()

        # Postgres dual-write
        self.pg = PgWriter()
        self.bot_id = self.pg.register_bot("crypto-live", {
            "assets": config.ASSETS,
            "capital": config.INITIAL_CAPITAL,
            "demo": config.DEMO,
            "strategies": ["candle_v2_3"],
        })  # type: Optional[int]

        # Strategies: one per asset per strategy type
        self.squeeze = make_squeeze_strategies()
        self.candle = make_candle_strategies()

        # Open positions: key = (asset, strategy_name)
        self.positions: dict[tuple[str, str], LivePosition] = {}

        # Data cache: asset -> DataFrame (accumulated hourly candles)
        self.candle_cache: dict[str, pd.DataFrame] = {}

        # Track last processed hour to avoid duplicate processing
        self.last_processed_hour: Optional[datetime] = None

        # Tick counters for heartbeat
        self._tick_signals = 0
        self._tick_opened = 0
        self._tick_closed = 0

        self._shutdown = False

    def run(self):
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        log.info("=== Live Runner starting (demo=%s) ===", config.DEMO)
        log.info("Capital: $%.0f ($%.0f per asset)", config.INITIAL_CAPITAL, CAPITAL_PER_ASSET)
        log.info("Assets: %s", ASSETS)

        # Sync positions with exchange on startup
        self._sync_positions()

        # Initial data load — get enough history for indicators
        self._load_initial_data()

        # Wire BTC data to squeeze strategies for cross-asset features
        if "BTC" in self.candle_cache:
            for asset, strat in self.squeeze.items():
                if asset != "BTC":
                    strat.set_btc_data(self.candle_cache["BTC"])

        log.info("Initial data loaded. Entering main loop.")

        while not self._shutdown:
            try:
                self._tick()
            except Exception:
                log.exception("Error in main loop tick")
            time.sleep(LOOP_INTERVAL_SEC)

        log.info("=== Shutdown complete ===")
        self.db.close()
        self.pg.close()

    def _handle_signal(self, signum, frame):
        log.info("Received signal %d, shutting down gracefully...", signum)
        self._shutdown = True

    def _sync_positions(self):
        """Reconcile local DB positions with Kraken on startup."""
        try:
            exchange_positions = self.executor.get_positions()
        except Exception:
            log.exception("Failed to fetch exchange positions for sync")
            return

        # Build map of exchange positions: ccxt_symbol -> pos dict
        exchange_map = {}  # type: dict[str, dict]
        for ep in exchange_positions:
            exchange_map[ep["symbol"]] = ep

        # Check DB for OPEN trades that Kraken doesn't know about
        cursor = self.db.execute("SELECT id, asset, strategy, direction, signal, entry_price, stop_price, target_price, size_usd, leverage FROM trades WHERE status='OPEN'")
        db_open = cursor.fetchall()

        for row in db_open:
            trade_id, asset, strategy, direction, sig, entry, stop, target, size_usd, leverage = row
            ccxt_sym = CCXT_SYMBOLS.get(asset.upper(), "")
            if ccxt_sym not in exchange_map:
                # DB has position, exchange doesn't — mark closed
                log.warning("SYNC: %s %s position in DB but not on exchange — marking closed", asset, strategy)
                log_trade_close(self.db, trade_id, entry, "SYNC_CLOSED", 0.0)
                continue

            # Exchange has this position — load it into self.positions
            ep = exchange_map.pop(ccxt_sym)
            trade = Trade(
                entry_time=pd.Timestamp.now(tz="UTC"),
                entry_price=ep["entry_price"] if ep["entry_price"] > 0 else entry,
                direction=direction,
                signal=sig or strategy,
                stop_price=stop,
                target_price=target,
                size_usd=size_usd,
                leverage=leverage if leverage else 1.0,
            )
            key = (asset, strategy)
            self.positions[key] = LivePosition(
                trade_id=trade_id,
                asset=asset,
                strategy=strategy,
                trade=trade,
            )
            log.info("SYNC: Loaded %s %s %s position from DB (entry=%.2f)", asset, strategy, direction, trade.entry_price)

        # Exchange has positions the bot doesn't know about — load them
        for ccxt_sym, ep in exchange_map.items():
            # Reverse lookup asset name from ccxt symbol
            asset_name = None
            for a, s in CCXT_SYMBOLS.items():
                if s == ccxt_sym:
                    asset_name = a
                    break
            if asset_name is None:
                log.warning("SYNC: Unknown exchange position %s — skipping", ccxt_sym)
                continue

            direction = "LONG" if ep["side"] == "long" else "SHORT"
            trade = Trade(
                entry_time=pd.Timestamp.now(tz="UTC"),
                entry_price=ep["entry_price"],
                direction=direction,
                signal="synced",
                stop_price=0.0,
                target_price=0.0,
                size_usd=ep["size"] * ep["entry_price"],
                leverage=1.0,
            )
            # Log to SQLite
            trade_id = log_trade_open(self.db, asset_name, "synced", trade)
            key = (asset_name, "synced")
            self.positions[key] = LivePosition(
                trade_id=trade_id,
                asset=asset_name,
                strategy="synced",
                trade=trade,
            )
            log.warning("SYNC: Found exchange position %s %s (%.4f contracts) NOT in DB — loaded as synced",
                        asset_name, direction, ep["size"])

        if self.positions:
            log.info("SYNC: %d active positions after reconciliation", len(self.positions))
        else:
            log.info("SYNC: No open positions")

    def _load_initial_data(self):
        """Fetch initial candle history for all assets."""
        for asset in ASSETS:
            try:
                df = self.feed.get_candles(asset, "1h")
                if len(df) > 0:
                    self.candle_cache[asset] = df
                    log.info("  %s: loaded %d hourly candles", asset, len(df))
                else:
                    log.warning("  %s: no candles returned", asset)
            except Exception:
                log.exception("  %s: failed to load candles", asset)

    def _tick(self):
        """Single iteration: fetch new candle, run strategies, manage positions."""
        now = datetime.now(timezone.utc)
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        # Only process once per hour (with 2-min grace for candle finalization)
        if now.minute < 2:
            return
        if self.last_processed_hour and self.last_processed_hour >= current_hour:
            return

        log.info("--- Processing hour: %s ---", current_hour.isoformat())
        self.last_processed_hour = current_hour

        # Fetch latest candles for all assets
        for asset in ASSETS:
            try:
                df = self.feed.get_candles(asset, "1h")
                if len(df) > 0:
                    self.candle_cache[asset] = df
            except Exception:
                log.exception("Failed to fetch %s candles", asset)

        # Wire BTC data for cross-asset
        if "BTC" in self.candle_cache:
            for asset, strat in self.squeeze.items():
                if asset != "BTC":
                    strat.set_btc_data(self.candle_cache["BTC"])

        # Reset tick counters
        self._tick_signals = 0
        self._tick_opened = 0
        self._tick_closed = 0

        # Process each asset
        for asset in ASSETS:
            if asset not in self.candle_cache:
                continue
            df = self.candle_cache[asset]
            if len(df) < MIN_BARS:
                log.warning("%s: only %d bars, need %d", asset, len(df), MIN_BARS)
                continue

            i = len(df) - 1
            price = float(df.iloc[i]["close"])

            # Check exits on open positions first
            self._check_exits(asset, df, i, price)

            # Generate signals from strategies
            # self._run_strategy(asset, "squeeze_v15", self.squeeze[asset], df, i, price)  # V15 disabled per Dan
            self._run_strategy(asset, "candle_v2_3", self.candle[asset], df, i, price)
            self._tick_signals += 1

            # Log equity to Postgres
            if self.bot_id is not None:
                open_count = sum(1 for pos in self.positions.values() if pos.asset == asset)
                self.pg.log_equity(self.bot_id, asset, CAPITAL_PER_ASSET, 0.0, open_count)

        # Heartbeat
        if self.bot_id is not None:
            total_equity = CAPITAL_PER_ASSET * len(ASSETS)
            self.pg.log_heartbeat(
                self.bot_id, self._tick_signals,
                self._tick_opened, self._tick_closed, total_equity,
            )

    def _check_exits(self, asset: str, df: pd.DataFrame, i: int, price: float):
        """Check if any open positions should be closed."""
        keys_to_close = []
        for key, pos in self.positions.items():
            if pos.asset != asset:
                continue
            trade = pos.trade

            # Check strategy exit signal
            if pos.strategy == "squeeze_v15":
                exit_reason = self.squeeze[asset].check_exit(df, i, trade)
            else:
                exit_reason = self.candle[asset].check_exit(df, i, trade)

            # Also check hard stop/target
            if exit_reason is None:
                if trade.direction == "LONG":
                    if price <= trade.stop_price:
                        exit_reason = "STOP"
                    elif trade.target_price > 0 and price >= trade.target_price:
                        exit_reason = "TARGET"
                else:
                    if price >= trade.stop_price:
                        exit_reason = "STOP"
                    elif trade.target_price > 0 and price <= trade.target_price:
                        exit_reason = "TARGET"

            if exit_reason and not exit_reason.startswith("PYRAMID"):
                self._close_position(key, pos, price, exit_reason)
                keys_to_close.append(key)

        for k in keys_to_close:
            del self.positions[k]

    def _close_position(self, key: tuple, pos: LivePosition, price: float, exit_reason: str):
        """Close a position: cancel SL/TP, place reduce-only order, log, alert."""
        trade = pos.trade
        close_side = "sell" if trade.direction == "LONG" else "buy"
        size_contracts = trade.size_usd / trade.entry_price if trade.entry_price > 0 else 0

        if trade.direction == "LONG":
            pnl_pct = (price - trade.entry_price) / trade.entry_price
        else:
            pnl_pct = (trade.entry_price - price) / trade.entry_price
        pnl_usd = pnl_pct * trade.size_usd * trade.leverage

        log.info(
            "CLOSE %s %s %s | exit=%s price=%.2f pnl=$%.2f (%.2f%%)",
            pos.asset, pos.strategy, trade.direction,
            exit_reason, price, pnl_usd, pnl_pct * 100,
        )

        # Cancel exchange SL/TP orders before closing
        self._cancel_sl_tp(pos)

        try:
            self.executor.place_order(
                symbol=pos.asset,
                side=close_side,
                size=abs(size_contracts),
                order_type="mkt",
                reduce_only=True,
            )
        except Exception:
            log.exception("Failed to close %s %s position on exchange", pos.asset, pos.strategy)

        # SQLite
        log_trade_close(self.db, pos.trade_id, price, exit_reason, pnl_usd)

        # Postgres
        if pos.pg_trade_id is not None:
            self.pg.log_trade_close(pos.pg_trade_id, price, exit_reason, pnl_usd, pnl_pct * 100)

        # iMessage alert
        sign = "+" if pnl_usd >= 0 else ""
        msg = (
            f"\U0001f534 CLOSE {pos.asset} {trade.direction} @ ${price:.2f} | "
            f"{sign}${pnl_usd:.0f} ({sign}{pnl_pct * 100:.1f}%) | reason: {exit_reason}"
        )
        send_imsg(msg)

        self._tick_closed += 1

        # Report to strategy's adaptive manager
        if pos.strategy == "squeeze_v15":
            self.squeeze[pos.asset].record_trade(trade.direction, pnl_pct * 100, exit_reason)

        log_decision(self.db, pos.asset, pos.strategy, "CLOSE", f"{exit_reason} pnl=${pnl_usd:.2f}")

    def _cancel_sl_tp(self, pos: LivePosition) -> None:
        """Cancel exchange stop-loss and take-profit orders for a position."""
        for label, oid in [("SL", pos.sl_order_id), ("TP", pos.tp_order_id)]:
            if oid is None:
                continue
            try:
                self.executor.cancel_order(oid, pos.asset)
                log.info("Cancelled %s order %s for %s", label, oid, pos.asset)
            except Exception:
                log.warning("Failed to cancel %s order %s for %s", label, oid, pos.asset, exc_info=True)
        pos.sl_order_id = None
        pos.tp_order_id = None

    def _place_sl_tp(self, pos: LivePosition) -> None:
        """Place exchange stop-loss and take-profit orders for a position."""
        trade = pos.trade
        close_side = "sell" if trade.direction == "LONG" else "buy"
        size_contracts = trade.size_usd / trade.entry_price if trade.entry_price > 0 else 0
        if size_contracts <= 0:
            return

        # Stop-loss
        if trade.stop_price and trade.stop_price > 0:
            try:
                sl_order = self.executor.place_stop_order(
                    symbol=pos.asset,
                    side=close_side,
                    size=abs(size_contracts),
                    stop_price=trade.stop_price,
                )
                pos.sl_order_id = sl_order.get("id", "")
                log.info("Placed SL order %s for %s @ %.2f", pos.sl_order_id, pos.asset, trade.stop_price)
            except Exception:
                log.warning("Failed to place SL order for %s", pos.asset, exc_info=True)

        # Take-profit
        if trade.target_price and trade.target_price > 0:
            try:
                tp_order = self.executor.place_take_profit_order(
                    symbol=pos.asset,
                    side=close_side,
                    size=abs(size_contracts),
                    tp_price=trade.target_price,
                )
                pos.tp_order_id = tp_order.get("id", "")
                log.info("Placed TP order %s for %s @ %.2f", pos.tp_order_id, pos.asset, trade.target_price)
            except Exception:
                log.warning("Failed to place TP order for %s", pos.asset, exc_info=True)

    def _run_strategy(
        self, asset: str, strat_name: str, strat, df: pd.DataFrame, i: int, price: float
    ):
        """Run a strategy and open position if signal fires."""
        key = (asset, strat_name)

        # Skip if already have a position for this asset+strategy
        if key in self.positions:
            return

        # Check risk: drawdown limit
        if not self.risk_mgr.check_drawdown(asset, CAPITAL_PER_ASSET):
            log_decision(self.db, asset, strat_name, "SKIP", "DD limit breached")
            return

        # Generate signal
        sig = strat.generate_signal(df, i)
        if sig is None:
            return

        direction = sig["action"]
        stop = sig["stop"]
        target = sig["target"]
        leverage = sig.get("leverage", 1.0)
        signal_name = sig.get("signal", strat_name)

        # Size position
        size_usd = size_position(sig, price, self.risk_mgr, CAPITAL_PER_ASSET)
        if size_usd <= 0:
            log_decision(self.db, asset, strat_name, "SKIP", f"Size=0 for {signal_name}")
            return

        # Create trade object
        trade = Trade(
            entry_time=df.index[i],
            entry_price=price,
            direction=direction,
            signal=signal_name,
            stop_price=stop,
            target_price=target,
            size_usd=size_usd,
            leverage=leverage,
        )

        # Execute on exchange
        order_side = "buy" if direction == "LONG" else "sell"
        size_contracts = size_usd / price if price > 0 else 0

        log.info(
            "OPEN %s %s %s | signal=%s price=%.2f stop=%.2f target=%.2f size=$%.0f lev=%.1fx",
            asset, strat_name, direction, signal_name,
            price, stop, target, size_usd, leverage,
        )

        try:
            order = self.executor.place_order(
                symbol=asset,
                side=order_side,
                size=size_contracts,
                order_type="mkt",
            )
            order_id = order.get("id", "")
        except Exception:
            log.exception("Failed to place %s %s order", asset, strat_name)
            log_decision(self.db, asset, strat_name, "ERROR", f"Order failed for {signal_name}")
            return

        # Track position
        trade_id = log_trade_open(self.db, asset, strat_name, trade)

        # Postgres dual-write
        pg_trade_id = None  # type: Optional[int]
        if self.bot_id is not None:
            pg_trade_id = self.pg.log_trade_open(
                self.bot_id, asset, strat_name, direction, signal_name,
                price, stop, target, size_usd, leverage,
            )

        pos = LivePosition(
            trade_id=trade_id,
            asset=asset,
            strategy=strat_name,
            trade=trade,
            order_id=order_id,
            pg_trade_id=pg_trade_id,
        )
        self.positions[key] = pos

        # Place exchange SL/TP orders for crash protection
        self._place_sl_tp(pos)

        # iMessage alert
        msg = (
            f"\U0001f7e2 OPEN {asset} {direction} @ ${price:.2f} | "
            f"stop ${stop:.2f} target ${target:.2f} | ${size_usd:.0f} {leverage:.1f}x"
        )
        send_imsg(msg)

        self._tick_opened += 1
        log_decision(self.db, asset, strat_name, "OPEN", f"{signal_name} size=${size_usd:.0f}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    runner = LiveRunner()
    runner.run()
