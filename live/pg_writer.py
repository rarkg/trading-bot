"""Postgres writer for the trading monitor dashboard.

Writes trade/equity/heartbeat data to Postgres (sole database).
Graceful: if Postgres is down, logs a warning and continues (never crashes the bot).
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

log = logging.getLogger("live.pg")

DSN = "postgresql://elio@localhost:5432/trading_monitor"


class PgWriter:
    """Graceful Postgres writer — all public methods swallow exceptions."""

    def __init__(self, dsn: str = DSN) -> None:
        self.dsn = dsn
        self._conn = None  # type: Optional[psycopg2.extensions.connection]
        self._connect()

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------
    def _connect(self) -> None:
        try:
            self._conn = psycopg2.connect(self.dsn)
            self._conn.autocommit = True
            log.info("Connected to Postgres (%s)", self.dsn)
        except Exception:
            log.warning("Postgres unavailable — will retry on next write", exc_info=True)
            self._conn = None

    def _ensure_conn(self) -> bool:
        """Return True if we have a usable connection."""
        if self._conn is not None:
            try:
                with self._conn.cursor() as cur:
                    cur.execute("SELECT 1")
                return True
            except Exception:
                self._conn = None
        # Try to reconnect
        self._connect()
        return self._conn is not None

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ------------------------------------------------------------------
    # Public API (all graceful)
    # ------------------------------------------------------------------
    def register_bot(self, name: str, config: dict) -> Optional[int]:
        """Register or update a bot. Returns bot_id or None on failure."""
        if not self._ensure_conn():
            return None
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO bots (name, config, started_at, updated_at)
                       VALUES (%s, %s, %s, NOW())
                       ON CONFLICT (name) DO UPDATE
                         SET config = EXCLUDED.config,
                             status = 'running',
                             started_at = EXCLUDED.started_at,
                             updated_at = NOW()
                       RETURNING id""",
                    (name, json.dumps(config), datetime.now(timezone.utc)),
                )
                row = cur.fetchone()
                bot_id = row[0] if row else None
                log.info("Registered bot '%s' with id=%s", name, bot_id)
                return bot_id
        except Exception:
            log.warning("Failed to register bot in Postgres", exc_info=True)
            return None

    def confirm_trade_open(self, pg_trade_id: int, actual_fill_price: float) -> None:
        """Confirm a PENDING trade is now OPEN with the actual fill price."""
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """UPDATE bot_trades
                       SET status='OPEN', entry_price=%s
                       WHERE id=%s AND status='PENDING'""",
                    (float(actual_fill_price), pg_trade_id),
                )
                self._conn.commit()
        except Exception:
            log.warning("Failed to confirm trade open in Postgres", exc_info=True)

    def cancel_pending_trade(self, pg_trade_id: int, reason: str) -> None:
        """Mark a PENDING trade as FAILED (order rejected/not filled)."""
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """UPDATE bot_trades
                       SET status='FAILED', exit_reason=%s, closed_at=%s
                       WHERE id=%s AND status='PENDING'""",
                    (reason, datetime.now(timezone.utc), pg_trade_id),
                )
                self._conn.commit()
        except Exception:
            log.warning("Failed to cancel pending trade in Postgres", exc_info=True)

    def log_trade_open(
        self,
        bot_id: int,
        asset: str,
        strategy: str,
        direction: str,
        signal: str,
        entry_price: float,
        stop_price: float,
        target_price: float,
        size_usd: float,
        leverage: float,
    ) -> Optional[int]:
        """Log a trade open. Returns the Postgres trade id or None."""
        if not self._ensure_conn():
            return None
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO bot_trades
                       (bot_id, asset, strategy, direction, signal,
                        entry_price, stop_price, target_price, size_usd,
                        leverage, status, opened_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id""",
                    (
                        bot_id, asset, strategy, direction, signal,
                        float(entry_price), float(stop_price) if stop_price is not None else None,
                        float(target_price) if target_price is not None else None,
                        float(size_usd), float(leverage), 'PENDING', datetime.now(timezone.utc),
                    ),
                )
                row = cur.fetchone()
                return row[0] if row else None
        except Exception:
            log.warning("Failed to log trade open in Postgres", exc_info=True)
            return None

    def log_trade_close(
        self,
        pg_trade_id: int,
        exit_price: float,
        exit_reason: str,
        pnl_usd: float,
        pnl_pct: float,
    ) -> None:
        """Log a trade close."""
        if not self._ensure_conn():
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """UPDATE bot_trades
                       SET exit_price=%s, exit_reason=%s, pnl_usd=%s, pnl_pct=%s,
                           status='CLOSED', closed_at=%s
                       WHERE id=%s""",
                    (exit_price, exit_reason, pnl_usd, pnl_pct,
                     datetime.now(timezone.utc), pg_trade_id),
                )
        except Exception:
            log.warning("Failed to log trade close in Postgres", exc_info=True)

    def log_equity(
        self,
        bot_id: int,
        asset: str,
        equity: float,
        pnl_total: float,
        open_positions: int,
    ) -> None:
        """Log per-asset equity snapshot."""
        if not self._ensure_conn():
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO bot_equity
                       (bot_id, asset, equity, pnl_total, open_positions)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (bot_id, asset, equity, pnl_total, open_positions),
                )
        except Exception:
            log.warning("Failed to log equity in Postgres", exc_info=True)

    def log_heartbeat(
        self,
        bot_id: int,
        signals_evaluated: int,
        trades_opened: int,
        trades_closed: int,
        total_equity: float,
    ) -> None:
        """Log a heartbeat."""
        if not self._ensure_conn():
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO bot_heartbeats
                       (bot_id, signals_evaluated, trades_opened, trades_closed, total_equity)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (bot_id, signals_evaluated, trades_opened, trades_closed, total_equity),
                )
        except Exception:
            log.warning("Failed to log heartbeat in Postgres", exc_info=True)

    def log_decision(
        self,
        bot_id: int,
        asset: str,
        strategy: str,
        action: str,
        details: str = "",
    ) -> None:
        """Log a decision (SKIP, ERROR, OPEN, CLOSE)."""
        # Decisions go to log only — no separate table needed
        log.info("DECISION %s %s %s: %s", asset, strategy, action, details)

    def cancel_stale_pending_trades(self, bot_id: int, older_than_minutes: int = 5) -> int:
        """Cancel PENDING trades older than N minutes — order never confirmed.
        Returns number of trades cancelled."""
        if not self._ensure_conn():
            return 0
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """UPDATE bot_trades
                       SET status='CLOSED', exit_reason='PENDING_TIMEOUT', closed_at=NOW()
                       WHERE bot_id=%s AND status='PENDING'
                         AND created_at < NOW() - INTERVAL '%s minutes'""",
                    (bot_id, older_than_minutes),
                )
                count = cur.rowcount
                if count:
                    log.warning("Cancelled %d stale PENDING trades (>%dm old)", count, older_than_minutes)
                return count
        except Exception:
            log.warning("Failed to cancel stale pending trades", exc_info=True)
            return 0

    def get_total_equity(self, bot_id: int) -> float:
        """Return total portfolio equity (sum across all assets) from latest snapshot."""
        if not self._ensure_conn():
            return 0.0
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """SELECT COALESCE(SUM(equity), 0)
                       FROM bot_equity
                       WHERE bot_id = %s
                         AND recorded_at = (SELECT MAX(recorded_at) FROM bot_equity WHERE bot_id = %s)""",
                    (bot_id, bot_id),
                )
                row = cur.fetchone()
                return float(row[0]) if row else 0.0
        except Exception:
            log.warning("Failed to get total equity from Postgres", exc_info=True)
            return 0.0

    def get_open_trades(self, bot_id: int) -> list:
        """Get all open trades for a bot. Returns list of dicts."""
        if not self._ensure_conn():
            return []
        try:
            with self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """SELECT id, asset, strategy, direction, signal,
                              entry_price, stop_price, target_price,
                              size_usd, leverage, opened_at
                       FROM bot_trades
                       WHERE bot_id=%s AND status='OPEN'
                       ORDER BY opened_at""",
                    (bot_id,),
                )
                return [dict(row) for row in cur.fetchall()]
        except Exception:
            log.warning("Failed to get open trades from Postgres", exc_info=True)
            return []

    def update_trade_entry(self, trade_id: int, entry_price: float, size_usd: float) -> None:
        """Correct entry price and size with actual exchange values."""
        if not self._ensure_conn():
            return
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """UPDATE bot_trades
                       SET entry_price=%s, size_usd=%s
                       WHERE id=%s AND status='OPEN'""",
                    (entry_price, size_usd, trade_id),
                )
        except Exception:
            log.warning("Failed to update trade entry %s", trade_id, exc_info=True)

    def close_trade_by_id(self, trade_id: int, exit_reason: str = "SYNC_CLOSED",
                          exit_price: float = None, pnl_usd: float = None,
                          closed_at=None) -> None:
        """Mark a trade as closed, optionally recording exit price, PnL, and exact close time."""
        if not self._ensure_conn():
            return
        effective_closed_at = closed_at if closed_at is not None else datetime.now(timezone.utc)
        try:
            with self._conn.cursor() as cur:
                cur.execute(
                    """UPDATE bot_trades
                       SET status='CLOSED', exit_reason=%s, closed_at=%s,
                           exit_price=COALESCE(%s, exit_price),
                           pnl_usd=COALESCE(%s, pnl_usd)
                       WHERE id=%s AND status='OPEN'""",
                    (exit_reason, effective_closed_at, exit_price, pnl_usd, trade_id),
                )
        except Exception:
            log.warning("Failed to close trade %s in Postgres", trade_id, exc_info=True)
