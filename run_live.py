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

import numpy as np
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
from live.adaptive_sizer import AdaptiveSizer
from live.regime import RegimeDetector
from live.wick_guard import WickGuard
from live.entry_optimizer import EntryOptimizer

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
DUST_THRESHOLD_USD = 10.0  # auto-close positions worth less than this

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


# Error tracking for rate-limiting alerts
_error_counts: dict = {}  # type: dict[str, int]
_last_alert_time: dict = {}  # type: dict[str, float]
ALERT_COOLDOWN_SEC = 300  # Don't spam same error more than once per 5 min


def alert_error(category: str, message: str) -> None:
    """Send error alert to OpenClaw (Elio) for auto-fix. Rate-limited per category."""
    import time as _time
    now = _time.time()
    _error_counts[category] = _error_counts.get(category, 0) + 1
    last = _last_alert_time.get(category, 0)
    if now - last < ALERT_COOLDOWN_SEC:
        return  # Rate limited
    _last_alert_time[category] = now
    count = _error_counts[category]
    alert_text = f"crypto-live ERROR [{category}] (x{count}): {message}"
    log.error(alert_text)
    try:
        subprocess.run(
            ["openclaw", "system-event", "--text", alert_text, "--mode", "now"],
            timeout=10,
            capture_output=True,
        )
    except Exception:
        log.warning("Failed to send error alert to OpenClaw")
    # Also iMessage Dan on critical errors
    if count >= 3 or category in ("CRASH", "EXCHANGE_DOWN", "DB_DOWN"):
        send_imsg(f"⚠️ Bot error: {category} - {message}")


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
            cooldown=12,
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
            "strategies": ["candle_v2_3"],
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

        # V2.6: Wick guard, smart entries, regime sizing
        self.wick_guard = WickGuard(
            enabled=getattr(config, "USE_WICK_GUARD", False),
        )
        self.entry_optimizer = EntryOptimizer(
            enabled=getattr(config, "USE_SMART_ENTRIES", False),
            pullback_atr=getattr(config, "SMART_ENTRY_PULLBACK_ATR", 0.3),
            expiry_hours=getattr(config, "SMART_ENTRY_EXPIRY_HOURS", 1),
        )
        self.use_regime_sizing = getattr(config, "USE_REGIME_SIZING", False)

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

        # V2.6: Re-evaluate existing positions against new strategy params
        self._reevaluate_positions()

        # Wire BTC data to squeeze strategies for cross-asset features
        if "BTC" in self.candle_cache:
            for asset, strat in self.squeeze.items():
                if asset != "BTC":
                    strat.set_btc_data(self.candle_cache["BTC"])

        log.info("Initial data loaded. Entering main loop.")

        while not self._shutdown:
            try:
                # Fast check every minute: SL/TP/trailing stop + Kraken reconciliation
                self._fast_check()
                # Hourly: full candle analysis + signal generation
                self._tick()
            except Exception as e:
                log.exception("Error in main loop tick")
                alert_error("MAIN_LOOP", str(e)[:200])
            time.sleep(LOOP_INTERVAL_SEC)

        log.info("=== Shutdown complete ===")
        self.pg.close()

    def _handle_signal(self, signum, frame):
        log.info("Received signal %d, shutting down gracefully...", signum)
        self._shutdown = True

    def _sync_positions(self):
        """Reconcile Postgres positions with Kraken on startup."""
        try:
            exchange_positions = self.executor.get_positions()
        except Exception as e:
            log.exception("Failed to fetch exchange positions for sync")
            alert_error("EXCHANGE_DOWN", f"Cannot fetch positions: {str(e)[:150]}")
            return

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

    def _reconcile_exchange(self):
        """Check Kraken positions every cycle — close any bot positions that Kraken closed."""
        try:
            exchange_positions = self.executor.get_positions()
        except Exception:
            log.warning("Failed to fetch exchange positions for reconciliation")
            return

        exchange_symbols = set()
        for ep in exchange_positions:
            exchange_symbols.add(ep["symbol"])

        keys_to_close = []
        for key, pos in self.positions.items():
            ccxt_sym = CCXT_SYMBOLS.get(pos.asset.upper(), "")
            if ccxt_sym and ccxt_sym not in exchange_symbols:
                # Bot thinks position is open, but Kraken closed it (SL/TP hit on exchange)
                log.warning("RECONCILE: %s %s position gone from exchange — marking closed",
                            pos.asset, pos.strategy)
                price = pos.trade.entry_price  # best we have without exchange fill price
                if pos.trade_id is not None:
                    self.pg.log_trade_close(pos.trade_id, price, "EXCHANGE_CLOSED", 0.0, 0.0)
                keys_to_close.append(key)
                self._tick_closed += 1

        for k in keys_to_close:
            del self.positions[k]

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
                alert_error("CANDLE_FEED", f"{asset}: failed to load candles")

    def _reevaluate_positions(self):
        """V2.6: Re-evaluate all open positions against current strategy params.

        When new config is deployed, existing positions may have stale SL/TP/targets.
        This method recalculates them using current strategy parameters and ATR,
        then updates the exchange orders accordingly.
        """
        if not self.positions:
            log.info("REEVAL: No open positions to re-evaluate")
            return

        log.info("REEVAL: Re-evaluating %d open positions against V2.6 params...", len(self.positions))

        for key, pos in list(self.positions.items()):
            asset = pos.asset
            if asset not in self.candle_cache:
                continue

            df = self.candle_cache[asset]
            if len(df) < MIN_BARS:
                continue

            i = len(df) - 1
            strat = self.candle.get(asset)
            if strat is None:
                continue

            # Recompute indicators to get current ATR
            strat._compute_indicators(df, i)
            atr = strat._indicators["atr"][i]
            if np.isnan(atr) or atr <= 0:
                continue

            trade = pos.trade
            price = float(df.iloc[i]["close"])

            # Recalculate stop and target from current price using strategy params
            old_stop = trade.stop_price
            old_target = trade.target_price

            if trade.direction == "LONG":
                new_stop = trade.entry_price - atr * strat.stop_atr
                new_target = trade.entry_price + atr * strat.target_atr
                # Only tighten stops, never widen (protect existing gains)
                if old_stop > 0 and new_stop < old_stop:
                    new_stop = old_stop
            else:
                new_stop = trade.entry_price + atr * strat.stop_atr
                new_target = trade.entry_price - atr * strat.target_atr
                if old_stop > 0 and new_stop > old_stop:
                    new_stop = old_stop

            changed = False
            if abs(new_stop - old_stop) > atr * 0.01:
                trade.stop_price = new_stop
                changed = True
            if abs(new_target - old_target) > atr * 0.01:
                trade.target_price = new_target
                changed = True

            if changed:
                log.info(
                    "REEVAL: %s %s %s | stop %.2f->%.2f | target %.2f->%.2f",
                    asset, pos.strategy, trade.direction,
                    old_stop, trade.stop_price,
                    old_target, trade.target_price,
                )
                # Cancel old exchange SL/TP and place new ones
                self._cancel_sl_tp(pos)
                self._place_sl_tp(pos)

                # Update Postgres
                if pos.trade_id is not None:
                    self.pg.update_trade_levels(
                        pos.trade_id, trade.stop_price, trade.target_price
                    )
            else:
                log.info("REEVAL: %s %s — no change needed", asset, pos.strategy)

    def _fast_check(self):
        """Fast price check every minute — trailing stop, SL/TP, exchange reconciliation."""
        if not self.positions:
            return

        # Reconcile with Kraken
        self._reconcile_exchange()

        # Fetch current prices and check exits
        for asset in ASSETS:
            keys_to_close = []
            for key, pos in self.positions.items():
                if pos.asset != asset:
                    continue
                try:
                    ticker = self.executor.client.exchange.fetch_ticker(
                        CCXT_SYMBOLS.get(asset.upper(), "BTC/USD:USD")
                    )
                    price = ticker.get("last", 0.0)
                    if price <= 0:
                        continue
                except Exception:
                    continue

                trade = pos.trade

                # Update trailing stop if price moved in our favor
                if hasattr(trade, '_trail_active') and trade._trail_active:
                    # Trailing stop is managed by strategy check_exit
                    pass

                # Check strategy exit (handles trailing stop logic)
                strat = self.candle.get(asset)
                if strat:
                    exit_reason = None

                    # V2.6: TP executes immediately on tick
                    if trade.direction == "LONG":
                        if trade.target_price > 0 and price >= trade.target_price:
                            exit_reason = "TARGET"
                    else:
                        if trade.target_price > 0 and price <= trade.target_price:
                            exit_reason = "TARGET"

                    # V2.6: Wick-resistant stop — require 15m close beyond stop
                    if exit_reason is None:
                        pos_key_str = f"{asset}_{pos.strategy}"
                        if trade.direction == "LONG" and price <= trade.stop_price:
                            if self.wick_guard.should_trigger_stop(
                                pos_key_str, trade.direction, trade.stop_price, price
                            ):
                                exit_reason = "STOP"
                        elif trade.direction == "SHORT" and price >= trade.stop_price:
                            if self.wick_guard.should_trigger_stop(
                                pos_key_str, trade.direction, trade.stop_price, price
                            ):
                                exit_reason = "STOP"

                    # Dust check — close tiny positions not worth keeping
                    if trade.size_usd and trade.size_usd < DUST_THRESHOLD_USD:
                        exit_reason = "DUST"

                    if exit_reason:
                        log.info("FAST_CHECK: %s %s %s triggered %s at $%.4f",
                                 asset, trade.direction, pos.strategy, exit_reason, price)
                        self._close_position(key, pos, price, exit_reason)
                        keys_to_close.append(key)
                        self.wick_guard.clear(f"{asset}_{pos.strategy}")

            for k in keys_to_close:
                del self.positions[k]

        # V2.6: Check pending limit entries for fills
        if self.entry_optimizer.enabled:
            now = datetime.now(timezone.utc)
            # Expire old entries first
            expired = self.entry_optimizer.expire_all(now)
            for exp in expired:
                log.info("SMART_ENTRY: %s %s limit expired (unfilled)", exp.asset, exp.direction)
                # Cancel exchange limit order if placed
                if exp.order_id:
                    try:
                        self.executor.cancel_order(exp.order_id, exp.asset)
                    except Exception:
                        pass

            # Check fills for remaining pending entries
            for asset in ASSETS:
                for strat_name in ["candle_v2_3"]:
                    key = (asset, strat_name)
                    if key in self.positions:
                        continue
                    try:
                        ticker = self.executor.client.exchange.fetch_ticker(
                            CCXT_SYMBOLS.get(asset.upper(), "BTC/USD:USD")
                        )
                        price = ticker.get("last", 0.0)
                        high = ticker.get("high", price)
                        low = ticker.get("low", price)
                        if price <= 0:
                            continue
                    except Exception:
                        continue

                    filled = self.entry_optimizer.check_fill(
                        asset, strat_name, high, low, now
                    )
                    if filled:
                        log.info("SMART_ENTRY: %s %s filled at limit $%.4f",
                                 asset, filled.direction, filled.limit_price)
                        self._execute_entry(
                            asset, strat_name, filled.signal,
                            filled.limit_price, filled.direction,
                        )

    def _tick(self):
        """Hourly iteration: fetch new candle, run strategies, manage positions."""
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
                alert_error("CANDLE_FEED", f"{asset}: failed to fetch candles")

        # Wire BTC data for cross-asset
        if "BTC" in self.candle_cache:
            for asset, strat in self.squeeze.items():
                if asset != "BTC":
                    strat.set_btc_data(self.candle_cache["BTC"])

        # Reset tick counters
        self._tick_signals = 0
        self._tick_opened = 0
        self._tick_closed = 0

        # Reconcile with Kraken every cycle (Kraken = source of truth)
        self._reconcile_exchange()

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

            # Log equity to Postgres (with real unrealized P&L)
            if self.bot_id is not None:
                open_count = 0
                unrealized_pnl = 0.0
                for pos in self.positions.values():
                    if pos.asset == asset:
                        open_count += 1
                        trade = pos.trade
                        if trade.direction == "LONG":
                            pnl_pct = (price - trade.entry_price) / trade.entry_price if trade.entry_price > 0 else 0
                        else:
                            pnl_pct = (trade.entry_price - price) / trade.entry_price if trade.entry_price > 0 else 0
                        unrealized_pnl += pnl_pct * trade.size_usd * trade.leverage
                asset_equity = CAPITAL_PER_ASSET + unrealized_pnl
                self.pg.log_equity(self.bot_id, asset, asset_equity, unrealized_pnl, open_count)

        # Heartbeat
        if self.bot_id is not None:
            total_equity = 0.0
            for asset in ASSETS:
                asset_pnl = 0.0
                if asset in self.candle_cache and len(self.candle_cache[asset]) > 0:
                    price = float(self.candle_cache[asset].iloc[-1]["close"])
                    for pos in self.positions.values():
                        if pos.asset == asset:
                            trade = pos.trade
                            if trade.direction == "LONG":
                                pnl_pct = (price - trade.entry_price) / trade.entry_price if trade.entry_price > 0 else 0
                            else:
                                pnl_pct = (trade.entry_price - price) / trade.entry_price if trade.entry_price > 0 else 0
                            asset_pnl += pnl_pct * trade.size_usd * trade.leverage
                total_equity += CAPITAL_PER_ASSET + asset_pnl
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
            alert_error("ORDER_FAIL", f"Failed to close {pos.asset} {pos.strategy}")

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

        # Size position — apply V2.5 multipliers
        base_capital = CAPITAL_PER_ASSET
        adaptive_mult = self.adaptive_sizer.get_multiplier(direction)
        regime_dir_mult = regime_state.direction_multiplier(direction)
        vol_mult = regime_state.volatility_multiplier
        combined_mult = adaptive_mult * regime_dir_mult * vol_mult

        # V2.6: Regime sizing multiplier
        if self.use_regime_sizing:
            regime_size_mult = regime_state.regime_size_multiplier(direction)
            combined_mult *= regime_size_mult

        # Clamp total multiplier: 0.2x - 2.5x
        combined_mult = max(0.2, min(2.5, combined_mult))
        adjusted_capital = base_capital * combined_mult

        size_usd = size_position(sig, price, self.risk_mgr, adjusted_capital)
        if size_usd <= 0:
            if self.bot_id is not None:
                self.pg.log_decision(self.bot_id, asset, strat_name, "SKIP", f"Size=0 for {signal_name}")
            return

        # V2.6: Smart entries — place limit order at pullback instead of market
        if self.entry_optimizer.enabled:
            atr_val = sig.get("atr_at_entry", 0)
            if atr_val and atr_val > 0:
                limit_price = self.entry_optimizer.compute_limit_price(direction, price, atr_val)
                sig["_size_usd"] = size_usd
                sig["_leverage"] = leverage
                sig["_stop"] = stop
                sig["_target"] = target
                sig["_signal_name"] = signal_name
                sig["_regime_mult"] = combined_mult

                pending = self.entry_optimizer.create_pending(
                    asset, strat_name, direction, limit_price, sig
                )

                # Place limit order on exchange
                order_side = "buy" if direction == "LONG" else "sell"
                size_contracts = size_usd / limit_price if limit_price > 0 else 0
                try:
                    order = self.executor.place_limit_order(
                        symbol=asset,
                        side=order_side,
                        size=size_contracts,
                        price=limit_price,
                    )
                    pending.order_id = order.get("id", "")
                    log.info(
                        "SMART_ENTRY: %s %s %s limit @ $%.4f (pullback from $%.4f)",
                        asset, direction, strat_name, limit_price, price,
                    )
                except Exception:
                    log.exception("Failed to place limit order for %s", asset)
                    self.entry_optimizer.cancel_pending(asset, strat_name)

                if self.bot_id is not None:
                    self.pg.log_decision(
                        self.bot_id, asset, strat_name, "LIMIT",
                        f"{signal_name} limit=${limit_price:.2f}",
                    )
                return

        # Market order entry (fallback or when smart entries disabled)
        self._execute_entry(asset, strat_name, sig, price, direction)

    def _execute_entry(
        self, asset: str, strat_name: str, sig: dict,
        entry_price: float, direction: str,
    ):
        """Execute an entry (market or filled limit) — shared by _run_strategy and smart entry fill."""
        key = (asset, strat_name)
        if key in self.positions:
            return

        stop = sig.get("_stop", sig.get("stop", 0))
        target = sig.get("_target", sig.get("target", 0))
        leverage = sig.get("_leverage", sig.get("leverage", 1.0))
        signal_name = sig.get("_signal_name", sig.get("signal", strat_name))
        size_usd = sig.get("_size_usd", 0)

        # If no pre-computed size, compute now
        if size_usd <= 0:
            size_usd = size_position(sig, entry_price, self.risk_mgr, CAPITAL_PER_ASSET)
        if size_usd <= 0:
            return

        trade = Trade(
            entry_time=pd.Timestamp.now(tz="UTC"),
            entry_price=entry_price,
            direction=direction,
            signal=signal_name,
            stop_price=stop,
            target_price=target,
            size_usd=size_usd,
            leverage=leverage,
        )

        order_side = "buy" if direction == "LONG" else "sell"
        size_contracts = size_usd / entry_price if entry_price > 0 else 0

        log.info(
            "OPEN %s %s %s | signal=%s price=%.2f stop=%.2f target=%.2f size=$%.0f lev=%.1fx",
            asset, strat_name, direction, signal_name,
            entry_price, stop, target, size_usd, leverage,
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
            alert_error("ORDER_FAIL", f"Failed to place {asset} {strat_name} order")
            if self.bot_id is not None:
                self.pg.log_decision(self.bot_id, asset, strat_name, "ERROR", f"Order failed for {signal_name}")
            return

        trade_id = None  # type: Optional[int]
        if self.bot_id is not None:
            trade_id = self.pg.log_trade_open(
                self.bot_id, asset, strat_name, direction, signal_name,
                entry_price, stop, target, size_usd, leverage,
            )

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
            f"\U0001f7e2 OPEN {asset} {direction} @ ${entry_price:.2f} | "
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
