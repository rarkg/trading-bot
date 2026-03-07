#!/usr/bin/env python3
"""Exchange Sync Service — reconciles exchange state with Postgres every 60s.

Fetches balance, positions, and orders from Kraken via ExchangeAdapter.
Writes to exchange_positions and exchange_orders tables.
Reconciles: OPEN trades with no exchange position -> CLOSED.
            PENDING trades > 5 min with no exchange position -> FAILED.

Runs as a standalone pm2 service (exchange-sync).
"""

import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live.exchange_adapter import KrakenAdapter
from live.executor import KrakenExecutor
from live.pg_writer import PgWriter
from live import config

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("exchange-sync")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SYNC_INTERVAL_SEC = 60
PENDING_TIMEOUT_SEC = 300  # 5 minutes

DSN = "postgresql://elio@localhost:5432/trading_monitor"


# ---------------------------------------------------------------------------
# Exchange Sync Service
# ---------------------------------------------------------------------------
class ExchangeSyncService:
    def __init__(self):
        load_dotenv(".env.demo")
        api_key = os.environ.get("KRAKEN_DEMO_API_KEY", "")
        api_secret = os.environ.get("KRAKEN_DEMO_API_SECRET", "")

        executor = KrakenExecutor(api_key, api_secret, demo=config.DEMO)
        self.adapter = KrakenAdapter(executor)
        self.pg = PgWriter(DSN)

        self._conn = None  # type: object
        self._shutdown = False
        self._connect_pg()
        self._ensure_tables()

    def _connect_pg(self):
        import psycopg2
        try:
            self._conn = psycopg2.connect(DSN)
            self._conn.autocommit = True
        except Exception:
            log.warning("Postgres unavailable for exchange sync", exc_info=True)
            self._conn = None

    def _ensure_conn(self):
        # type: () -> bool
        if self._conn is not None:
            try:
                with self._conn.cursor() as cur:
                    cur.execute("SELECT 1")
                return True
            except Exception:
                self._conn = None
        self._connect_pg()
        return self._conn is not None

    def _ensure_tables(self):
        if not self._ensure_conn():
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS exchange_positions (
                        id SERIAL PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        size DOUBLE PRECISION NOT NULL,
                        entry_price DOUBLE PRECISION,
                        pnl DOUBLE PRECISION,
                        synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (symbol)
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS exchange_orders (
                        id SERIAL PRIMARY KEY,
                        order_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        order_type TEXT,
                        amount DOUBLE PRECISION,
                        price DOUBLE PRECISION,
                        stop_price DOUBLE PRECISION,
                        status TEXT,
                        synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        UNIQUE (order_id)
                    )
                """)
        except Exception:
            log.warning("Failed to create exchange sync tables", exc_info=True)

    def run(self):
        signal.signal(signal.SIGINT, self._handle_signal)
        signal.signal(signal.SIGTERM, self._handle_signal)

        log.info("=== Exchange Sync starting (interval=%ds) ===", SYNC_INTERVAL_SEC)

        while not self._shutdown:
            try:
                self._sync_once()
            except Exception:
                log.exception("Error in sync loop")
            time.sleep(SYNC_INTERVAL_SEC)

        log.info("=== Exchange Sync stopped ===")

    def _handle_signal(self, signum, frame):
        log.info("Received signal %d, shutting down...", signum)
        self._shutdown = True

    def _sync_once(self):
        now = datetime.now(timezone.utc)

        # 1. Sync balance -> bot_equity (asset='ACCOUNT')
        try:
            bal = self.adapter.get_balance()
            total = bal.get("USD_total", 0.0)
            if total > 0:
                # Find bot_id for crypto-live
                bot_id = self._get_bot_id("crypto-live")
                if bot_id is not None:
                    self.pg.log_equity(bot_id, "ACCOUNT", total, 0.0, 0)
                log.info("Balance synced: $%.2f", total)
        except Exception:
            log.warning("Failed to sync balance", exc_info=True)

        # 2. Sync positions -> exchange_positions
        try:
            positions = self.adapter.get_positions()
            self._upsert_positions(positions, now)
            log.info("Positions synced: %d open", len(positions))
        except Exception:
            log.warning("Failed to sync positions", exc_info=True)
            positions = []

        # 3. Sync open orders -> exchange_orders
        try:
            orders = self.adapter.get_open_orders()
            self._upsert_orders(orders, now)
            log.info("Orders synced: %d open", len(orders))
        except Exception:
            log.warning("Failed to sync orders", exc_info=True)

        # 4. Reconcile bot_trades vs exchange positions
        self._reconcile(positions, now)

    def _get_bot_id(self, name):
        # type: (str) -> object
        if not self._ensure_conn():
            return None
        try:
            with self._conn.cursor() as cur:
                cur.execute("SELECT id FROM bots WHERE name=%s", (name,))
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            return None

    def _upsert_positions(self, positions, now):
        if not self._ensure_conn():
            return
        try:
            with self._conn.cursor() as cur:
                # Clear old positions first (full snapshot replacement)
                cur.execute("DELETE FROM exchange_positions")
                for pos in positions:
                    cur.execute(
                        """INSERT INTO exchange_positions
                           (symbol, side, size, entry_price, pnl, synced_at)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (symbol) DO UPDATE SET
                             side=EXCLUDED.side, size=EXCLUDED.size,
                             entry_price=EXCLUDED.entry_price, pnl=EXCLUDED.pnl,
                             synced_at=EXCLUDED.synced_at""",
                        (
                            pos["symbol"], pos["side"], pos["size"],
                            pos.get("entry_price", 0), pos.get("pnl", 0), now,
                        ),
                    )
        except Exception:
            log.warning("Failed to upsert exchange positions", exc_info=True)

    def _upsert_orders(self, orders, now):
        if not self._ensure_conn():
            return
        try:
            with self._conn.cursor() as cur:
                # Clear stale orders, replace with current snapshot
                cur.execute("DELETE FROM exchange_orders")
                for order in orders:
                    oid = order.get("id", "")
                    if not oid:
                        continue
                    cur.execute(
                        """INSERT INTO exchange_orders
                           (order_id, symbol, side, order_type, amount, price, stop_price, status, synced_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (order_id) DO UPDATE SET
                             status=EXCLUDED.status, synced_at=EXCLUDED.synced_at""",
                        (
                            oid,
                            order.get("symbol", ""),
                            order.get("side", ""),
                            order.get("type", ""),
                            order.get("amount", 0),
                            order.get("price"),
                            order.get("stopPrice"),
                            order.get("status", ""),
                            now,
                        ),
                    )
        except Exception:
            log.warning("Failed to upsert exchange orders", exc_info=True)

    def _reconcile(self, exchange_positions, now):
        """Reconcile bot_trades with exchange state.

        - OPEN trades with no exchange position -> mark CLOSED (SYNC_CLOSED)
        - PENDING trades > 5 min old with no exchange position -> mark FAILED
        """
        if not self._ensure_conn():
            return

        # Build set of exchange position symbols
        exchange_syms = set()
        for pos in exchange_positions:
            exchange_syms.add(pos.get("symbol", ""))

        bot_id = self._get_bot_id("crypto-live")
        if bot_id is None:
            return

        try:
            import psycopg2.extras
            with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # Check OPEN trades
                cur.execute(
                    """SELECT id, asset, strategy, direction
                       FROM bot_trades
                       WHERE bot_id=%s AND status='OPEN'""",
                    (bot_id,),
                )
                open_trades = cur.fetchall()

                from live.exchange.kraken import CCXT_SYMBOLS
                for trade in open_trades:
                    asset = trade["asset"]
                    ccxt_sym = CCXT_SYMBOLS.get(asset.upper(), "")
                    if ccxt_sym and ccxt_sym not in exchange_syms:
                        log.warning(
                            "RECONCILE: %s %s OPEN in DB but not on exchange -> CLOSED",
                            asset, trade["strategy"],
                        )
                        self.pg.close_trade_by_id(trade["id"], "SYNC_CLOSED")

                # Check PENDING trades > 5 min
                cur.execute(
                    """SELECT id, asset, strategy, opened_at
                       FROM bot_trades
                       WHERE bot_id=%s AND status='PENDING'""",
                    (bot_id,),
                )
                pending_trades = cur.fetchall()

                for trade in pending_trades:
                    opened_at = trade["opened_at"]
                    if opened_at is None:
                        continue
                    age_sec = (now - opened_at).total_seconds()
                    if age_sec > PENDING_TIMEOUT_SEC:
                        asset = trade["asset"]
                        ccxt_sym = CCXT_SYMBOLS.get(asset.upper(), "")
                        if not ccxt_sym or ccxt_sym not in exchange_syms:
                            log.warning(
                                "RECONCILE: %s %s PENDING for %.0fs with no exchange position -> FAILED",
                                asset, trade["strategy"], age_sec,
                            )
                            self.pg.cancel_pending_trade(
                                trade["id"],
                                "Pending timeout (%.0fs, no exchange position)" % age_sec,
                            )

        except Exception:
            log.warning("Failed to reconcile trades", exc_info=True)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    svc = ExchangeSyncService()
    svc.run()
