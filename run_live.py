#!/usr/bin/env python3
"""Live trading runner — feeds V15 squeeze + V2.3 candle strategies into Kraken demo.

Loops every hour:
  1. Fetch latest 1h candles for BTC/ETH/SOL/LINK
  2. Feed to both strategies
  3. If signal → check risk → size → execute on Kraken demo
  4. Check open positions for stop/target hits
  5. Log to console + Postgres
"""

import os
import sys
import signal
import subprocess
import time
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List

import pandas as pd
from dotenv import load_dotenv

from strategies.squeeze_v15 import SqueezeV15
from strategies.candle_v2_6 import CandleV2_6
from backtest.engine import Trade
from live.feed import LiveFeed
from live.executor import KrakenExecutor
from live.risk import RiskManager
from live.pg_writer import PgWriter
from live import config
from live.exchange.kraken import CCXT_SYMBOLS
from live.adaptive_sizer import AdaptiveSizer
from live.regime import RegimeDetector

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
# Open position tracker
# ---------------------------------------------------------------------------
@dataclass
class LivePosition:
    trade_id: Optional[int]  # Postgres trade id
    asset: str
    strategy: str
    trade: Trade
    order_id: Optional[str] = None
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


def make_candle_strategies() -> dict[str, CandleV2_6]:
    """Create one CandleV2_6 per asset with MTF enabled."""
    strats = {}
    for asset in ASSETS:
        strats[asset] = CandleV2_6(
            # V2.5: tighter trail, score 1, adx 50, R:R 2:4
            min_score=1,
            stop_atr=2.0,
            target_atr=4.0,
            use_mtf=True,
            mtf_require="both",
            use_rsi=True, use_stoch_rsi=True, use_williams_r=True,
            use_macd=True, use_cci=True, use_ema_alignment=True,
            use_adx=True, use_bb=True, use_atr_percentile=True,
            use_keltner=True, use_volume=True, use_mfi=True,
            use_obv_slope=True, use_range_position=True, use_hh_ll=True,
            pattern_set="top5",
            adx_max=50,
            cooldown=0,  # disabled temporarily
            time_exit_bars=144,
            base_leverage=2.0,
            # V2.5 trailing stop (tighter trail)
            use_trailing_stop=True,
            trail_activation_atr=1.5,
            trail_distance_atr=0.3,
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

        # Postgres
        self.pg = PgWriter()
        self.bot_id = self.pg.register_bot("crypto-live", {
            "assets": config.ASSETS,
            "capital": config.INITIAL_CAPITAL,
            "demo": config.DEMO,
            "strategies": ["candle_v2_6"],
        })  # type: Optional[int]

        # Strategies: one per asset per strategy type
        self.squeeze = make_squeeze_strategies()
        self.candle = make_candle_strategies()

        # V2.5: Adaptive sizing + regime detection (disabled by default)
        self.adaptive_sizer = AdaptiveSizer(
            enabled=getattr(config, "USE_ADAPTIVE_SIZING", False),
        )
        self.regime_detector = RegimeDetector(
            enabled=getattr(config, "USE_REGIME_DETECTION", False),
        )

        # Open positions: key = (asset, strategy_name)
        self.positions: dict[tuple[str, str], LivePosition] = {}

        # Data cache: asset -> DataFrame (accumulated hourly candles)
        self.candle_cache: dict[str, pd.DataFrame] = {}

        # Track last processed hour to avoid duplicate processing
        self.last_processed_hour: Optional[datetime] = None
        # Track last candle timestamp per asset to avoid duplicate signals on same candle
        self._last_signal_candle: dict[str, object] = {}

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

        # Start background thread: write live position status every 10s
        import threading
        def _position_status_loop():
            while not self._shutdown:
                try:
                    self._write_live_status()
                except Exception:
                    pass
                time.sleep(10)
        threading.Thread(target=_position_status_loop, daemon=True).start()

        while not self._shutdown:
            try:
                self._tick()  # full scan: fetch candles + entries + exits every 10 min
            except Exception:
                log.exception("Error in main loop tick")
            time.sleep(900)  # 15 minute interval

        log.info("=== Shutdown complete ===")
        self.pg.close()

    def _handle_signal(self, signum, frame):
        log.info("Received signal %d, shutting down gracefully...", signum)
        self._shutdown = True

    def _sync_positions(self):
        """Reconcile Postgres positions with Kraken on startup."""
        try:
            exchange_positions = self.executor.get_positions()
        except Exception:
            log.exception("Failed to fetch exchange positions for sync")
            return

        # Cancel stale PENDING trades first (order never confirmed)
        if self.bot_id is not None:
            self.pg.cancel_stale_pending_trades(self.bot_id, older_than_minutes=5)

        # Build map of exchange positions: ccxt_symbol -> pos dict
        exchange_map = {}  # type: dict[str, dict]
        for ep in exchange_positions:
            exchange_map[ep["symbol"]] = ep

        # Check Postgres for OPEN trades that Kraken doesn't know about
        db_open = self.pg.get_open_trades(self.bot_id) if self.bot_id is not None else []

        for row in db_open:
            trade_id = row["id"]
            asset = row["asset"]
            strategy = row["strategy"]
            direction = row["direction"]
            sig = row["signal"]
            entry = row["entry_price"]
            stop = row["stop_price"]
            target = row["target_price"]
            size_usd = row["size_usd"]
            leverage = row["leverage"]

            ccxt_sym = CCXT_SYMBOLS.get(asset.upper(), "")
            if ccxt_sym not in exchange_map:
                # DB has position, exchange doesn't — mark closed
                log.warning("SYNC: %s %s position in DB but not on exchange — marking closed", asset, strategy)
                self.pg.close_trade_by_id(trade_id, "SYNC_CLOSED")
                continue

            # Exchange has this position — load it into self.positions
            ep = exchange_map.pop(ccxt_sym)
            # Use actual Kraken entry price and compute real size in USD
            actual_entry = ep["entry_price"] if ep["entry_price"] > 0 else entry
            actual_size = float(ep["size"]) * actual_entry if ep.get("size") and actual_entry > 0 else size_usd
            # Correct DB if entry price or size was inaccurate (e.g. signal-price fallback)
            if abs(actual_entry - entry) > 0.0001 or abs(actual_size - size_usd) > 1.0:
                log.info("SYNC: Correcting %s entry %.6f→%.6f size $%.2f→$%.2f", asset, entry, actual_entry, size_usd, actual_size)
                self.pg.update_trade_entry(trade_id, actual_entry, actual_size)
            trade = Trade(
                entry_time=pd.Timestamp.now(tz="UTC"),
                entry_price=actual_entry,
                direction=direction,
                signal=sig or strategy,
                stop_price=stop,
                target_price=target,
                size_usd=actual_size,
                leverage=leverage if leverage else 1.0,
            )
            key = (asset, strategy)
            self.positions[key] = LivePosition(
                trade_id=trade_id,
                asset=asset,
                strategy=strategy,
                trade=trade,
            )
            log.info("SYNC: Loaded %s %s %s entry=%.6f size=$%.2f", asset, strategy, direction, actual_entry, actual_size)

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
            # Log to Postgres
            trade_id = None
            if self.bot_id is not None:
                trade_id = self.pg.log_trade_open(
                    self.bot_id, asset_name, "synced", direction, "synced",
                    ep["entry_price"], 0.0, 0.0,
                    trade.size_usd, 1.0,
                )
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
        """Single iteration: fetch fresh candles and scan for entries every 10 min."""
        now = datetime.now(timezone.utc)
        current_hour = now.replace(minute=0, second=0, microsecond=0)

        log.info("--- Scanning: %s ---", now.strftime("%H:%M UTC"))
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

        # Check exits with live price first (before signal scan)
        self._fast_exit_check()

        # --- Reconcile DB vs Kraken every cycle ---
        # Any trade OPEN in DB but not on Kraken gets closed immediately
        self._reconcile_db_with_exchange()

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

            # self._run_strategy(asset, "squeeze_v15", self.squeeze[asset], df, i, price)  # V15 disabled per Dan
            self._run_strategy(asset, "candle_v2_6", self.candle[asset], df, i, price)
            self._tick_signals += 1

            # Log equity to Postgres — real equity = slot capital + unrealized PnL on open pos
            if self.bot_id is not None:
                open_count = sum(1 for pos in self.positions.values() if pos.asset == asset)
                asset_key = (asset, "candle_v2_6")
                unrealized = 0.0
                if asset_key in self.positions:
                    pos = self.positions[asset_key]
                    if pos.trade.direction == "LONG":
                        unrealized = (price - pos.trade.entry_price) / pos.trade.entry_price * pos.trade.size_usd
                    else:
                        unrealized = (pos.trade.entry_price - price) / pos.trade.entry_price * pos.trade.size_usd
                slot_equity = CAPITAL_PER_ASSET + unrealized
                self.pg.log_equity(self.bot_id, asset, slot_equity, unrealized, open_count)

        # Heartbeat — fetch real balance from Kraken as source of truth
        if self.bot_id is not None:
            try:
                balance = self.executor.client.exchange.fetch_balance()
                total_equity = float(balance.get("total", {}).get("USD", 0) or 0)
                unrealized = sum(
                    float(p.get("unrealizedPnl") or 0)
                    for p in self.executor.get_positions()
                )
                realized_pnl = total_equity - config.INITIAL_CAPITAL
                log.info("Account equity: $%.2f (started $%.0f, PnL $%+.2f)", total_equity, config.INITIAL_CAPITAL, realized_pnl)
                # Log as ACCOUNT row in bot_equity for dashboard
                self.pg.log_equity(self.bot_id, "ACCOUNT", total_equity, realized_pnl, len(self.positions))
            except Exception:
                log.warning("Failed to fetch Kraken balance for heartbeat", exc_info=True)
                total_equity = config.INITIAL_CAPITAL
            self.pg.log_heartbeat(
                self.bot_id, self._tick_signals,
                self._tick_opened, self._tick_closed, total_equity,
            )

    def _write_live_status(self) -> None:
        """Write current open positions + live PnL to /tmp/bot-live-status.json every 10s."""
        import json as _json
        positions_out = []
        for key, pos in list(self.positions.items()):
            try:
                ccxt_sym = CCXT_SYMBOLS.get(pos.asset.upper(), "")
                ticker = self.executor.client.exchange.fetch_ticker(ccxt_sym) if ccxt_sym else {}
                price = float(ticker.get("last") or ticker.get("close") or 0)
                t = pos.trade
                if price > 0:
                    if t.direction == "LONG":
                        unr_pct = (price - t.entry_price) / t.entry_price * 100
                    else:
                        unr_pct = (t.entry_price - price) / t.entry_price * 100
                    unr_usd = t.size_usd * unr_pct / 100
                else:
                    price = unr_pct = unr_usd = 0.0
                positions_out.append({
                    "asset": pos.asset,
                    "direction": t.direction,
                    "entry": round(t.entry_price, 6),
                    "current": round(price, 6),
                    "stop": round(t.stop_price, 6),
                    "target": round(t.target_price, 6),
                    "size_usd": round(t.size_usd, 2),
                    "unr_usd": round(unr_usd, 2),
                    "unr_pct": round(unr_pct, 3),
                    "trade_id": pos.trade_id,
                })
            except Exception:
                pass
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "positions": positions_out,
        }
        try:
            with open("/tmp/bot-live-status.json", "w") as f:
                _json.dump(payload, f)
        except Exception:
            pass

    def _fast_exit_check(self) -> None:
        """Check exits every 10 minutes using live ticker price (not waiting for new candle)."""
        if not self.positions:
            return
        for key, pos in list(self.positions.items()):
            try:
                ccxt_sym = CCXT_SYMBOLS.get(pos.asset.upper(), "")
                if not ccxt_sym:
                    continue
                ticker = self.executor.client.exchange.fetch_ticker(ccxt_sym)
                price = float(ticker.get("last") or ticker.get("close") or 0)
                if price <= 0:
                    continue
                t = pos.trade
                ex_r = ex_p = None
                if t.direction == "LONG":
                    if price <= t.stop_price:
                        ex_r, ex_p = "STOP", t.stop_price * (1 - 0.001)
                    elif price >= t.target_price:
                        ex_r, ex_p = "TARGET", t.target_price
                else:
                    if price >= t.stop_price:
                        ex_r, ex_p = "STOP", t.stop_price * (1 + 0.001)
                    elif price <= t.target_price:
                        ex_r, ex_p = "TARGET", t.target_price
                if ex_r:
                    log.info("FAST EXIT: %s %s hit %s at %.6f (live price=%.6f)", pos.asset, t.direction, ex_r, ex_p, price)
                    self._close_position(key, pos, ex_p, ex_r)
            except Exception:
                log.warning("Fast exit check failed for %s", pos.asset, exc_info=True)

    def _reconcile_db_with_exchange(self) -> None:
        """Every cycle: close any DB-OPEN trades that no longer exist on Kraken.
        Also sweeps dust positions (notional < $5) left behind by partial closes."""
        if self.bot_id is None:
            return
        try:
            # Dust sweep: close any exchange positions too small to manage
            DUST_THRESHOLD_USD = 5.0
            try:
                all_positions = self.executor.get_positions()
                for p in all_positions:
                    sym = p.get("symbol", "")
                    size = float(p.get("size", 0))
                    ep = float(p.get("entry_price", 0))
                    notional = size * ep
                    if 0 < notional < DUST_THRESHOLD_USD:
                        asset = sym.split("/")[0].upper()
                        close_side = "sell" if p.get("side", "long") == "long" else "buy"
                        log.warning("DUST: %s notional=$%.2f < $%.0f — closing", asset, notional, DUST_THRESHOLD_USD)
                        try:
                            from live.exchange.kraken import CCXT_SYMBOLS
                            ccxt_sym = CCXT_SYMBOLS.get(asset, asset + "/USD:USD")
                            self.executor.place_market_order(asset, close_side, size, reduce_only=True)
                            log.info("DUST: Closed %s dust position (%.6f contracts)", asset, size)
                        except Exception:
                            log.warning("DUST: Failed to close dust position for %s", asset, exc_info=True)
            except Exception:
                log.warning("DUST: Sweep failed", exc_info=True)

            exchange_positions = self.executor.get_positions()
            # symbol format is "ADA/USD:USD" — extract base asset (e.g. "ADA")
            exchange_assets = {p["symbol"].split("/")[0].upper() for p in exchange_positions if p.get("symbol")}

            db_open = self.pg.get_open_trades(self.bot_id)
            for row in db_open:
                asset = row["asset"].upper()
                trade_id = row["id"]
                if asset not in exchange_assets:
                    # In DB as OPEN but Kraken doesn't have it — fetch actual exit + compute PnL
                    log.warning("RECONCILE: %s is OPEN in DB (id=%d) but not on Kraken — computing exit PnL", asset, trade_id)
                    exit_price = None
                    pnl_usd = None
                    try:
                        from live.exchange.kraken import CCXT_SYMBOLS
                        ccxt_sym = CCXT_SYMBOLS.get(asset.upper(), asset + "/USD:USD")
                        pf_sym = f"pf_{asset.lower()}usd"

                        # Primary: account log has realized_pnl + trade_price per fill
                        result = self.executor.client.exchange.history_get_account_log({"count": 20})
                        for entry_log in result.get("logs", []):
                            if (entry_log.get("asset") == "usd"
                                    and entry_log.get("contract") == pf_sym
                                    and entry_log.get("info") == "futures trade"):
                                exit_price = float(entry_log.get("trade_price") or 0) or None
                                pnl_usd = float(entry_log.get("realized_pnl") or 0) or None
                                log.info("RECONCILE: %s account_log exit_price=%.6f pnl_usd=%.4f",
                                         asset, exit_price or 0, pnl_usd or 0)
                                break

                        # Fallback: fetch_my_trades for exit price
                        if exit_price is None:
                            trades = self.executor.client.exchange.fetch_my_trades(ccxt_sym, limit=5)
                            if trades:
                                exit_price = float(trades[-1].get("price") or 0) or None
                    except Exception:
                        log.warning("RECONCILE: Could not fetch exit data for %s", asset, exc_info=True)

                    # Final fallback: compute PnL from entry vs exit price
                    if pnl_usd is None and exit_price and row.get("entry_price") and row.get("size_usd"):
                        entry_p = float(row["entry_price"])
                        size = float(row["size_usd"])
                        direction = row.get("direction", "LONG")
                        raw = (exit_price - entry_p) / entry_p if direction == "LONG" else (entry_p - exit_price) / entry_p
                        pnl_usd = round(size * raw, 2)
                        log.info("RECONCILE: %s computed pnl=%.2f from entry=%.4f exit=%.4f",
                                 asset, pnl_usd, entry_p, exit_price)

                    log.info("RECONCILE: Closing %s id=%d exit_price=%s pnl_usd=%s", asset, trade_id, exit_price, pnl_usd)
                    self.pg.close_trade_by_id(trade_id, "EXCHANGE_CLOSED", exit_price=exit_price, pnl_usd=pnl_usd)
                    # Also remove from in-memory positions
                    keys_to_remove = [k for k, p in self.positions.items() if p.asset == asset]
                    for k in keys_to_remove:
                        del self.positions[k]
                        log.info("RECONCILE: Removed %s from in-memory positions", asset)
        except Exception:
            log.warning("RECONCILE: Failed to reconcile DB with exchange", exc_info=True)

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

        # Postgres
        if pos.trade_id is not None:
            self.pg.log_trade_close(pos.trade_id, price, exit_reason, pnl_usd, pnl_pct * 100)

        # iMessage alert
        sign = "+" if pnl_usd >= 0 else ""
        msg = (
            f"\U0001f534 CLOSE {pos.asset} {trade.direction} @ ${price:.2f} | "
            f"{sign}${pnl_usd:.0f} ({sign}{pnl_pct * 100:.1f}%) | reason: {exit_reason}"
        )
        send_imsg(msg)

        self._tick_closed += 1

        # V2.5: Feed to adaptive sizer
        self.adaptive_sizer.record_trade(trade.direction, pnl_pct)

        # Report to strategy's adaptive manager
        if pos.strategy == "squeeze_v15":
            self.squeeze[pos.asset].record_trade(trade.direction, pnl_pct * 100, exit_reason)

        if self.bot_id is not None:
            self.pg.log_decision(self.bot_id, pos.asset, pos.strategy, "CLOSE", f"{exit_reason} pnl=${pnl_usd:.2f}")

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
            if self.bot_id is not None:
                self.pg.log_decision(self.bot_id, asset, strat_name, "SKIP", "DD limit breached")
            return

        # V2.5: Regime detection for score adjustment
        regime_state = self.regime_detector.detect(df, i)
        regime_adj = None  # type: ignore
        if self.regime_detector.enabled:
            regime_adj = lambda d: self.regime_detector.get_score_adjustment(regime_state, d)

        # Generate signal
        sig = strat.generate_signal(df, i, regime_score_adj=regime_adj)
        if sig is None:
            return

        direction = sig["action"]

        # V2.4: Correlation guard — limit concurrent same-direction positions
        if getattr(strat, 'use_correlation_guard', False):
            max_same = getattr(strat, 'max_same_direction', 3)
            same_dir_count = sum(
                1 for pos in self.positions.values()
                if pos.trade.direction == direction
            )
            if same_dir_count >= max_same:
                if self.bot_id is not None:
                    self.pg.log_decision(
                        self.bot_id, asset, strat_name, "SKIP",
                        f"correlation_guard: {same_dir_count} {direction} already open",
                    )
                return
        stop = sig["stop"]
        target = sig["target"]
        leverage = sig.get("leverage", 1.0)
        signal_name = sig.get("signal", strat_name)

        # Guard: reject signals with invalid price, stop, or target
        if not price or not stop or not target or price <= 0 or stop <= 0 or target <= 0:
            log.warning("SKIP %s: invalid signal values price=%.6f stop=%.6f target=%.6f", asset, price, stop, target)
            return

        # Compound sizing: equity / n_assets, with floor and cap
        try:
            total_equity = self.pg.get_total_equity(self.bot_id) if self.bot_id is not None else 0.0
            if total_equity > 0:
                slot_size = total_equity / len(ASSETS)
                slot_size = max(slot_size, CAPITAL_PER_ASSET)           # floor: never below initial
                slot_size = min(slot_size, CAPITAL_PER_ASSET * 10)      # cap: max 10x initial per slot
            else:
                slot_size = CAPITAL_PER_ASSET                            # fallback to fixed

            # Tiered sizing by signal score (V2.6 backtest: MaxDD 57% → 39%, +3.5% PnL)
            # Score: 1–2 → 1x, 3–3.5 → 1.5x, 4–4.5 → 2x, 5+ → 3x
            score = float(sig.get("score", 1.0))
            if score >= 5.0:
                score_mult = 3.0
            elif score >= 4.0:
                score_mult = 2.0
            elif score >= 3.0:
                score_mult = 1.5
            else:
                score_mult = 1.0
            slot_size = min(slot_size * score_mult, CAPITAL_PER_ASSET * 10)
            log.info("Compound sizing: total_equity=%.2f slot_size=%.2f score=%.1f tier=%.1fx",
                     total_equity or 0, slot_size, score, score_mult)
        except Exception:
            slot_size = CAPITAL_PER_ASSET
            log.warning("Compound sizing failed — using fixed CAPITAL_PER_ASSET")

        # Size position — apply V2.5 multipliers
        base_capital = slot_size
        adaptive_mult = self.adaptive_sizer.get_multiplier(direction)
        regime_dir_mult = regime_state.direction_multiplier(direction)
        vol_mult = regime_state.volatility_multiplier
        combined_mult = adaptive_mult * regime_dir_mult * vol_mult
        # Clamp total multiplier: 0.2x - 2.5x
        combined_mult = max(0.2, min(2.5, combined_mult))
        adjusted_capital = base_capital * combined_mult

        size_usd = size_position(sig, price, self.risk_mgr, adjusted_capital)
        if size_usd <= 0:
            if self.bot_id is not None:
                self.pg.log_decision(self.bot_id, asset, strat_name, "SKIP", f"Size=0 for {signal_name}")
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

        import time as _time

        # Place order with retry (up to 3 attempts, 2s apart)
        order_id = ""
        MAX_ORDER_ATTEMPTS = 3
        for attempt in range(1, MAX_ORDER_ATTEMPTS + 1):
            try:
                order = self.executor.place_order(
                    symbol=asset,
                    side=order_side,
                    size=size_contracts,
                    order_type="mkt",
                )
                order_id = order.get("id", "")
                log.info("Order placed (attempt %d): %s %s id=%s", attempt, asset, direction, order_id)
                break
            except Exception as e:
                log.warning("Order attempt %d/%d failed for %s %s: %s", attempt, MAX_ORDER_ATTEMPTS, asset, strat_name, e)
                if attempt < MAX_ORDER_ATTEMPTS:
                    _time.sleep(2)
                else:
                    err_msg = str(e)
                    log.error("All %d order attempts failed for %s %s: %s", MAX_ORDER_ATTEMPTS, asset, strat_name, err_msg)
                    if self.bot_id is not None:
                        self.pg.log_decision(self.bot_id, asset, strat_name, "ERROR", f"Order failed after {MAX_ORDER_ATTEMPTS} attempts for {signal_name}")
                    send_imsg(f"⚠️ Order failed: {asset} {direction}\nSignal: {signal_name}\nReason: {err_msg}\nAfter {MAX_ORDER_ATTEMPTS} retries — not entered.")
                    return

        # Confirm fill — retry up to 3 times with 1s delay
        actual_fill = price  # fallback
        MAX_FILL_ATTEMPTS = 3
        fill_confirmed = False
        ccxt_sym = CCXT_SYMBOLS.get(asset, asset + "/USD:USD")
        for attempt in range(1, MAX_FILL_ATTEMPTS + 1):
            try:
                _time.sleep(1)
                order_status = self.executor.client.exchange.fetch_order(order_id, ccxt_sym)
                if order_status.get("status") == "closed":
                    actual_fill = float(order_status.get("average") or order_status.get("price") or price)
                    log.info("Fill confirmed (attempt %d): %s %s @ $%.4f (signal $%.4f)", attempt, asset, direction, actual_fill, price)
                    fill_confirmed = True
                    break
                elif order_status.get("status") == "canceled":
                    log.warning("Order cancelled by exchange for %s %s — not logging to DB", asset, strat_name)
                    send_imsg(f"⚠️ Order cancelled by exchange: {asset} {direction}\nSignal: {signal_name}\nKraken rejected the order.")
                    return
                else:
                    log.info("Fill attempt %d/%d: order status=%s, retrying...", attempt, MAX_FILL_ATTEMPTS, order_status.get("status"))
            except Exception as e:
                log.warning("Fill confirm attempt %d/%d failed for %s: %s", attempt, MAX_FILL_ATTEMPTS, asset, e)

        if not fill_confirmed:
            log.warning("Fill unconfirmed for %s after %d attempts — logging with signal price $%.4f", asset, MAX_FILL_ATTEMPTS, price)

        # Order succeeded — log to DB as OPEN with actual fill price
        trade.entry_price = actual_fill
        fill_status = "✅ confirmed" if fill_confirmed else "⚠️ unconfirmed (signal price used)"
        send_imsg(
            f"✅ Trade opened: {asset} {direction}\n"
            f"Signal: {signal_name}\n"
            f"Entry: ${actual_fill:.4f} | Stop: ${stop:.4f} | Target: ${target:.4f}\n"
            f"Size: ${size_usd:.0f} | Fill: {fill_status}"
        )
        trade_id = None  # type: Optional[int]
        if self.bot_id is not None:
            trade_id = self.pg.log_trade_open(
                self.bot_id, asset, strat_name, direction, signal_name,
                actual_fill, stop, target, size_usd, leverage,
            )
            # Immediately confirm as OPEN (skip PENDING state entirely)
            if trade_id:
                self.pg.confirm_trade_open(trade_id, actual_fill)

        pos = LivePosition(
            trade_id=trade_id,
            asset=asset,
            strategy=strat_name,
            trade=trade,
            order_id=order_id,
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
        if self.bot_id is not None:
            self.pg.log_decision(self.bot_id, asset, strat_name, "OPEN", f"{signal_name} size=${size_usd:.0f}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    runner = LiveRunner()
    runner.run()
